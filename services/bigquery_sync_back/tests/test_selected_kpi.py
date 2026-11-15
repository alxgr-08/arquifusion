from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from af_shared import keys
from moto import mock_aws

from services.bigquery_sync_back import handler as service

MACHINE, OP, DAY = "ENS-0007", "ENSAYO-0007", date(2026, 11, 15)
PROFILE = {"PK": f"MACHINE#{MACHINE}", "SK": "PROFILE", "operationId": OP, "model": "SYNTHETIC"}
KPI = {"PK": f"MACHINE#{MACHINE}", "SK": f"KPI#{DAY}"}
ROW = {
    "machine_id": MACHINE,
    "operation_id": OP,
    "model": PROFILE["model"],
    "date": DAY,
    "utilization_pct": 4.2,
    "operating_hours": 1.0,
    "idle_hours": 0.0,
    "overuse_hours": 0.0,
    "tons_moved": 0.0,
    "cost_per_hour": 2400.0,
    "fuel_liters_per_ton": None,
}


class Output:
    project = "arquifusion"

    def __init__(self, rows):
        self.rows = rows

    def query(self, sql, *, job_config, location):
        assert location == "US" and job_config.use_legacy_sql is False
        assert not job_config.destination and "LIMIT 2" in sql
        assert "COUNTIF(operationId IS NULL OR operationId != @operation_id OR model IS NULL) = 0" in sql
        assert "WHERE k.machine_id = @machine_id AND k.date = @kpi_date" in sql and "JOIN scope" in sql
        assert MACHINE not in sql and OP not in sql
        assert [(p.name, p.type_, p.value) for p in job_config.query_parameters] == [
            ("machine_id", "STRING", MACHINE),
            ("operation_id", "STRING", OP),
            ("kpi_date", "DATE", DAY),
        ]
        return SimpleNamespace(result=lambda *, timeout: iter(self.rows))


@pytest.fixture
def table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        t = resource.create_table(
            TableName="af-dev-arquifusion",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("PK", "SK", "GSI1PK", "GSI1SK")],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        t.put_item(Item=PROFILE)
        yield t


def run(table, rows):
    return service.return_selected_kpi(Output(rows), table, machine_id=MACHINE, operation_id=OP, kpi_date=DAY)


def test_native_transaction_and_unknown_response_replay_read_through_index(table):
    other = keys.kpi_item("ENS-0008", "ENSAYO-0008", DAY.isoformat()) | {"sentinel": True}
    table.put_item(Item=other)
    assert run(table, [ROW]) == {"kpis": 1, "status": "written"}
    assert run(table, [ROW]) == {"kpis": 0, "status": "already_present"}
    [item] = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :key",
        ExpressionAttributeValues={":key": f"OP#{OP}#KPI#{DAY}"},
    )["Items"]
    assert item["utilizationPct"] == Decimal("4.2") and item["idleHours"] == 0 and "fuelLitersPerTon" not in item
    assert item["source"] == "bigquery" and item["operationId"] == OP
    assert table.get_item(Key={"PK": other["PK"], "SK": other["SK"]})["Item"] == other
    assert table.get_item(Key=keys.machine_profile(MACHINE))["Item"] == PROFILE


def test_only_create_and_profile_ownership(table):
    original = KPI | {"owner": "another-writer"}
    table.put_item(Item=original)
    with pytest.raises(ValueError, match="Existing KPI"):
        run(table, [ROW])
    assert table.get_item(Key=KPI)["Item"] == original
    table.put_item(Item=PROFILE | {"operationId": "OTHER"})
    with pytest.raises(ValueError, match="profile"):
        run(table, [ROW])
    assert table.get_item(Key=KPI)["Item"] == original


def test_ambiguous_foreign_and_missing_rows_leave_table_untouched(table):
    assert run(table, []) == {"kpis": 0, "status": "not_found"}
    for rows, message in [
        ([ROW, ROW], "Ambiguous"),
        ([ROW | {"operation_id": "OTHER"}], "differs"),
        ([ROW | {"date": date(2026, 11, 14)}], "differs"),
    ]:
        with pytest.raises(ValueError, match=message):
            run(table, rows)
    assert "Item" not in table.get_item(Key=KPI)


def test_lambda_entrypoint_and_rejected_scopes(table, monkeypatch):
    arn = table.table_arn
    account = arn.split(":")[4]
    ctx = SimpleNamespace(invoked_function_arn=f"arn:aws:lambda:us-east-1:{account}:function:af-dev-bigquery-sync-back")
    event = {"machineId": MACHINE, "operationId": OP, "date": DAY.isoformat()}
    for key, val in {
        "AF_TABLE_ARN": arn,
        "AF_SYNC_MACHINE_ID": MACHINE,
        "AF_SYNC_OPERATION_ID": OP,
        "AF_SYNC_DATE": DAY.isoformat(),
    }.items():
        monkeypatch.setenv(key, val)
    client = Mock(return_value=Output([ROW]))
    monkeypatch.setattr(service, "wif_client", client)
    for bad in [{}, event | {"mode": "legacy"}, event | {"machineId": "EXC-0042"}]:
        with pytest.raises(ValueError, match="scope"):
            service.selected_handler(bad, ctx)
    monkeypatch.setenv("AF_TABLE_ARN", arn.replace(account, "000000000000"))
    with pytest.raises(ValueError, match="ARN"):
        service.selected_handler(event, ctx)
    client.assert_not_called()
    monkeypatch.setenv("AF_TABLE_ARN", arn)
    assert service.selected_handler(event, ctx) == {"kpis": 1, "status": "written"}


def test_identity_requires_the_federation_layer_or_explicit_legacy_secret(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "arquifusion")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ValueError, match="federation"):
        service.wif_client()
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/opt/arquifusion/gcp-wif.json")
    auth, connect = Mock(), Mock()
    monkeypatch.setattr(service.aws.Credentials, "from_file", auth)
    monkeypatch.setattr(service.bigquery, "Client", connect)
    assert service.wif_client() is connect.return_value
    auth.assert_called_once_with("/opt/arquifusion/gcp-wif.json", scopes=["https://www.googleapis.com/auth/bigquery"])
    monkeypatch.setenv("AF_SYNC_AUTH_MODE", "legacy_secret")
    monkeypatch.delenv("AF_SYNC_MODE", raising=False)
    secret_client = Mock()
    monkeypatch.setattr(service.boto3, "client", secret_client)
    with pytest.raises(ValueError, match="disallowed"):
        service.bigquery_fetcher()
    secret_client.assert_not_called()
    monkeypatch.setenv("AF_SYNC_MODE", "legacy")
    monkeypatch.setenv("AF_GCP_SECRET_ID", "af/test/synthetic")
    secret_client.return_value.get_secret_value.return_value = {"SecretString": '{"project_id":"other-project"}'}
    with pytest.raises(ValueError, match="Unexpected service-account project"):
        service.bigquery_fetcher()
    secret_client.return_value.get_secret_value.assert_called_once_with(SecretId="af/test/synthetic")
    from google.oauth2 import service_account

    legacy = Mock()
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", legacy)
    secret_client.return_value.get_secret_value.return_value = {"SecretString": '{"project_id":"arquifusion"}'}
    assert callable(service.bigquery_fetcher())
    legacy.assert_called_once_with({"project_id": "arquifusion"})
    connect.assert_called_with(project="arquifusion", credentials=legacy.return_value, location="US")


@pytest.mark.parametrize("machine,operation", [("ENS-0007", "ENSAYO-0008"), ("EXC-0042", "MINA-NORTE")])
def test_real_or_mismatched_scope_fails_before_query(table, machine, operation):
    client = Mock()
    with pytest.raises(ValueError, match="outside"):
        service.return_selected_kpi(client, table, machine_id=machine, operation_id=operation, kpi_date=DAY)
    client.query.assert_not_called()
