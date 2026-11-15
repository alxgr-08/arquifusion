import io
from pathlib import Path

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from infra.gcp import rehearse_object as relay
from services.lake_to_bigquery.telemetry import bigquery_row
from services.lake_to_bigquery.tests.bigquery_double import BigQueryDouble, decoded_parquet
from services.stream_exporter.lake_io import Archive, DataError

FIXTURE = Path(__file__).resolve().parents[2] / "services/lake_to_bigquery/tests/firehose-syn-9001.parquet"
KEY = "records/2026/11/15/07/telemetry.parquet"


def test_exact_academy_fixture_replay_and_kpi_after_proving_source(monkeypatch):
    data = FIXTURE.read_bytes()
    expected = bigquery_row(decoded_parquet(data)[0])
    row = relay.inspect_file(data, expected)
    assert row["machineId"] == "SYN-9001" and row["operationId"] == "AF-PARQUET-SMOKE"
    bq = BigQueryDouble()
    bq.project = "arquifusion"
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="af-test-operator-lake")
        archive = Archive(s3, "af-test-operator-lake", "123456789012")
        s3.put_object(**archive.args(KEY), Body=data)
        with pytest.raises(DataError, match="matching source"):
            relay.run_remote("return-kpi", archive, None, bq, KEY, data, row)
        first = relay.run_remote("load", archive, None, bq, KEY, data, row)
        assert first["stageJob"] and first["loadJob"]
        assert relay.run_remote("load", archive, None, bq, KEY, data, row) == first
        assert bq.tables["arquifusion.af_lake.telemetry"] == [row]
        returned = []
        monkeypatch.setattr(relay, "return_selected_kpi", lambda client, table, **scope: returned.append(scope) or {})
        relay.run_remote("return-kpi", archive, None, bq, KEY, data, row)
        assert returned[0]["machine_id"] == "SYN-9001" and returned[0]["operation_id"] == "AF-PARQUET-SMOKE"
        with pytest.raises(DataError, match="differs"):
            relay.run_remote("load", archive, None, bq, KEY, data + b"other", row)
        with pytest.raises(DataError, match="records"):
            relay.run_remote("load", archive, None, bq, "aws/terraform.tfstate", data, row)


def test_reject_another_message_and_mixed_rows_before_connecting():
    data = FIXTURE.read_bytes()
    expected = bigquery_row(decoded_parquet(data)[0])
    with pytest.raises(DataError, match="original simulator"):
        relay.inspect_file(data, expected | {"operationId": "OTHER"})
    rows = decoded_parquet(data) * 2
    output = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), output)
    with pytest.raises(DataError, match="one bounded"):
        relay.inspect_file(output.getvalue(), expected)


def test_relay_refuses_an_explicit_credential_file_before_access(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "unused")
    with pytest.raises(DataError, match="personal Google ADC"):
        relay.operator_clients("123456789012")
