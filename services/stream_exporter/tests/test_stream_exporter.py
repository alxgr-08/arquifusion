import gzip
import json

import boto3
import pytest
from moto import mock_aws

from services.stream_exporter.handler import export, handler
from services.stream_exporter.lake_io import PENDING, ROOT, Archive, configured_archive
from services.stream_exporter.projection import project
from services.stream_exporter.tests.samples import (
    ARN,
    BUCKET,
    OWNER,
    TS,
    incidents,
    reading,
    record,
    sample,
    stock_events,
)


@pytest.fixture
def archive():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_tagging(
            Bucket=BUCKET,
            Tagging={
                "TagSet": [
                    {"Key": "project", "Value": "arquifusion"},
                    {"Key": "env", "Value": "dev"},
                    {"Key": "purpose", "Value": "telemetry-lake"},
                    {"Key": "owner", "Value": "rol1"},
                ]
            },
        )
        configured_archive.cache_clear()
        yield Archive(client, BUCKET, OWNER)
        configured_archive.cache_clear()


def keys(archive, prefix):
    return [obj["Key"] for obj in archive.page(prefix, "", 100)[0]]


def test_real_entrypoint_groups_contract_rows_and_replay(archive, monkeypatch):
    monkeypatch.setenv("AF_SOURCE_STREAM_ARN", ARN)
    monkeypatch.setenv("AF_LAKE_ACCOUNT_ID", OWNER)
    monkeypatch.setenv("AF_LAKE_BUCKET", BUCKET)
    good, missing_sensor = sample(), sample("telemetry.sensor-null.json")
    records = [reading(1, good), reading(2, missing_sensor), *incidents(), *stock_events()]
    assert handler({"Records": records}, None) == {"batchItemFailures": []}
    original = keys(archive, ROOT)
    assert handler({"Records": records}, None) == {"batchItemFailures": []}
    assert keys(archive, ROOT) == original
    assert len(keys(archive, PENDING)) == 2
    histories = keys(archive, ROOT + "data/incident_history/")
    snapshots = [json.loads(line)["row"] for line in gzip.decompress(archive.read(histories[0])).splitlines()]
    assert {s["state"] for s in snapshots} == {"OPEN", "ASSIGNED", "RESOLVED"}
    assert b"PRIVATE-FIXTURE" not in gzip.decompress(archive.read(histories[0]))
    assert not any("telemetry" in key or "reading" in key for key in original)


def test_deployed_and_full_readings_are_ignored_even_without_images():
    raw = sample()
    reduced = {
        "messageId": raw["messageId"],
        "metrics": raw["metrics"],
        "status": raw["status"],
        "healthScore": 90,
        "severity": "OK",
        "expiresAt": 1870000000,
        "taskToken": "PRIVATE-FIXTURE",
    }
    assert project(record(8, "MACHINE#EXC-0042", f"READING#{TS}", reduced), ARN) == []
    full = reading(9, raw)
    assert project(full, ARN) == []
    del full["dynamodb"]["NewImage"]
    assert project(full, ARN) == []


def test_wait_token_and_failed_close_do_not_create_new_facts():
    before = {
        "machineId": "SYN-0001",
        "incidentId": "INC-local",
        "operationId": "OP-TEST",
        "createdAt": TS,
        "state": "ASSIGNED",
    }
    pk, sk = "MACHINE#SYN-0001", f"INCIDENT#{TS}#INC-local"
    assert not project(record(1, pk, sk, {**before, "taskToken": "PRIVATE-FIXTURE"}, before), ARN)
    kinds = [
        kind
        for kind, _ in project(
            record(2, pk, sk, {**before, "flowFailed": True, "GSI1PK": "OP#OP-TEST#INCIDENT#CLOSED"}, before), ARN
        )
    ]
    assert kinds == ["incident_history"]


@pytest.mark.parametrize(
    "pk,sk",
    [
        ("MACHINE#A", "ACK#abc"),
        ("MACHINE#A", "PROFILE"),
        ("MACHINE#A", "MAINT#today"),
        ("GRANT#issuer#subject", "OP#A"),
        ("PRINCIPAL#1", "OP#A"),
        ("MACHINE#A", "KPI#1"),
        ("OUTBOX#A", "1"),
        ("MODEL#X", "BASELINE"),
        ("PART#1", "FORECAST#W#1"),
    ],
)
def test_no_feedback_or_private_rows(pk, sk):
    assert project(record(1, pk, sk, {"private": "PRIVATE-FIXTURE"}), ARN) == []


def test_ttl_and_foreign_sources(archive):
    ttl = record(12, "MACHINE#A", "INCIDENT#old", None, {"private": "PRIVATE-FIXTURE"})
    assert project(ttl, ARN) == []
    foreign = {**reading(2, sample()), "eventSourceARN": ARN.replace("af-dev-arquifusion", "other-project")}
    assert export([foreign], archive, ARN) == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert not keys(archive, ROOT)


def test_stock_only_emits_proven_physical_consumption():
    result = [project(e, ARN) for e in stock_events()]
    assert [len(r) for r in result] == [0, 0, 1, 0, 0, 1]
    assert result[2][0][1]["row"] == {
        "date": "2026-11-13",
        "partId": "P-1",
        "warehouseId": "W-1",
        "qty": 1,
        "reason": "CORRECTIVE",
    }
    assert result[5][0][0] == "stock_review"


def test_data_survives_failure_between_archive_and_pointer(archive, monkeypatch):
    put = archive.put_new

    def drop_pointer(key, *args):
        if key.startswith(PENDING):
            raise OSError("synthetic interruption")
        return put(key, *args)

    monkeypatch.setattr(archive, "put_new", drop_pointer)
    records = [incidents()[-1]]
    assert export(records, archive, ARN) == {"batchItemFailures": [{"itemIdentifier": "23"}]}
    [data] = keys(archive, ROOT + "data/incidents/")
    monkeypatch.setattr(archive, "put_new", put)
    batch = data.rsplit("/", 1)[1].split(".")[0]
    archive.requeue("incidents", batch)
    assert export(records, archive, ARN) == {"batchItemFailures": []}
    assert len(keys(archive, PENDING)) == 1


def test_wrong_bucket_tags_or_state_bucket_rejected(archive, monkeypatch):
    monkeypatch.setenv("AF_LAKE_ACCOUNT_ID", OWNER)
    monkeypatch.setenv("AF_LAKE_BUCKET", "af-dev-tfstate-123456789012")
    with pytest.raises(ValueError, match="dedicated"):
        configured_archive()
    monkeypatch.setenv("AF_LAKE_BUCKET", BUCKET)
    archive.client.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": [{"Key": "project", "Value": "other"}]})
    with pytest.raises(ValueError, match="project tags"):
        configured_archive()


def test_same_project_catalog_is_rejected(archive, monkeypatch):
    monkeypatch.setenv("AF_LAKE_ACCOUNT_ID", OWNER)
    monkeypatch.setenv("AF_LAKE_BUCKET", BUCKET)
    tags = archive.client.get_bucket_tagging(Bucket=BUCKET)["TagSet"]
    for tag in tags:
        if tag["Key"] == "purpose":
            tag["Value"] = "incident-catalog"
    archive.client.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": tags})
    with pytest.raises(ValueError, match="project tags"):
        configured_archive()
