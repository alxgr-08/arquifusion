import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import handler
import pytest
from af_shared import AnomalyDetected
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
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
    assert profile()["lastSeen"] == "2026-11-13T12:05:00.000000Z"


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


def test_failed_publish_is_retried_from_pending_alert(aws) -> None:
    for i in range(60):
        handler.process(telemetry(i))

    def failing_put_events(Entries):  # noqa: N803 (boto3 signature)
        raise RuntimeError("EventBridge unavailable")

    publish_ok = aws.put_events
    aws.put_events = failing_put_events
    fault = None
    for i in range(10):
        fault = telemetry(60 + i, temperatureC=92.4, vibrationMmS=7.8)
        try:
            handler.process(fault)
        except RuntimeError:
            break
    assert profile()["pendingAlert"]["alertId"] == fault["messageId"]

    aws.put_events = publish_ok
    assert handler.process(fault)["result"] == "SKIPPED"  # Lambda retry of the same message

    assert len(aws.entries) == 1
    assert json.loads(aws.entries[0]["Detail"])["machineId"] == "EXC-0042"
    assert "pendingAlert" not in profile()


def test_wrong_operation_cannot_reassign_an_existing_machine(aws):
    source = telemetry(0)
    handler.process(source)
    before = profile()
    result = handler.process(telemetry(1) | {"operationId": "OTHER"})
    assert result == {"result": "INVALID"} and profile() == before
    assert (
        len(
            boto3.resource("dynamodb")
            .Table(TABLE)
            .query(
                KeyConditionExpression=Key("PK").eq("MACHINE#" + source["machineId"])
                & Key("SK").begins_with("READING#")
            )["Items"]
        )
        == 1
    )


def test_concurrent_operation_transfer_prevents_the_telemetry_transaction(aws, monkeypatch):
    handler.process(telemetry(0))
    source = telemetry(1)
    original = handler.transact_write
    table = boto3.resource("dynamodb").Table(TABLE)
    changed = []

    def transfer_then_write(db, actions):
        table.update_item(
            Key={"PK": f"MACHINE#{source['machineId']}", "SK": "PROFILE"},
            UpdateExpression="SET operationId = :op",
            ExpressionAttributeValues={":op": "OTHER"},
        )
        changed.append(profile())
        original(db, actions)

    monkeypatch.setattr(handler, "transact_write", transfer_then_write)
    with pytest.raises(ClientError, match="TransactionCanceled"):
        handler.process(source)
    assert (
        profile() == changed[0]
        and len(
            boto3.resource("dynamodb")
            .Table(TABLE)
            .query(
                KeyConditionExpression=Key("PK").eq("MACHINE#" + source["machineId"])
                & Key("SK").begins_with("READING#")
            )["Items"]
        )
        == 1
    )


def test_published_alert_leaves_no_pending_alert(aws) -> None:
    for i in range(60):
        handler.process(telemetry(i))
    for i in range(10):
        handler.process(telemetry(60 + i, temperatureC=92.4, vibrationMmS=7.8))
    assert aws.entries
    assert "pendingAlert" not in profile()


def test_silent_machine_recovery_from_durable_record_and_stream_replay(aws, monkeypatch):
    table = boto3.resource("dynamodb").Table(TABLE)
    table.put_item(
        Item={
            "PK": "MODEL#EXC-320",
            "SK": "BASELINE",
            "metrics": {"temperatureC": {"mean": 78, "std": 2}, "vibrationMmS": {"mean": 4, "std": 1}},
        }
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            aws, "put_events", lambda **_: {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
        )
        with pytest.raises(RuntimeError):
            for step in range(8):
                source = telemetry(step, temperatureC=99, vibrationMmS=10)
                handler.process(source)
    key = handler.outbox_key(source["machineId"], source["messageId"])
    row = table.get_item(Key=key)["Item"]
    assert row["GSI1PK"] == "OUTBOX#ANOMALY"
    table.update_item(Key={"PK": key["PK"], "SK": "PROFILE"}, UpdateExpression="REMOVE pendingAlert")
    with pytest.raises(ValueError):
        handler.reconcile(source["machineId"], source["messageId"], "OTHER")
    record = {
        "eventSource": "aws:dynamodb",
        "eventName": "INSERT",
        "eventSourceARN": table.table_arn + "/stream/test",
        "dynamodb": {
            "Keys": {k: {"S": v} for k, v in key.items()},
            "SequenceNumber": "81",
            "NewImage": {"operationId": {"S": source["operationId"]}},
        },
    }
    with monkeypatch.context() as patch:
        patch.setattr(aws, "put_events", lambda **_: {"FailedEntryCount": 0, "Entries": [{}]})
        assert handler.handler({"Records": [record]}, LambdaContext()) == {
            "batchItemFailures": [{"itemIdentifier": "81"}]
        }
    for _ in range(2):
        assert handler.handler({"Records": [record]}, LambdaContext()) == {"batchItemFailures": []}
    assert len(aws.entries) == 1 and "Item" not in table.get_item(Key=key)
    assert json.loads(aws.entries[0]["Detail"])["detectedAt"] == row["detail"]["detectedAt"]


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


def test_bus_outage_preserves_each_new_reading_and_retry_recovers_the_alert(aws, monkeypatch):
    table = boto3.resource("dynamodb").Table(TABLE)
    table.put_item(
        Item={
            "PK": "MODEL#EXC-320",
            "SK": "BASELINE",
            "metrics": {"temperatureC": {"mean": 78, "std": 2}, "vibrationMmS": {"mean": 4, "std": 1}},
        }
    )
    readings = [telemetry(i, temperatureC=99, vibrationMmS=10) for i in range(3)]
    with monkeypatch.context() as patch:
        patch.setattr(
            aws, "put_events", lambda **_: {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
        )
        for reading in readings:
            with pytest.raises(RuntimeError, match="EventBridge rejected"):
                handler.process(reading)
    saved = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :kind)",
        ExpressionAttributeValues={":pk": "MACHINE#EXC-0042", ":kind": "READING#"},
        ConsistentRead=True,
    )["Items"]
    assert {r["messageId"] for r in saved} == {r["messageId"] for r in readings}
    first = readings[0]
    assert table.get_item(Key=handler.outbox_key(first["machineId"], first["messageId"]))["Item"]
    assert handler.process(first) == {"result": "SKIPPED"}
    assert len(aws.entries) == 1 and "pendingAlert" not in profile()


def test_replay_recovers_its_own_outbox_when_the_profile_pointer_is_gone(aws, monkeypatch):
    table = boto3.resource("dynamodb").Table(TABLE)
    table.put_item(Item={"PK": "MODEL#EXC-320", "SK": "BASELINE", "metrics": {"temperatureC": {"mean": 78, "std": 2}}})
    source = telemetry(0, temperatureC=99)
    with monkeypatch.context() as patch:
        patch.setattr(aws, "put_events", lambda **_: {"FailedEntryCount": 1, "Entries": [{}]})
        with pytest.raises(RuntimeError):
            handler.process(source)
    key = handler.outbox_key(source["machineId"], source["messageId"])
    table.update_item(Key={"PK": key["PK"], "SK": "PROFILE"}, UpdateExpression="REMOVE pendingAlert")
    assert handler.process(source) == {"result": "SKIPPED"}
    assert len(aws.entries) == 1 and "Item" not in table.get_item(Key=key)


@pytest.mark.parametrize("interleaved_step", [1, 3])
def test_profile_cas_preserves_a_concurrent_reading_and_retry_uses_its_baseline(aws, monkeypatch, interleaved_step):
    handler.process(telemetry(0))
    real_transaction = handler.transact_write
    interloper = telemetry(interleaved_step)
    incoming = telemetry(2)
    invoked = False

    def interleave(table, actions):
        nonlocal invoked
        if not invoked:
            invoked = True
            assert handler.process(interloper)["result"] == "OK"
        real_transaction(table, actions)

    with monkeypatch.context() as patch:
        patch.setattr(handler, "transact_write", interleave)
        if interleaved_step == 1:
            with pytest.raises(handler._table().meta.client.exceptions.TransactionCanceledException):
                handler.process(incoming)
        else:
            assert handler.process(incoming) == {"result": "SKIPPED"}
    assert datetime.fromisoformat(profile()["lastSeen"]) == datetime.fromisoformat(interloper["timestamp"])
    if interleaved_step == 1:
        assert handler.process(incoming)["result"] == "OK"
        assert profile()["baseline"]["temperatureC"]["n"] == 3


def test_invalid_telemetry_diagnostics_omit_the_supplied_input(aws, monkeypatch):
    errors = []
    monkeypatch.setattr(handler.logger, "error", lambda message, **fields: errors.append(fields))
    assert handler.process({"machineId": "EXC-0042", "secret": "do-not-log-this"}) == {"result": "INVALID"}
    assert errors and "do-not-log-this" not in json.dumps(errors)
    assert all("input" not in err for err in errors[0]["extra"]["errors"])


def test_readings_within_one_second_keep_fixed_precision_and_legacy_replays_stay_idempotent(aws):
    table = boto3.resource("dynamodb").Table(TABLE)
    legacy = telemetry(0)
    assert handler.process(legacy)["result"] == "OK"
    canonical_key = {"PK": "MACHINE#EXC-0042", "SK": "READING#2026-11-13T12:00:00.000000Z"}
    legacy_key = {**canonical_key, "SK": "READING#2026-11-13T12:00:00Z"}
    old = table.get_item(Key=canonical_key)["Item"]
    table.delete_item(Key=canonical_key)
    table.put_item(Item=old | legacy_key)
    table.update_item(
        Key={"PK": "MACHINE#EXC-0042", "SK": "PROFILE"},
        UpdateExpression="SET lastSeen = :old",
        ExpressionAttributeValues={":old": legacy["timestamp"]},
    )
    assert handler.process(legacy) == {"result": "SKIPPED"}
    assert "Item" not in table.get_item(Key=canonical_key)
    first = telemetry(0)
    first["timestamp"] = "2026-11-13T12:00:00.100001Z"
    second = telemetry(0)
    second["timestamp"] = "2026-11-13T12:00:00.900002Z"
    assert handler.process(first)["result"] == "OK"
    assert handler.process(second)["result"] == "OK"
    assert handler.process(first) == {"result": "SKIPPED"}
    assert profile()["lastSeen"] == "2026-11-13T12:00:00.900002Z"
    first_stored = table.get_item(Key={**canonical_key, "SK": "READING#" + first["timestamp"]})["Item"]
    second_stored = table.get_item(Key={**canonical_key, "SK": "READING#" + second["timestamp"]})["Item"]
    assert first_stored["messageId"] == first["messageId"] and second_stored["messageId"] == second["messageId"]


def test_retry_reconciles_old_outbox_and_current_escalation_independently(aws, monkeypatch):
    table = boto3.resource("dynamodb").Table(TABLE)
    table.put_item(Item={"PK": "MODEL#EXC-320", "SK": "BASELINE", "metrics": {"temperatureC": {"mean": 78, "std": 2}}})
    first = telemetry(0, temperatureC=99)
    with monkeypatch.context() as patch:
        patch.setattr(aws, "put_events", lambda **_: {"FailedEntryCount": 1, "Entries": [{}]})
        with pytest.raises(RuntimeError):
            handler.process(first)
    # A later escalation can replace the profile pointer while the old row remains pending.
    second = telemetry(1, temperatureC=99)
    first_key = handler.outbox_key(first["machineId"], first["messageId"])
    next_key = handler.outbox_key(second["machineId"], second["messageId"])
    old = table.get_item(Key=first_key)["Item"]
    next_detail = old["detail"] | {"detectedAt": "2026-11-13T12:01:00.000000Z"}
    table.put_item(Item=old | next_key | {"alertId": second["messageId"], "detail": next_detail})
    table.update_item(
        Key={"PK": first_key["PK"], "SK": "PROFILE"},
        UpdateExpression="SET pendingAlert = :next, lastSeen = :time",
        ExpressionAttributeValues={
            ":next": {"alertId": second["messageId"], "detail": next_detail},
            ":time": second["timestamp"],
        },
    )
    successful_put_events = aws.put_events

    def fail_only_new(Entries):  # noqa: N803 (boto3 signature)
        if json.loads(Entries[0]["Detail"])["detectedAt"] == "2026-11-13T12:01:00Z":
            return {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
        return successful_put_events(Entries)

    with monkeypatch.context() as patch:
        patch.setattr(aws, "put_events", fail_only_new)
        with pytest.raises(RuntimeError):
            handler.process(first)
    assert len(aws.entries) == 1 and "Item" not in table.get_item(Key=first_key)
    assert table.get_item(Key=next_key)["Item"] and profile()["pendingAlert"]["alertId"] == second["messageId"]
    assert handler.process(second) == {"result": "SKIPPED"}
    assert len(aws.entries) == 2 and "pendingAlert" not in profile()


def test_timestamp_without_a_timezone_is_rejected_before_database_access(aws, monkeypatch):
    source = telemetry(0)
    source["timestamp"] = source["timestamp"].removesuffix("Z")
    monkeypatch.setattr(handler, "load_context", lambda *_: pytest.fail("Invalid timestamp reached DynamoDB"))
    assert handler.process(source) == {"result": "INVALID"}


@pytest.mark.parametrize("legacy_pointer_only", [True, False])
def test_new_escalation_preserves_the_previous_legacy_or_modern_outbox(aws, monkeypatch, legacy_pointer_only):
    table = boto3.resource("dynamodb").Table(TABLE)
    table.put_item(Item={"PK": "MODEL#EXC-320", "SK": "BASELINE", "metrics": {"temperatureC": {"mean": 78, "std": 2}}})
    first = telemetry(0, temperatureC=99)
    second = telemetry(1, temperatureC=99)
    first_key = handler.outbox_key(first["machineId"], first["messageId"])
    with monkeypatch.context() as patch:
        patch.setattr(aws, "put_events", lambda **_: {"FailedEntryCount": 1, "Entries": [{}]})
        with pytest.raises(RuntimeError):
            handler.process(first)
        initial = table.get_item(Key=first_key)["Item"]
        if legacy_pointer_only:
            table.delete_item(Key=first_key)
        else:
            table.update_item(Key=first_key, UpdateExpression="SET attempts = :n", ExpressionAttributeValues={":n": 3})
        # A recovered machine can escalate again before the older event has been accepted.
        table.update_item(
            Key={"PK": first_key["PK"], "SK": "PROFILE"},
            UpdateExpression="SET severity = :ok",
            ExpressionAttributeValues={":ok": "OK"},
        )
        with pytest.raises(RuntimeError):
            handler.process(second)
    preserved = table.get_item(Key=first_key)["Item"]
    assert preserved["detail"] == initial["detail"]
    assert preserved["GSI1PK"] == "OUTBOX#ANOMALY"
    if not legacy_pointer_only:
        assert preserved["attempts"] == 3
    assert profile()["pendingAlert"]["alertId"] == second["messageId"]
    assert table.get_item(Key=handler.outbox_key(second["machineId"], second["messageId"]))["Item"]
    assert handler.process(first) == {"result": "SKIPPED"}
    assert len(aws.entries) == 2 and "Item" not in table.get_item(Key=first_key)
    assert "pendingAlert" not in profile()
