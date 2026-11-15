"""Exercise the acceptance fixtures and the publish-after-commit failure using Moto only."""

import json
from unittest.mock import Mock

import boto3
import pytest
from jsonschema import Draft202012Validator
from moto import mock_aws
from prepare_case import CONTRACTS, REPO, prepare_case


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "services" / "health_evaluator"))
    import handler

    def load(name):
        return json.loads((case / f"{name}.json").read_text(encoding="utf-8"))

    case = prepare_case(tmp_path)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AF_TABLE_NAME", "af-test-arquifusion")
    monkeypatch.setenv("AF_EVENT_BUS", "af-test-bus")
    monkeypatch.setattr(handler, "_cache", {})
    with mock_aws():
        client = boto3.client("dynamodb")
        client.create_table(
            TableName="af-test-arquifusion",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"} for name in ("PK", "SK")],
        )
        client.put_item(TableName="af-test-arquifusion", Item=load("baseline"))
        yield handler, load, client


def test_normal_then_anomaly_reaches_matching_subscriber(scenario):
    handler, load, db = scenario
    events = boto3.client("events")
    events.create_event_bus(Name="af-test-bus")
    sqs = boto3.client("sqs")
    queue = sqs.create_queue(QueueName="af-test-health-evidence")["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    events.put_rule(Name="evidence", EventBusName="af-test-bus", EventPattern=json.dumps(load("event-pattern")))
    events.put_targets(Rule="evidence", EventBusName="af-test-bus", Targets=[{"Id": "evidence", "Arn": arn}])

    for name, severity, score in [("normal", "OK", 100), ("anomaly", "CRITICAL", 0)]:
        result = handler.process(load(name))
        assert result == {"result": "OK", "healthScore": score, "severity": severity, "emitted": name == "anomaly"}
        for key in (f"{name}-key", "profile-key"):
            item = db.get_item(TableName="af-test-arquifusion", Key=load(key), ConsistentRead=True)["Item"]
            assert item["severity"]["S"] == severity
            assert float(item["healthScore"]["N"]) == score
    message = json.loads(sqs.receive_message(QueueUrl=queue)["Messages"][0]["Body"])
    schema = json.loads((CONTRACTS / "anomaly-detected.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(message)
    assert message["detail"]["machineId"] == load("normal")["machineId"]
    assert message["detail"]["comparedAgainst"] == ["MODEL_FLEET"]


def test_rejected_publication_is_retained_and_retried_on_replay(scenario):
    handler, load, db = scenario
    events = Mock()
    events.put_events.return_value = {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
    handler._cache["events"] = events
    handler.process(load("normal"))
    with pytest.raises(RuntimeError, match="EventBridge rejected"):
        handler.process(load("anomaly"))
    profile = db.get_item(TableName="af-test-arquifusion", Key=load("profile-key"))["Item"]
    assert profile["severity"]["S"] == "CRITICAL"
    assert "pendingAlert" in profile
    assert db.get_item(TableName="af-test-arquifusion", Key=load("anomaly-key"))["Item"]
    first_attempt = events.put_events.call_args
    events.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "accepted"}]}
    assert handler.process(load("anomaly")) == {"result": "SKIPPED"}
    assert events.put_events.call_args_list == [first_attempt, first_attempt]
    profile = db.get_item(TableName="af-test-arquifusion", Key=load("profile-key"), ConsistentRead=True)["Item"]
    assert "pendingAlert" not in profile
    assert handler.process(load("anomaly")) == {"result": "SKIPPED"}
    assert events.put_events.call_count == 2
