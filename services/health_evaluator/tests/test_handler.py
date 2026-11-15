import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import handler
import pytest
from af_shared import AnomalyDetected
from moto import mock_aws

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "examples"
TABLE = "af-test-arquifusion"
START = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PATTERN = [-2, -1, 0, 1, 2]


class FakeEvents:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def put_events(self, Entries: list[dict]) -> dict:  # noqa: N803 (boto3 signature)
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "1"}]}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AF_TABLE_NAME", TABLE)
    monkeypatch.setenv("AF_EVENT_BUS", "af-test-bus")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")
            ],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": f"GSI{i}",
                    "KeySchema": [
                        {"AttributeName": f"GSI{i}PK", "KeyType": "HASH"},
                        {"AttributeName": f"GSI{i}SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for i in (1, 2)
            ],
        )
        events = FakeEvents()
        handler._cache.clear()
        handler._cache["events"] = events
        yield events
        handler._cache.clear()


def telemetry(i: int, **overrides: float) -> dict:
    base = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    k = PATTERN[i % 5]
    base["messageId"] = str(uuid.uuid4())
    base["timestamp"] = (START + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base["metrics"] |= {
        "temperatureC": 78 + k,
        "vibrationMmS": 4.2 + 0.2 * k,
        "pressureBar": 210 + 2 * k,
        "fuelRateLph": 31 + 0.5 * k,
        "rpm": 1800 + 20 * k,
    }
    base["metrics"] |= overrides
    return base


def profile() -> dict:
    return boto3.resource("dynamodb").Table(TABLE).get_item(Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"})["Item"]


def test_first_reading_creates_profile_and_reading(aws) -> None:
    assert handler.process(telemetry(0))["result"] == "OK"

    item = profile()
    assert item["GSI1PK"] == "OP#MINA-NORTE#MACHINE"
    assert item["GSI1SK"] == "OPERATING#EXC-0042"
    assert item["severity"] == "OK"
    assert item["zoneId"] == "Z3"
    assert "temperatureC" in item["baseline"]

    readings = (
        boto3.resource("dynamodb")
        .Table(TABLE)
        .query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :r)",
            ExpressionAttributeValues={":pk": "MACHINE#EXC-0042", ":r": "READING#"},
        )["Items"]
    )
    assert len(readings) == 1
    assert readings[0]["expiresAt"] == int((START + timedelta(hours=48)).timestamp())
    assert readings[0]["context"]["fuelBatchId"] == "FB-1120"


def test_duplicate_message_is_skipped(aws) -> None:
    msg = telemetry(0)
    assert handler.process(msg)["result"] == "OK"
    assert handler.process(msg)["result"] == "SKIPPED"


def test_out_of_order_reading_does_not_overwrite_profile(aws) -> None:
    handler.process(telemetry(5))
    assert handler.process(telemetry(3))["result"] == "SKIPPED"
    assert profile()["lastSeen"] == "2026-11-13T12:05:00Z"


def test_invalid_payload_is_rejected_without_raising(aws) -> None:
    assert handler.process({"machineId": "EXC-0042"})["result"] == "INVALID"


def test_status_change_updates_gsi1(aws) -> None:
    handler.process(telemetry(0))
    idle = telemetry(1)
    idle["status"] = "IDLE"
    handler.process(idle)
    assert profile()["GSI1SK"] == "IDLE#EXC-0042"


def test_fault_publishes_one_valid_anomaly_event(aws) -> None:
    for i in range(60):
        handler.process(telemetry(i))
    assert aws.entries == []

    results = [handler.process(telemetry(60 + i, temperatureC=92.4, vibrationMmS=7.8)) for i in range(10)]

    assert results[-1]["severity"] == "CRITICAL"
    assert 1 <= len(aws.entries) <= 2
    entry = aws.entries[0]
    assert entry["Source"] == "arquifusion.health"
    assert entry["DetailType"] == "AnomalyDetected"
    assert entry["EventBusName"] == "af-test-bus"
    event = AnomalyDetected.model_validate(
        {"source": entry["Source"], "detail-type": entry["DetailType"], "detail": json.loads(entry["Detail"])}
    )
    assert event.detail.machine_id == "EXC-0042"
    assert event.detail.signals[0].metric in {"temperatureC", "vibrationMmS"}


def test_fleet_baseline_from_dynamodb_is_used(aws) -> None:
    boto3.resource("dynamodb").Table(TABLE).put_item(
        Item={
            "PK": "MODEL#EXC-320",
            "SK": "BASELINE",
            "metrics": {"temperatureC": {"mean": 78, "std": 2}, "vibrationMmS": {"mean": 4, "std": 1}},
        }
    )
    results = [handler.process(telemetry(i, temperatureC=99.0, vibrationMmS=9.0)) for i in range(8)]
    assert results[-1]["severity"] in {"WARNING", "CRITICAL"}
    assert aws.entries
    assert json.loads(aws.entries[0]["Detail"])["comparedAgainst"] == ["MODEL_FLEET"]


def test_config_comes_from_ssm(aws) -> None:
    boto3.client("ssm").put_parameter(
        Name="/af/health/thresholds", Value=json.dumps({"streak_required": 5}), Type="SecureString"
    )
    assert handler.load_config().streak_required == 5


class LambdaContext:
    function_name = "af-test-health-evaluator"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:af-test-health-evaluator"
    aws_request_id = "req-1"


def test_lambda_entrypoint(aws) -> None:
    assert handler.handler(telemetry(0), LambdaContext())["result"] == "OK"
    batch = handler.handler([telemetry(1), telemetry(2)], LambdaContext())
    assert [r["result"] for r in batch["results"]] == ["OK", "OK"]
