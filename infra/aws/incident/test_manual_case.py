"""The runner uses native DynamoDB operations and a controlled in-process workflow client."""

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from af_shared.ddb import to_ddb
from moto import mock_aws

from infra.aws.incident import run_case as run


@pytest.fixture
def case_table(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTO_ACCOUNT_ID", run.ACCOUNT)
    monkeypatch.delenv("AF_AUTH_DISABLED", raising=False)
    folder = tmp_path / "case"
    journal = run.prepare(81, folder)
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=run.REGION)
        table = resource.create_table(
            TableName=run.TABLE,
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
            Tags=[{"Key": k, "Value": v} for k, v in run.TAGS.items()],
        )
        yield folder, journal, case, table


def test_conditional_seed_and_ambiguous_response_reconcile_without_overwriting(case_table, monkeypatch):
    folder, journal, case, table = case_table
    path = folder / "journal.json"
    original = table.meta.client.transact_write_items
    calls = []

    def lose_response(**kwargs):
        calls.append(kwargs)
        original(**kwargs)
        raise TimeoutError("lost seed response")

    monkeypatch.setattr(table.meta.client, "transact_write_items", lose_response)
    with pytest.raises(TimeoutError):
        run.seed(table, case, journal, path)
    assert len(calls) == 1 and calls[0]["ClientRequestToken"] == journal["seedToken"]
    assert all("attribute_not_exists(PK)" == a["Put"]["ConditionExpression"] for a in calls[0]["TransactItems"])
    run.seed(table, case, journal, path)
    assert journal["seeded"] is True and len(calls) == 1
    run.seed(table, case, journal, path)
    assert len(calls) == 1
    table.update_item(
        Key={"PK": case["items"][0]["PK"], "SK": case["items"][0]["SK"]},
        UpdateExpression="SET rehearsalId = :other",
        ExpressionAttributeValues={":other": "another-run"},
    )
    with pytest.raises(ValueError, match="ownership"):
        run.seed(table, case, journal, path)


@pytest.mark.parametrize("which", ["machine", "operation", "technician", "grant", "stock", "warehouse"])
def test_existing_synthetic_namespace_blocks_all_seed_writes(case_table, which):
    folder, journal, case, table = case_table
    ids = case["kit"]["run.json"]
    keys = {
        "machine": {"PK": "MACHINE#" + ids["machine"], "SK": "LEGACY"},
        "operation": {"PK": "OP#" + ids["operation"], "SK": "OTHER"},
        "technician": {"PK": "TECH#" + ids["tech"], "SK": "ASSIGN#OLD"},
        "grant": {"PK": next(r["PK"] for r in case["items"] if r["PK"].startswith("GRANT#")), "SK": "OP#OTHER"},
        "stock": {"PK": "PART#BRG-SPH-22", "SK": "STOCK#" + ids["warehouse"]},
        "warehouse": {
            "PK": "PART#OTHER",
            "SK": "STOCK#" + ids["warehouse"],
            "GSI1PK": "WH#" + ids["warehouse"],
            "GSI1SK": "PART#OTHER",
        },
    }
    existing = keys[which] | {"sentinel": True}
    table.put_item(Item=existing)
    with pytest.raises(ValueError, match="already"):
        run.seed(table, case, journal, folder / "journal.json")
    assert table.scan()["Items"] == [existing]


def test_preflight_rejects_wrong_account_rule_recipients_and_package(case_table, monkeypatch, tmp_path):
    _, _, _, table = case_table
    clients = {k: Mock() for k in ("sts", "events", "ssm", "sns", "lambda", "stepfunctions")}
    clients["ssm"].get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                dict.fromkeys(["OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"], run.TOPIC)
            )
        }
    }
    clients["sts"].get_caller_identity.return_value = {"Account": run.ACCOUNT}
    clients["events"].describe_rule.return_value = {"State": "DISABLED"}
    clients["sns"].get_topic_attributes.return_value = {
        "Attributes": {"SubscriptionsConfirmed": "0", "SubscriptionsPending": "0"}
    }
    clients["stepfunctions"].describe_state_machine.side_effect = lambda **kw: {
        "stateMachineArn": kw["stateMachineArn"],
        "roleArn": run.ROLE,
        "type": "STANDARD",
        "status": "ACTIVE",
        "definition": "{}",
    }
    clients["stepfunctions"].list_tags_for_resource.return_value = {
        "tags": [{"key": k, "value": v} for k, v in run.TAGS.items()]
    }
    monkeypatch.setattr(run, "compiled", lambda path: {})
    source = tmp_path / "package.zip"
    source.write_bytes(b"reviewed-local-test")
    monkeypatch.setattr(run, "PACKAGES", {"health-evaluator": source})
    fn = {
        "FunctionArn": f"arn:aws:lambda:{run.REGION}:{run.ACCOUNT}:function:af-dev-health-evaluator",
        "Runtime": "python3.12",
        "Role": run.ROLE,
        "State": "Active",
        "LastUpdateStatus": "Successful",
        "CodeSha256": base64.b64encode(hashlib.sha256(source.read_bytes()).digest()).decode(),
    }
    clients["lambda"].get_function_configuration.return_value = fn
    clients["lambda"].list_tags.return_value = {"Tags": run.TAGS}
    assert run.preflight(clients, table)["rule"] == "DISABLED"
    clients["sts"].get_caller_identity.return_value = {"Account": "000000000000"}
    with pytest.raises(ValueError, match="account"):
        run.preflight(clients, table)
    clients["sts"].get_caller_identity.return_value = {"Account": run.ACCOUNT}
    fn["CodeSha256"] = "different"
    with pytest.raises(ValueError, match="package"):
        run.preflight(clients, table)
    fn["CodeSha256"] = base64.b64encode(hashlib.sha256(source.read_bytes()).digest()).decode()
    clients["events"].describe_rule.return_value = {"State": "ENABLED"}
    with pytest.raises(ValueError, match="disabled"):
        run.preflight(clients, table)
    clients["events"].describe_rule.return_value = {"State": "DISABLED"}
    clients["sns"].get_topic_attributes.return_value = {
        "Attributes": {"SubscriptionsConfirmed": "1", "SubscriptionsPending": "0"}
    }
    with pytest.raises(ValueError, match="recipients"):
        run.preflight(clients, table)
    clients["sns"].get_topic_attributes.return_value = {
        "Attributes": {"SubscriptionsConfirmed": "0", "SubscriptionsPending": "0"}
    }
    clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": '{"OPERATOR":"other-topic"}'}}
    with pytest.raises(ValueError, match="outside"):
        run.preflight(clients, table)


class Workflow:
    class exceptions:
        class ExecutionDoesNotExist(Exception):
            pass

    def __init__(self, table, case):
        self.table, self.case, self.executions, self.starts, self.callback_ids = table, case, {}, [], []
        self.incident_key = {"PK": "MACHINE#ENS-0081", "SK": "INCIDENT#2026-11-15T09:00:00Z#INC-test"}

    def start_execution(self, **request):
        arn = run.execution_arn(request["stateMachineArn"], request["name"])
        self.starts.append(arn)
        row = {
            "executionArn": arn,
            "stateMachineArn": request["stateMachineArn"],
            "input": request["input"],
            "status": "SUCCEEDED",
        }
        if request["stateMachineArn"] == run.MOTHER:
            child = run.execution_arn(run.CHILD, "controlled-child")
            row["output"] = json.dumps({"ExecutionArn": child})
            self.executions[child] = {"stateMachineArn": run.CHILD, "status": "RUNNING", "input": "{}"}
            self.table.put_item(
                Item={
                    **self.incident_key,
                    "incidentId": "INC-test",
                    "machineId": "ENS-0081",
                    "operationId": "ENSAYO-0081",
                    "assignedTech": "T-E0081",
                    "state": "ASSIGNED",
                    "pendingStage": "ARRIVAL",
                    "waitGeneration": "arrival-test",
                    "taskToken": "private-arrival-token",
                    "waitExecution": child,
                    "timeline": [],
                    "certified": {},
                    "partsReserved": [{"partId": "BRG-SPH-22", "warehouse": "WH-E0081", "qty": 1}],
                }
            )
        self.executions[arn] = row
        return {"executionArn": arn, "ResponseMetadata": {"RequestId": "start-" + str(len(self.starts))}}

    def describe_execution(self, executionArn):
        if executionArn not in self.executions:
            raise self.exceptions.ExecutionDoesNotExist()
        return self.executions[executionArn]

    def send_task_success(self, taskToken, output):
        row = self.table.get_item(Key=self.incident_key, ConsistentRead=True)["Item"]
        assert row["taskToken"] == taskToken
        payload = json.loads(output)
        stage = row["pendingStage"]
        row["certified"][stage] = {"ackId": payload["ackId"]}
        row["timeline"].append({"event": "ARRIVED" if stage == "ARRIVAL" else "RESOLVED", "at": run.stamp()})
        receipt = {"PK": row["PK"], "SK": f"ACK#INC-test#{stage}#{row['waitGeneration']}"}
        self.table.update_item(
            Key=receipt,
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "ACCEPTED"},
        )
        if stage == "ARRIVAL":
            row.update(
                pendingStage="RESOLUTION", waitGeneration="resolution-test", taskToken="private-resolution-token"
            )
        else:
            row.update(state="RESOLVED", partsUsed=[{"partId": "BRG-SPH-22", "warehouse": "WH-E0081", "qty": 1}])
            for key in ("pendingStage", "waitGeneration", "taskToken"):
                row.pop(key)
            self.executions[run.execution_arn(run.CHILD, "controlled-child")]["status"] = "SUCCEEDED"
            self.table.put_item(Item={"PK": row["PK"], "SK": "MAINT#one", "maintenanceId": "MNT-one"})
            self.table.update_item(
                Key={"PK": "PART#BRG-SPH-22", "SK": "STOCK#WH-E0081"},
                UpdateExpression="SET qty=:z, available=:z, reserved=:z, reservations=:r",
                ExpressionAttributeValues={":z": 0, ":r": {}},
            )
        self.table.put_item(Item=to_ddb(row))
        self.callback_ids.append(stage)
        return {"ResponseMetadata": {"RequestId": "callback-" + stage}}

    def get_paginator(self, name):
        assert name == "get_execution_history"
        return SimpleNamespace(
            paginate=lambda **kw: [{"events": [{"stateEnteredEventDetails": {"name": "DuplicateIgnored"}}]}]
        )


@pytest.mark.parametrize("lost_callback", [False, True])
def test_execute_resume_keeps_one_seed_two_callbacks_and_sanitized_evidence(case_table, monkeypatch, lost_callback):
    folder, journal, case, table = case_table
    wf = Workflow(table, case)
    callback = wf.send_task_success
    if lost_callback:

        def accept_and_lose(**kwargs):
            callback(**kwargs)
            raise TimeoutError("The successful callback response was lost")

        wf.send_task_success = accept_and_lose
    sns = Mock()
    sns.get_topic_attributes.return_value = {"Attributes": {"SubscriptionsConfirmed": "0", "SubscriptionsPending": "0"}}
    events = Mock()
    events.describe_rule.return_value = {"State": "DISABLED"}
    ssm = Mock()
    ssm.get_parameter.return_value = {
        "Parameter": {
            "Value": json.dumps(
                dict.fromkeys(["OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"], run.TOPIC)
            )
        }
    }
    clients = {"sns": sns, "ssm": ssm, "events": events, "stepfunctions": wf}
    session = SimpleNamespace(
        client=lambda name, **kw: clients.get(name, Mock()),
        resource=lambda name, **kw: SimpleNamespace(Table=lambda name: table),
    )
    monkeypatch.setattr(run, "preflight", lambda clients, table: {"rule": "DISABLED"})
    result = run.execute(folder, run.ACCOUNT, 10, session)
    assert result["incident"]["state"] == "RESOLVED"
    assert len(wf.starts) == 2 and wf.callback_ids == ["ARRIVAL", "RESOLUTION"]
    assert run.execute(folder, run.ACCOUNT, 10, session)["maintenanceKeys"] == ["MAINT#one"]
    assert len(wf.starts) == 2 and len(wf.callback_ids) == 2
    text = (folder / "journal.json").read_text() + (folder / "evidence.json").read_text()
    assert "private-arrival-token" not in text and "private-resolution-token" not in text
    assert "taskToken" not in text
    if not lost_callback:
        assert "callback-ARRIVAL" in text and "callback-RESOLUTION" in text
    assert "synthetic claims" in text
    altered = dict(case)
    altered["event"] = {**case["event"], "account": "000000000000"}
    run.save(folder / "case.json", altered)
    with pytest.raises(ValueError, match="modified"):
        run.execute(folder, run.ACCOUNT, 10, session)


def test_lost_start_response_reuses_its_execution_and_rejects_changed_input(case_table, monkeypatch):
    folder, journal, case, table = case_table
    wf = Workflow(table, case)
    begin = wf.start_execution

    def accept_and_lose(**kwargs):
        begin(**kwargs)
        raise TimeoutError("Accepted execution, missing response")

    monkeypatch.setattr(wf, "start_execution", accept_and_lose)
    path = folder / "journal.json"
    with pytest.raises(TimeoutError):
        run.start(wf, run.MOTHER, case["event"], "entry", journal, path)
    assert len(wf.starts) == 1
    assert run.start(wf, run.MOTHER, case["event"], "entry", journal, path) == wf.starts[0]
    assert len(wf.starts) == 1
    with pytest.raises(ValueError, match="changed"):
        run.start(wf, run.MOTHER, {**case["event"], "id": "different"}, "entry", journal, path)
