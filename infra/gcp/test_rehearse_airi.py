from datetime import date

import boto3
import pytest
from moto import mock_aws

from infra.gcp import rehearse_airi


@mock_aws
def test_atomic_bridge_return_and_replay():
    db = boto3.resource("dynamodb", region_name="us-east-1")
    table = db.create_table(
        TableName="af-dev-arquifusion",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("PK", "SK")],
    )
    case = {
        "machineId": "TLABC-0123",
        "operationId": "AF-LAKE-TEST-" + "A" * 32,
        "messageId": "2c4d6dec-82ee-4328-9d0c-91a22d0610fd",
        "model": "TEST",
        "machineType": "EXCAVATOR",
        "status": "OPERATING",
        "timestamp": "2026-11-16T01:56:32Z",
        "location": {"lat": -10.0, "lon": -76.0, "zoneId": "ZT"},
    }
    day = {
        "machine_id": case["machineId"],
        "model": case["model"],
        "date": date(2026, 11, 16),
        "utilization_pct": 1.0,
        "operating_hours": 0.25,
        "idle_hours": 0,
        "overuse_hours": 0,
        "tons_moved": 18.2,
        "cost_per_hour": 2408.5,
    }
    written, key = rehearse_airi.store_result(table, case, day, "a" * 64, "query-id")
    assert written == "written"
    record = table.get_item(Key=key, ConsistentRead=True)["Item"]
    assert record["sourceMessageId"] == case["messageId"] and record["source"] == "bigquery"
    assert "clusterId" not in record
    assert rehearse_airi.store_result(table, case, day, "a" * 64, "query-id") == ("already_present", key)
    with pytest.raises(ValueError, match="overwrite"):
        rehearse_airi.store_result(table, case, day | {"tons_moved": 42}, "a" * 64, "query-id")
    assert table.scan(Select="COUNT")["Count"] == 2


def test_single_case_sql_keeps_analytics_math_and_filters_the_source():
    statement = rehearse_airi.kpi_sql("airi-erp.af_bridge_demo.telemetry_test")
    assert statement.count("airi-erp.af_bridge_demo.telemetry_test") == 1
    assert "messageId = @message_id AND machineId = @machine_id AND operationId = @operation_id" in statement
    assert "ROUND(fuel_liters, 1) AS fuel_liters" in statement
    assert "CREATE OR REPLACE" not in statement and "af_lake." not in statement
