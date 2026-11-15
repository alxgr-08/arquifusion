import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from af_shared import ddb, keys
from botocore.exceptions import ClientError
from moto import mock_aws

from infra.aws.maintenance import maintenance_trial as trial
from scripts import ensayo_kit
from services.offline_detector.handler import mark_if_stale

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "statemachines/tests"))
from asl_runner import Runner  # noqa: E402

NOW = datetime(2026, 11, 13, 14, tzinfo=UTC)
OP, MID, WH = "ENSAYO-0007", "ENS-0007", "WH-E0007"
CHECK = {"action": "check_offline", "operationId": OP}


@pytest.fixture
def table():
    with mock_aws():
        session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1")
        db = session.resource("dynamodb").create_table(
            TableName="af-dev-arquifusion",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("PK", "SK")],
            BillingMode="PAY_PER_REQUEST",
        )
        for run in (7, 8):
            kit = ensayo_kit.items(ensayo_kit.ids(run), ddb.iso(NOW))
            for name in ("item-op-settings.json", "item-machine.json"):
                db.put_item(Item=ddb.to_ddb(kit[name]))
        yield db


def populate(table):
    table.put_item(Item=keys.kpi_item(MID, OP, "2026-11-12") | {"operatingHours": 20, "overuseHours": 5})
    for wh, available, forecast in ((WH, 0, 2), ("WH-SUR", 90, 900)):
        for part in ("FLT-HID-01", "FLT-FUE-02"):
            table.put_item(Item=keys.stock_keys(part, wh) | {"partId": part, "warehouseId": wh, "available": available})
            table.put_item(Item=keys.forecast_keys(part, wh, "2026-W47") | {"forecastQty": forecast})


def test_asl_end_to_end_with_exact_stock_keys_and_daily_idempotence(table, monkeypatch):
    populate(table)
    original_query, original_get = table.query, table.get_item
    stock_reads = []

    def query(**kwargs):
        assert "IndexName" not in kwargs
        pk, sk = kwargs["KeyConditionExpression"].get_expression()["values"]
        assert pk.get_expression()["values"][1] == f"MACHINE#{MID}"
        assert all(key.startswith("KPI#") for key in sk.get_expression()["values"][1:])
        return original_query(**kwargs)

    def get_item(**kwargs):
        key = kwargs["Key"]
        if key["PK"].startswith("PART#"):
            assert key["SK"] in (f"STOCK#{WH}", f"FORECAST#{WH}#2026-W47")
            stock_reads.append((key["PK"], key["SK"]))
        return original_get(**kwargs)

    monkeypatch.setattr(table, "query", query)
    monkeypatch.setattr(table, "get_item", get_item)
    definition = json.loads((ROOT / "statemachines/preventive.asl.json").read_text(encoding="utf-8"))
    notifications = []

    def capture(event):
        notifications.append(event)
        assert trial.dispatch(event, table, OP, NOW) == {"externalDeliveries": 0}

    runner = Runner(
        definition,
        {"${incident_tasks_arn}": lambda event: trial.dispatch(event, table, OP, NOW), "${notify_arn}": capture},
    )
    first, second = (runner.run({"operationId": OP}) for _ in range(2))
    assert first["load"]["machines"][0]["machineId"] == MID and len(first["load"]["machines"]) == 1
    assert first["results"][0]["order"]["created"] and not second["results"][0]["order"]["created"]
    assert not first["results"][0]["order"]["partsOk"] and first["plan"]["orders"] == 1
    assert {(r["warehouseId"], r["suggestedQty"]) for r in first["plan"]["reorders"]} == {(WH, 3)}
    assert notifications[0] == {"kind": "DAILY_SUMMARY", "summary": first["plan"]["summary"]}
    assert runner.visited[-1] == "SendDailySummary"
    assert len(set(stock_reads)) == 4
    assert original_get(Key={"PK": "PART#FLT-HID-01", "SK": "STOCK#WH-SUR"})["Item"]["available"] == 90
    rows = table.scan()["Items"]
    assert [(r["PK"], r["operationId"]) for r in rows if r["SK"].startswith("MAINT#")] == [(f"MACHINE#{MID}", OP)]


def test_detector_marks_one_machine_and_keeps_fresh_or_reassigned_profile(table):
    assert trial.dispatch(CHECK, table, OP, NOW) == {"offline": {OP: [MID]}}
    assert trial.dispatch(CHECK, table, OP, NOW) == {"offline": {OP: []}}
    assert table.get_item(Key=keys.machine_profile("ENS-0008"))["Item"]["status"] == "OPERATING"
    profile = table.get_item(Key=keys.machine_profile(MID))["Item"] | {"status": "OPERATING"}
    table.put_item(Item=profile | {"lastSeen": ddb.iso(NOW)})
    assert not mark_if_stale(table, OP, profile, NOW, 5, run="0007")
    table.put_item(Item=profile | {"operationId": "MINA-NORTE", "GSI1PK": "OP#MINA-NORTE#MACHINE"})
    assert not mark_if_stale(table, OP, profile, NOW, 5, run="0007")
    assert table.get_item(Key=keys.machine_profile(MID))["Item"]["status"] == "OPERATING"
    table.delete_item(Key=keys.machine_profile(MID))
    assert not mark_if_stale(table, OP, profile, NOW, 5, run="0007")
    assert "Item" not in table.get_item(Key=keys.machine_profile(MID))


def test_fresh_fractional_seconds_at_the_cutoff_do_not_mark_offline(table):
    profile = table.get_item(Key=keys.machine_profile(MID))["Item"]
    table.put_item(Item=profile | {"lastSeen": "2026-11-13T13:55:00.500Z"})
    assert trial.dispatch(CHECK, table, OP, NOW) == {"offline": {OP: []}}
    assert table.get_item(Key=keys.machine_profile(MID))["Item"]["status"] == "OPERATING"


@pytest.mark.parametrize(
    "change",
    [
        {"settings": {"partsFromOtherWarehouses": True}},
        {"zoneWarehouses": {"ZE": ["WH-SUR"]}},
        {"zoneWarehouses": {"ZE": [WH, "WH-SUR"]}},
        {"run": "0008"},
    ],
)
def test_unapproved_catalog_mapping_fails_before_work(table, change):
    key = {"PK": f"OP#{OP}", "SK": "INCIDENT_SETTINGS"}
    table.put_item(Item=table.get_item(Key=key)["Item"] | change)
    with pytest.raises(ValueError, match="warehouse"):
        trial.dispatch(CHECK, table, OP, NOW)
    assert table.get_item(Key=keys.machine_profile(MID))["Item"]["status"] == "OPERATING"


def test_other_operations_machines_and_kpis_are_rejected(table):
    with pytest.raises(ValueError, match="configured operation"):
        trial.dispatch({"action": "load_machines", "operationId": "ENSAYO-0008"}, table, OP, NOW)
    with pytest.raises(ValueError, match="Configure exactly one"):
        trial.dispatch({"action": "load_machines", "operationId": "MINA-NORTE"}, table, "MINA-NORTE", NOW)
    other = {"machineId": "ENS-0008", "zoneId": "ZE"}
    with pytest.raises(ValueError, match="configured machine"):
        trial.dispatch({"action": "compute_due", "operationId": OP, "machine": other}, table, OP, NOW)
    table.put_item(Item=keys.kpi_item(MID, "MINA-NORTE", "2026-11-12") | {"operatingHours": 20})
    with pytest.raises(ValueError, match="KPI"):
        trial.dispatch(
            {"action": "compute_due", "operationId": OP, "machine": other | {"machineId": MID}}, table, OP, NOW
        )


def test_atomic_order_stops_if_profile_or_warehouse_changes_between_reads_and_write(table):
    populate(table)
    item = {"PK": f"MACHINE#{MID}", "SK": "MAINT#attempt"}
    settings_key = {"PK": f"OP#{OP}", "SK": "INCIDENT_SETTINGS"}
    settings = table.get_item(Key=settings_key)["Item"]
    table.put_item(Item=settings | {"zoneWarehouses": {"ZE": ["WH-SUR"]}})
    with pytest.raises(ClientError, match="TransactionCanceledException"):
        trial.save_order(table, item, OP, "0007", WH)
    table.put_item(Item=settings)
    profile = table.get_item(Key=keys.machine_profile(MID))["Item"]
    table.put_item(Item=profile | {"operationId": "ENSAYO-0008"})
    with pytest.raises(ClientError, match="TransactionCanceledException"):
        trial.save_order(table, item, OP, "0007", WH)
    assert "Item" not in table.get_item(Key=item)


def test_handler_rejects_wrong_arn_without_opening_dynamodb(monkeypatch):
    monkeypatch.setenv("AF_MAINTENANCE_OPERATION", OP)
    monkeypatch.setenv("AF_TABLE_ARN", "arn:aws:dynamodb:us-east-1:111122223333:table/af-dev-arquifusion")
    context = SimpleNamespace(
        invoked_function_arn="arn:aws:lambda:us-east-1:999900001111:function:af-dev-maintenance-trial-0007"
    )
    with pytest.raises(ValueError, match="regional Academy"):
        trial.handler(CHECK, context)
