import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from google.api_core.exceptions import ServiceUnavailable
from moto import mock_aws

from services.lake_to_bigquery import handler as intake
from services.lake_to_bigquery.load_jobs import append_config, load_attempt
from services.lake_to_bigquery.loader import load_manifest
from services.lake_to_bigquery.parquet_input import import_parquet
from services.lake_to_bigquery.tests.bigquery_double import STAGING, BigQueryDouble, decoded_parquet
from services.stream_exporter.handler import export
from services.stream_exporter.lake_io import PENDING, ROOT, Archive, DataError, canonical, digest, entry
from services.stream_exporter.tests.samples import ARN, BUCKET, OWNER, incidents, reading, sample, stock_events

FIXTURE = Path(__file__).with_name("firehose-syn-9001.parquet")
PREFIX = "incoming/telemetry/"


@pytest.fixture
def bridge():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield Archive(s3, BUCKET, OWNER), BigQueryDouble()


def parquet_bytes(rows):
    output = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), output)
    return output.getvalue()


def pending(archive):
    return [o["Key"] for o in archive.page(PENDING, "", 100)[0]]


def manifest(archive, rows):
    batch = archive.publish("telemetry", [entry(row["messageId"], "local-input", row) for row in rows])
    return f"{PENDING}telemetry/{batch}.json"


def test_two_handlers_with_real_parquet_and_split_replay(bridge, monkeypatch):
    archive, bq = bridge
    fixture = FIXTURE.read_bytes()
    assert digest(fixture) == "5933d192461941b94e72477d50e775caa965c71bcd4f7cd33c0a75356170e9c2"
    assert len(fixture) == 3576
    archive.client.put_object(Bucket=BUCKET, Key=PREFIX + "first.parquet", Body=fixture)
    records = [reading(1, sample()), *incidents(), *stock_events()]
    assert not export(records, archive, ARN)["batchItemFailures"]
    monkeypatch.setattr(intake, "configured_archive", lambda: archive)
    monkeypatch.setattr(intake, "bigquery_client", lambda: bq)
    monkeypatch.setenv("AF_PARQUET_PREFIX", PREFIX)
    monkeypatch.setenv("AF_BQ_STAGING_DATASET", STAGING)
    ctx = SimpleNamespace(get_remaining_time_in_millis=lambda: 60000)
    assert not intake.handler({}, ctx)["pending"]["failures"]
    target = bq.project + ".af_lake.telemetry"
    [actual] = bq.tables[target]
    first_id = "f1cc09d4-f922-57fd-a9f7-bc7a028e801b"
    assert actual["messageId"] == first_id and actual["machineId"] == "SYN-9001"
    assert datetime.fromisoformat(actual["timestamp"]) == datetime.fromisoformat("2026-11-15T06:58:05.139Z")
    assert actual["metrics"]["pressureBar"] is None and actual["metrics"]["loadTons"] == 0
    assert actual["location"]["zoneId"] == "SYNTHETIC" and actual["context"]["ambientTempC"] == 23.25
    assert bq.parquet_uploads == [fixture]
    for rec in records:
        assert not export([rec], archive, ARN)["batchItemFailures"]
    intake.handler({}, ctx)
    assert bq.parquet_uploads == [fixture] and len(bq.tables[target]) == 1

    [physical] = decoded_parquet(fixture)
    second_id = sample()["messageId"]
    second = {**physical, "messageid": second_id, "context": None}
    source_schema = pq.ParquetFile(FIXTURE, coerce_int96_timestamp_unit="us").schema_arrow
    buf = io.BytesIO()
    pq.write_table(
        pa.Table.from_pylist([second, physical, second], schema=source_schema),
        buf,
        use_deprecated_int96_timestamps=True,
    )
    archive.client.put_object(Bucket=BUCKET, Key=PREFIX + "overlap.parquet", Body=buf.getvalue())
    intake.handler({}, ctx)
    intake.handler({}, ctx)
    assert {r["messageId"] for r in bq.tables[target]} == {first_id, second_id}
    assert len(bq.tables[target]) == 2 and bq.parquet_uploads == [fixture, buf.getvalue()]
    [inc] = bq.tables[bq.project + ".af_lake.incidents"]
    [part] = bq.tables[bq.project + ".af_lake.parts_movements"]
    assert inc["incidentId"] == "INC-local" and inc["downtimeMinutes"] == 60
    assert inc["partsReplaced"] == [{"partId": "P-1", "warehouse": "W-1", "qty": 1}]
    assert part["qty"] == 1 and part["reason"] == "CORRECTIVE"
    assert not pending(archive) and "PRIVATE-FIXTURE" not in json.dumps(bq.tables)


@pytest.mark.parametrize("outcome", ["submit_timeout", "result_timeout", "conflict"])
def test_uncertain_submission_resumes_the_exact_same_job(bridge, outcome):
    archive, bq = bridge
    key = manifest(archive, [sample()])
    bq.next_outcome = outcome
    if outcome == "submit_timeout":
        with pytest.raises(ServiceUnavailable):
            load_manifest(archive, bq, key)
        assert pending(archive) == [key]
    receipt = load_manifest(archive, bq, key)
    assert receipt["job"] == bq.submissions[0] and len(bq.submissions) == 1
    assert len(bq.tables[bq.project + ".af_lake.telemetry"]) == 1
    assert load_manifest(archive, bq, key) == receipt


def test_running_job_does_not_advance_checkpoint(bridge):
    archive, bq = bridge
    key = manifest(archive, [sample()])
    bq.next_outcome = "running"
    with pytest.raises(TimeoutError):
        load_manifest(archive, bq, key)
    assert pending(archive) == [key]
    [job] = bq.jobs.values()
    job.state = "DONE"
    job.result.side_effect = None
    assert load_manifest(archive, bq, key)["loadedRows"] == 1
    assert len(bq.submissions) == 1


def test_failed_atomic_job_can_retry_but_invalid_schema_stays_pending(bridge):
    archive, bq = bridge
    first = manifest(archive, [sample()])
    bq.next_outcome = "backendError"
    receipt = load_manifest(archive, bq, first)
    assert receipt["job"].endswith("_1") and len(bq.tables[bq.project + ".af_lake.telemetry"]) == 1
    second = manifest(archive, [sample("telemetry.sensor-null.json")])
    bq.next_outcome = "invalid"
    with pytest.raises(DataError, match="terminal"):
        load_manifest(archive, bq, second)
    assert pending(archive) == [second] and len(bq.submissions) == 3


def test_three_confirmed_transient_failures_resume_on_next_invocation(bridge, monkeypatch):
    archive, bq = bridge
    key = manifest(archive, [sample()])
    original = bq.load_table_from_file

    def three_failures(*args, **kwargs):
        if len(bq.submissions) < 3:
            bq.next_outcome = "backendError"
        return original(*args, **kwargs)

    monkeypatch.setattr(bq, "load_table_from_file", three_failures)
    with pytest.raises(DataError, match="remains pending"):
        load_manifest(archive, bq, key)
    assert pending(archive) == [key] and not bq.tables
    receipt = load_manifest(archive, bq, key)
    assert receipt["job"].endswith("_3") and len(bq.submissions) == 4
    assert len(bq.tables[bq.project + ".af_lake.telemetry"]) == 1 and pending(archive) == []


def test_empty_optional_context_is_the_same_fact_as_omitted_context(bridge):
    archive, bq = bridge
    raw = sample()
    raw.pop("context", None)
    load_manifest(archive, bq, manifest(archive, [raw]))
    receipt = load_manifest(archive, bq, manifest(archive, [{**raw, "context": {}}]))
    assert receipt["duplicateOrDelegatedRows"] == 1 and len(bq.submissions) == 1


def test_concurrent_overlapping_batches_assign_each_fact_one_owner(bridge, monkeypatch):
    archive, bq = bridge
    a, b = sample(), sample("telemetry.sensor-null.json")
    large = manifest(archive, [a, b])
    small = manifest(archive, [a])
    original = archive.put_new
    entered = False

    def interleave(key, *args):
        nonlocal entered
        if "/facts/" in key and not entered:
            entered = True
            # Second worker enters between the first's read and its conditional claim.
            assert load_manifest(archive, bq, small)["loadedRows"] == 1
        return original(key, *args)

    monkeypatch.setattr(archive, "put_new", interleave)
    receipt = load_manifest(archive, bq, large)
    assert receipt["loadedRows"] == 1 and receipt["duplicateOrDelegatedRows"] == 1
    assert len(bq.tables[bq.project + ".af_lake.telemetry"]) == 2


def test_conflicting_final_revision_or_message_preserves_original(bridge):
    archive, bq = bridge
    raw = sample()
    first = manifest(archive, [raw])
    load_manifest(archive, bq, first)
    conflict = manifest(archive, [{**raw, "status": "STOPPED"}])
    with pytest.raises(DataError, match="differs"):
        load_manifest(archive, bq, conflict)
    assert pending(archive) == [conflict] and len(bq.submissions) == 1
    rec = incidents()[-1]
    assert not export([rec], archive, ARN)["batchItemFailures"]
    inc_key = next(k for k in pending(archive) if "/incidents/" in k)
    load_manifest(archive, bq, inc_key)
    rec["eventID"] = "local-later"
    rec["dynamodb"]["NewImage"]["downtimeMinutes"] = {"N": "61"}
    assert not export([rec], archive, ARN)["batchItemFailures"]
    changed = next(k for k in pending(archive) if "/incidents/" in k)
    with pytest.raises(DataError, match="differs"):
        load_manifest(archive, bq, changed)
    assert len(bq.tables[bq.project + ".af_lake.incidents"]) == 1


def test_corruption_and_bad_parquet_are_not_acknowledged(bridge):
    archive, bq = bridge
    key = manifest(archive, [sample()])
    bad = {"batch": "f" * 64, "kind": "telemetry", "rows": 1}
    archive.client.put_object(Bucket=BUCKET, Key=key, Body=canonical(bad))
    archive.client.put_object(
        Bucket=BUCKET,
        Key="incoming/test.parquet",
        Body=parquet_bytes([{"machineId": "SYN-0001", "arbitrary": "PRIVATE-FIXTURE"}]),
    )
    result = intake.run(archive, bq, parquet_prefix="incoming/", staging_dataset=STAGING)
    assert all(len(v["failures"]) == 1 for v in result.values())
    assert pending(archive) == [key] and len(bq.parquet_uploads) == 1 and not bq.tables


def test_missing_completion_after_commit_reuses_job(bridge, monkeypatch):
    archive, bq = bridge
    key = manifest(archive, [sample()])
    put = archive.put_new

    def lose_checkpoint(path, *args):
        if "/done/" in path:
            raise OSError("local interrupted checkpoint")
        return put(path, *args)

    monkeypatch.setattr(archive, "put_new", lose_checkpoint)
    with pytest.raises(OSError):
        load_manifest(archive, bq, key)
    assert pending(archive) == [key]
    monkeypatch.setattr(archive, "put_new", put)
    assert load_manifest(archive, bq, key)["loadedRows"] == 1
    assert len(bq.submissions) == 1


def test_page_cursor_wraps_and_orphan_replay_is_idempotent(bridge):
    archive, bq = bridge
    k1 = manifest(archive, [sample()])
    k2 = manifest(archive, [sample("telemetry.sensor-null.json")])
    earliest = min(k1, k2)
    good = archive.read(earliest)
    archive.client.put_object(Bucket=BUCKET, Key=earliest, Body=b"{}")  # earlier poisoned pointer
    assert intake.run(archive, bq, limit=1)["pending"]["failures"]
    assert not intake.run(archive, bq, limit=1)["pending"]["failures"]
    assert len(bq.submissions) == 1 and pending(archive) == [earliest]
    archive.client.put_object(Bucket=BUCKET, Key=earliest, Body=good)
    assert not intake.run(archive, bq, limit=1)["pending"]["failures"]
    archive.requeue("telemetry", json.loads(good)["batch"])
    intake.run(archive, bq)
    assert len(bq.submissions) == 2 and pending(archive) == []


def test_old_ambiguous_intent_needs_reconciliation():
    bq = BigQueryDouble()
    now = datetime.now(UTC)
    with pytest.raises(DataError, match="reconciliation"):
        load_attempt(
            bq,
            bq.project + ".af_lake.telemetry",
            canonical(sample()) + b"\n",
            append_config(),
            "af_lake_local",
            now - timedelta(days=180),
            now=now,
            timeout=2,
        )
    assert not bq.submissions


def test_schema_and_checksum_checked_before_submission(bridge):
    archive, bq = bridge
    batch = archive.publish("incidents", [entry("case", "local", {"incidentId": "INC-1", "taskToken": "private"})])
    [key] = pending(archive)
    with pytest.raises(DataError, match="columns"):
        load_manifest(archive, bq, key)
    archive.client.put_object(Bucket=BUCKET, Key=f"{ROOT}data/incidents/{batch}.ndjson.gz", Body=b"bad gzip")
    assert intake.run(archive, bq)["pending"]["failures"]
    assert pending(archive) == [key] and not bq.submissions


def put_source(archive, data):
    key = PREFIX + "test.parquet"
    response = archive.client.put_object(Bucket=BUCKET, Key=key, Body=data)
    return {"Key": key, "ETag": response["ETag"]}


def test_parquet_lost_response_and_checkpoint_recover(bridge, monkeypatch):
    archive, bq = bridge
    fixture = FIXTURE.read_bytes()
    source = put_source(archive, fixture)
    bq.next_outcome = "submit_timeout"
    clock = datetime(2026, 11, 15, tzinfo=UTC)
    with pytest.raises(ServiceUnavailable):
        import_parquet(archive, bq, source, STAGING, now=clock)
    assert not pending(archive) and bq.parquet_uploads == [fixture]
    put = archive.put_new

    def interrupt(key, *args):
        if "/parquet/" in key:
            raise OSError("local interrupted source receipt")
        return put(key, *args)

    monkeypatch.setattr(archive, "put_new", interrupt)
    with pytest.raises(OSError):
        import_parquet(archive, bq, source, STAGING, now=clock)
    [pending_key] = pending(archive)
    assert load_manifest(archive, bq, pending_key)["loadedRows"] == 1
    assert bq.parquet_uploads == [fixture]
    monkeypatch.setattr(archive, "put_new", put)
    bq.stages.clear()  # BigQuery expired yesterday's temporary table.
    receipt = import_parquet(archive, bq, source, STAGING, now=clock + timedelta(days=1))
    assert receipt["rows"] == 1 and receipt["sourceSha256"] == digest(fixture)
    assert bq.parquet_uploads == [fixture, fixture]
    assert load_manifest(archive, bq, pending_key)["loadedRows"] == 1  # original final receipt
    assert len(bq.tables[bq.project + ".af_lake.telemetry"]) == 1


@pytest.mark.parametrize("dataset", [None, "af_lake", "af_ml", "af_out", "x.y"])
def test_parquet_requires_explicit_isolated_staging(bridge, dataset):
    archive, bq = bridge
    source = put_source(archive, FIXTURE.read_bytes())
    with pytest.raises(DataError, match="dedicated"):
        import_parquet(archive, bq, source, dataset)
    assert not bq.submissions and not pending(archive)


@pytest.mark.parametrize("location,ttl", [("EU", 86_400_000), ("US", None)])
def test_staging_location_and_expiration(bridge, location, ttl):
    archive, bq = bridge
    source = put_source(archive, FIXTURE.read_bytes())
    bq.dataset.location, bq.dataset.default_table_expiration_ms = location, ttl
    with pytest.raises(DataError, match="expiration"):
        import_parquet(archive, bq, source, STAGING)
    assert not bq.submissions


def test_partial_stage_count_and_wrong_timestamp_type_never_publish(bridge, monkeypatch):
    archive, bq = bridge
    source = put_source(archive, FIXTURE.read_bytes())
    original = bq.list_rows
    monkeypatch.setattr(bq, "list_rows", lambda *args, **kwargs: ())
    with pytest.raises(DataError, match="staging row count"):
        import_parquet(archive, bq, source, STAGING)
    assert not pending(archive)
    monkeypatch.setattr(bq, "list_rows", original)
    [table] = bq.stages.values()
    table[0]["timestamp"] = table[0]["timestamp"].replace(tzinfo=None)
    with pytest.raises(DataError, match="TIMESTAMP"):
        import_parquet(archive, bq, source, STAGING)
    assert len(bq.parquet_uploads) == 1 and not pending(archive)


def test_existing_target_rows_and_durable_selection_after_lost_append(bridge):
    archive, bq = bridge
    a, b = sample(), sample("telemetry.sensor-null.json")
    target = bq.project + ".af_lake.telemetry"
    unrelated = {**a, "messageId": "2cf0bc44-9fa1-4843-8f93-5b673fccce62"}
    bq.tables[target] = [a.copy(), unrelated.copy()]
    key = manifest(archive, [a, b])
    bq.next_outcome = "submit_timeout"
    with pytest.raises(ServiceUnavailable):
        load_manifest(archive, bq, key)
    receipt = load_manifest(archive, bq, key)
    assert receipt["loadedRows"] == receipt["alreadyPresentRows"] == 1
    assert len(bq.queries) == len(bq.submissions) == 1
    assert len(bq.tables[target]) == 3 and bq.tables[target][:2] == [a, unrelated]
    assert {row["messageId"] for row in bq.tables[target]} == {a["messageId"], b["messageId"], unrelated["messageId"]}


def test_conflicting_preexisting_message_blocks_the_batch(bridge):
    archive, bq = bridge
    raw = sample()
    existing = {**raw, "status": "STOPPED"}
    target = bq.project + ".af_lake.telemetry"
    bq.tables[target] = [existing]
    key = manifest(archive, [raw])
    with pytest.raises(DataError, match="existing telemetry conflicts"):
        load_manifest(archive, bq, key)
    assert bq.tables[target] == [existing] and pending(archive) == [key] and not bq.submissions


def test_conflicting_rows_within_one_parquet_publish_nothing(bridge):
    archive, bq = bridge
    [row] = decoded_parquet(FIXTURE.read_bytes())
    source = put_source(archive, parquet_bytes([row, {**row, "status": "STOPPED"}]))
    with pytest.raises(DataError, match="conflicting revisions"):
        import_parquet(archive, bq, source, STAGING)
    assert not pending(archive) and not bq.tables


def test_entrypoint_uses_only_the_configured_aws_federation_layer(monkeypatch):
    intake.bigquery_client.cache_clear()
    monkeypatch.setenv("GCP_PROJECT_ID", "arquifusion")
    federated = Mock()
    connect = Mock()
    monkeypatch.setattr(intake.aws.Credentials, "from_file", federated)
    monkeypatch.setattr(intake.bigquery, "Client", connect)
    for invalid in ("", "personal-credentials.json"):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", invalid)
        with pytest.raises(DataError, match="federation layer"):
            intake.bigquery_client()
    federated.assert_not_called()
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/opt/arquifusion/gcp-wif.json")
    for project in ("", "other-project"):
        monkeypatch.setenv("GCP_PROJECT_ID", project)
        with pytest.raises(DataError, match="project"):
            intake.bigquery_client()
    federated.assert_not_called()
    connect.assert_not_called()
    monkeypatch.setenv("GCP_PROJECT_ID", "arquifusion")
    try:
        assert intake.bigquery_client() is connect.return_value
        federated.assert_called_once_with(
            "/opt/arquifusion/gcp-wif.json", scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        connect.assert_called_once_with(project="arquifusion", location="US", credentials=federated.return_value)
    finally:
        intake.bigquery_client.cache_clear()
