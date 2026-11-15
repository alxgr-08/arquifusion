from datetime import date
from decimal import Decimal

import boto3
import handler
import pytest
from moto import mock_aws

TABLE = "af-test-arquifusion"

ROWS = {
    "daily_kpis": [
        {
            "machine_id": "TRK-0104",
            "model": "HT-793",
            "date": date(2026, 11, 13),
            "utilization_pct": 18.0,
            "operating_hours": 4.3,
            "idle_hours": 17.5,
            "down_hours": 0.0,
            "overuse_hours": 0.0,
            "tons_moved": 3060.0,
            "fuel_liters": 410.2,
            "fuel_liters_per_ton": 0.43,
            "fuel_liters_per_ton_model": 0.45,
            "cost_per_hour": 410.0,
            "fuel_anomaly": False,
            "cluster_id": 2,
            "cluster_label": "LOW_UTILIZATION_HIGH_COST",
            "trend_pct_per_week": 0.1,
            "progressive_increase": False,
        },
        {
            "machine_id": "EXC-0051",
            "model": "EXC-320",
            "date": date(2026, 11, 13),
            "utilization_pct": 76.0,
            "operating_hours": 18.2,
            "idle_hours": 5.8,
            "down_hours": 0.0,
            "overuse_hours": 0.0,
            "tons_moved": 2220.0,
            "fuel_liters": 5300.0,
            "fuel_liters_per_ton": 2.4,
            "fuel_liters_per_ton_model": 1.7,
            "cost_per_hour": 390.0,
            "fuel_anomaly": True,
            "cluster_id": 1,
            "cluster_label": "BALANCED",
            "trend_pct_per_week": 2.6,
            "progressive_increase": True,
        },
    ],
    "model_baselines": [
        {"model": "EXC-320", "metric": "temperatureC", "mean": 78.1, "std": 2.4, "n": 8420},
        {"model": "EXC-320", "metric": "vibrationMmS", "mean": 4.2, "std": 0.4, "n": 8420},
        {"model": "HT-793", "metric": "temperatureC", "mean": 85.0, "std": 2.1, "n": 9100},
    ],
    "parts_forecast": [
        {
            "part_id": "INJ-DSL-07",
            "warehouse_id": "WH-NORTE",
            "iso_week": "2026-W47",
            "forecast_qty": 2.3,
            "forecast_qty_upper": 5.1,
            "irregular": True,
            "coefficient_of_variation": 1.4,
        },
        {
            "part_id": "FLT-FUE-02",
            "warehouse_id": "WH-SUR",
            "iso_week": "2026-W47",
            "forecast_qty": 4.0,
            "forecast_qty_upper": 6.2,
            "irregular": None,
            "coefficient_of_variation": None,
        },
    ],
}


def fake_fetch(sql: str):
    for name, rows in ROWS.items():
        if f".{name}`" in sql.split("JOIN")[0]:
            return rows
    raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
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
        yield boto3.resource("dynamodb").Table(TABLE)


def test_sync_writes_kpis_baselines_and_forecasts(table) -> None:
    counts = handler.sync(fake_fetch, table, dataset="proj.af_out", operation_id="MINA-NORTE", days=7)
    assert counts == {"kpis": 2, "baselines": 2, "forecasts": 2}

    fleet_day = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :pk",
        ExpressionAttributeValues={":pk": "OP#MINA-NORTE#KPI#2026-11-13"},
    )["Items"]
    assert {i["machineId"] for i in fleet_day} == {"TRK-0104", "EXC-0051"}
    exc = table.get_item(Key={"PK": "MACHINE#EXC-0051", "SK": "KPI#2026-11-13"})["Item"]
    assert exc["fuelAnomaly"] is True
    assert exc["progressiveFuelIncrease"] is True
    assert exc["fuelLitersPerTon"] == Decimal("2.4")


def test_baseline_matches_health_evaluator_format(table) -> None:
    handler.sync(fake_fetch, table, dataset="proj.af_out", operation_id="MINA-NORTE", days=7)
    item = table.get_item(Key={"PK": "MODEL#EXC-320", "SK": "BASELINE"})["Item"]
    assert item["metrics"]["temperatureC"] == {"mean": Decimal("78.1"), "std": Decimal("2.4"), "n": Decimal("8420")}
    assert set(item["metrics"]) == {"temperatureC", "vibrationMmS"}


def test_forecast_items_and_null_handling(table) -> None:
    handler.sync(fake_fetch, table, dataset="proj.af_out", operation_id="MINA-NORTE", days=7)
    forecasts = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={":pk": "PART#INJ-DSL-07", ":sk": "FORECAST#WH-NORTE#"},
    )["Items"]
    assert forecasts[0]["isoWeek"] == "2026-W47"
    assert forecasts[0]["irregular"] is True
    plain = table.get_item(Key={"PK": "PART#FLT-FUE-02", "SK": "FORECAST#WH-SUR#2026-W47"})["Item"]
    assert plain["irregular"] is False
    assert "coefficientOfVariation" not in plain


def test_resync_is_idempotent(table) -> None:
    handler.sync(fake_fetch, table, dataset="proj.af_out", operation_id="MINA-NORTE", days=7)
    handler.sync(fake_fetch, table, dataset="proj.af_out", operation_id="MINA-NORTE", days=7)
    assert table.scan(Select="COUNT")["Count"] == 6


def test_queries_use_dataset() -> None:
    assert "`proj.af_out.daily_kpis`" in handler.KPI_SQL.format(dataset="proj.af_out", days=7)
