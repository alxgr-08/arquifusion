"""Run one manual Academy incident rehearsal. Default preparation is offline; --execute performs writes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "shared"), str(ROOT / "services/api")]

from af_shared.ddb import from_ddb  # noqa: E402

from scripts import ensayo_kit  # noqa: E402

ACCOUNT = "975050277936"
REGION = "us-east-1"
TABLE = "af-dev-arquifusion"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/LabRole"
MOTHER = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:af-dev-incident-entry"
CHILD = MOTHER.removesuffix("-entry")
TOPIC = f"arn:aws:sns:{REGION}:{ACCOUNT}:af-dev-incident-notifications"
TAGS = {"project": "arquifusion", "env": "dev", "owner": "rol1"}
PACKAGES = {
    "health-evaluator": "build/health-evaluator/health-evaluator.zip",
    "incident-tasks": "build/incident/incident-tasks-catalog.zip",
    "assign-technician": "build/incident/assign-technician.zip",
    "ai-diagnosis": "build/incident/ai-diagnosis.zip",
    "notify": "build/incident/notify.zip",
}
DESERIALIZER = TypeDeserializer()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp():
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def save(path, value):
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(canonical(value) + b"\n")
    os.replace(tmp, path)


def prepare(run, folder):
    require(1 <= run <= 9999, "Choose a run from 0001 through 9999")
    folder.mkdir(parents=True, exist_ok=False)
    kit = ensayo_kit.build(run)
    event = kit["anomaly-caso2-rodamiento.json"]
    event["account"] = ACCOUNT
    identity = str(uuid4())
    items = [
        {**{k: DESERIALIZER.deserialize(v) for k, v in kit[name].items()}, "rehearsalId": identity, "run": f"{run:04d}"}
        for name in kit
        if name.startswith("item-")
    ]
    frozen = {"kit": kit, "items": from_ddb(items), "event": event}
    save(folder / "case.json", frozen)
    state = {
        "runnerId": identity,
        "run": f"{run:04d}",
        "caseSha256": fingerprint(frozen),
        "seedToken": str(uuid4()),
        "preparedAt": stamp(),
        "authEvidence": "Local API handler with synthetic claims and IAM AWS access. Google JWT and HTTP API untested.",
        "steps": {},
    }
    save(folder / "journal.json", state)
    return state


def query_all(table, **kwargs):
    result = []
    while True:
        page = table.query(**kwargs)
        result.extend(page["Items"])
        if not page.get("LastEvaluatedKey"):
            return result
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def rows_for_machine(table, case):
    return query_all(
        table, KeyConditionExpression=Key("PK").eq("MACHINE#" + case["kit"]["run.json"]["machine"]), ConsistentRead=True
    )


def check_notifications(clients):
    topics = json.loads(
        clients["ssm"].get_parameter(Name="/af/notify/topics", WithDecryption=False)["Parameter"]["Value"]
    )
    require(
        isinstance(topics, dict)
        and set(topics) == {"OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"}
        and set(topics.values()) == {TOPIC},
        "Notification roles point outside the reviewed empty project topic",
    )
    attrs = clients["sns"].get_topic_attributes(TopicArn=TOPIC)["Attributes"]
    require(
        attrs.get("SubscriptionsConfirmed") == "0" and attrs.get("SubscriptionsPending") == "0",
        "The project topic has subscribers or pending recipients",
    )
    rule = clients["events"].describe_rule(Name="af-dev-incident", EventBusName="af-dev-bus")
    require(rule["State"] == "DISABLED", "Incident rule must remain disabled")


def compiled(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    for name in ("incident_tasks", "assign_technician", "ai_diagnosis", "notify"):
        text = text.replace(
            "${" + name + "_arn}", f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:af-dev-{name.replace('_', '-')}"
        )
    return json.loads(
        text.replace("${incident_arn}", CHILD).replace(
            "${execution_prefix}", CHILD.replace(":stateMachine:", ":execution:") + ":"
        )
    )


def preflight(clients, table):
    require(clients["sts"].get_caller_identity()["Account"] == ACCOUNT, "Unexpected Academy account")
    metadata = table.meta.client.describe_table(TableName=TABLE)["Table"]
    require(
        metadata["TableArn"] == f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE}"
        and metadata["TableStatus"] == "ACTIVE",
        "Unexpected DynamoDB table or status",
    )
    tags = table.meta.client.list_tags_of_resource(ResourceArn=metadata["TableArn"])["Tags"]
    require(TAGS.items() <= {t["Key"]: t["Value"] for t in tags}.items(), "Unexpected table ownership tags")
    sfn = clients["stepfunctions"]
    for arn, path in ((MOTHER, "infra/aws/incident/entry.asl.json"), (CHILD, "statemachines/incident.asl.json")):
        info = sfn.describe_state_machine(stateMachineArn=arn)
        require(
            info["stateMachineArn"] == arn
            and info["roleArn"] == ROLE
            and info["type"] == "STANDARD"
            and info["status"] == "ACTIVE",
            "Unexpected workflow identity, type, status or execution role",
        )
        require(json.loads(info["definition"]) == compiled(path), "Workflow definition differs from the checkout")
        tags = sfn.list_tags_for_resource(resourceArn=arn)["tags"]
        require(TAGS.items() <= {t["key"]: t["value"] for t in tags}.items(), "Unexpected workflow ownership tags")
    functions = clients["lambda"]
    for name, path in PACKAGES.items():
        arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:af-dev-{name}"
        info = functions.get_function_configuration(FunctionName=arn)
        require(
            info["FunctionArn"] == arn
            and info["Runtime"] == "python3.12"
            and info["Role"] == ROLE
            and info["State"] == "Active"
            and info["LastUpdateStatus"] == "Successful",
            "Unexpected Lambda configuration",
        )
        digest = base64.b64encode(hashlib.sha256((ROOT / path).read_bytes()).digest()).decode()
        require(info["CodeSha256"] == digest, "Deployed Lambda package differs from the local artifact: " + name)
        require(TAGS.items() <= functions.list_tags(Resource=arn)["Tags"].items(), "Unexpected Lambda ownership tags")
    check_notifications(clients)
    return {"checkedAt": stamp(), "account": ACCOUNT, "region": REGION, "rule": "DISABLED", "snsRecipients": 0}


def seed(table, case, journal, path):
    rows = case["items"]
    require(
        all(row["rehearsalId"] == journal["runnerId"] and row["run"] == journal["run"] for row in rows),
        "Case rows differ from this runner",
    )
    if journal.get("seedIntent"):
        current = [table.get_item(Key={"PK": r["PK"], "SK": r["SK"]}, ConsistentRead=True).get("Item") for r in rows]
        if journal.get("seeded"):
            require(all(r and r.get("rehearsalId") == journal["runnerId"] for r in current), "Seed ownership changed")
            return
        if all(from_ddb(a) == b for a, b in zip(current, rows, strict=True)):
            journal["seeded"] = True
            save(path, journal)
            return
        require(not any(current), "Ambiguous or foreign partial seed; inspect its rows before resuming")
    ids = case["kit"]["run.json"]
    for pk in (
        "OP#" + ids["operation"],
        "MACHINE#" + ids["machine"],
        "TECH#" + ids["tech"],
        next(r["PK"] for r in rows if r["PK"].startswith("GRANT#")),
    ):
        require(
            not query_all(table, KeyConditionExpression=Key("PK").eq(pk), ConsistentRead=True),
            "The synthetic partition already exists",
        )
    stock = next(r for r in rows if r["SK"].startswith("STOCK#"))
    require(
        not table.get_item(Key={"PK": stock["PK"], "SK": stock["SK"]}, ConsistentRead=True).get("Item"),
        "The synthetic stock key already exists",
    )
    require(
        not query_all(table, IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("WH#" + ids["warehouse"])),
        "The synthetic warehouse already has stock",
    )
    journal["seedIntent"] = stamp()
    save(path, journal)
    from af_shared.ddb import to_ddb

    result = table.meta.client.transact_write_items(
        ClientRequestToken=journal["seedToken"],
        TransactItems=[
            {"Put": {"TableName": TABLE, "Item": to_ddb(row), "ConditionExpression": "attribute_not_exists(PK)"}}
            for row in rows
        ],
    )
    journal.update(seeded=True, seedRequestId=result["ResponseMetadata"]["RequestId"])
    save(path, journal)


def execution_arn(arn, name):
    return arn.replace(":stateMachine:", ":execution:") + ":" + name


def start(sfn, machine, event, key, journal, path):
    name = f"af-ensayo-{journal['run']}-{journal['runnerId'][:12]}-{key}"
    arn = execution_arn(machine, name)
    step = journal["steps"].get(key)
    if step is None:
        step = {"arn": arn, "inputSha256": fingerprint(event), "attemptedAt": stamp()}
        journal["steps"][key] = step
        save(path, journal)
    require(step["arn"] == arn and step["inputSha256"] == fingerprint(event), "Execution input changed")
    try:
        info = sfn.describe_execution(executionArn=arn)
    except sfn.exceptions.ExecutionDoesNotExist:
        response = sfn.start_execution(stateMachineArn=machine, name=name, input=canonical(event).decode())
        require(response["executionArn"] == arn, "Unexpected execution ARN")
        step["requestId"] = response["ResponseMetadata"]["RequestId"]
        save(path, journal)
        return arn
    require(
        info["stateMachineArn"] == machine and fingerprint(json.loads(info["input"])) == fingerprint(event),
        "Existing execution differs from this case",
    )
    return arn


def wait_execution(sfn, arn, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = sfn.describe_execution(executionArn=arn)
        if info["status"] != "RUNNING":
            require(info["status"] == "SUCCEEDED", "The workflow ended with " + info["status"])
            return info
        time.sleep(2)
    raise TimeoutError("Workflow is still running; resume this owned journal")


def api_handler():
    spec = importlib.util.spec_from_file_location("af_incident_rehearsal_api", ROOT / "services/api/handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CallbackRecorder:
    """Persist callback acknowledgements without retaining the task token or callback payload."""

    def __init__(self, client, item, journal, path):
        self.client, self.item, self.journal, self.path = client, item, journal, path

    def send_task_success(self, **kwargs):
        result = self.client.send_task_success(**kwargs)
        self.item.setdefault("callbackRequestIds", []).append(result["ResponseMetadata"]["RequestId"])
        save(self.path, self.journal)
        return result


def acknowledge(api, sfn, table, clients, case, journal, path, action, timeout):
    stage = "ARRIVAL" if action == "ARRIVED" else "RESOLUTION"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = rows_for_machine(table, case)
        incidents = [row for row in rows if row["SK"].startswith("INCIDENT#")]
        require(len(incidents) <= 1, "The rehearsal has multiple incidents")
        if incidents and incidents[0].get("pendingStage") == stage:
            incident = incidents[0]
            ids = case["kit"]["run.json"]
            require(
                incident.get("operationId") == ids["operation"] and incident.get("assignedTech") == ids["tech"],
                "Incident ownership or technician differs from the kit",
            )
            item = journal.get(action)
            request = dict(case["kit"]["api-get-incident.json"])
            request["rawPath"] = request["rawPath"].replace("INC-XXXX", incident["incidentId"])
            if item is None:
                response = api.route(request, table, sfn, stamp())
                require(response["statusCode"] == 200, "Local API detail returned " + str(response["statusCode"]))
                etag = response["headers"].get("ETag") or response["headers"].get("etag")
                require(bool(re.fullmatch(r'"[A-Za-z0-9_-]{1,128}"', etag or "")), "Missing strong ETag")
                body = {"action": action}
                if action == "RESOLVED":
                    body.update(notes=f"Ensayo {journal['run']}", partsUsed=[{"partId": "BRG-SPH-22", "qty": 1}])
                item = {"etag": etag, "body": body, "incidentId": incident["incidentId"], "attemptedAt": stamp()}
                journal[action] = item
                save(path, journal)
            require(item["incidentId"] == incident["incidentId"], "The incident changed")
            check_notifications(clients)
            request["requestContext"] = {**request["requestContext"], "http": {"method": "POST"}}
            request.update(
                rawPath=request["rawPath"] + "/ack", headers={"If-Match": item["etag"]}, body=json.dumps(item["body"])
            )
            response = api.route(request, table, CallbackRecorder(sfn, item, journal, path), stamp())
            item["lastStatus"] = response["statusCode"]
            save(path, journal)
            if response["statusCode"] == 200:
                item["acceptedAt"] = stamp()
                save(path, journal)
                return
            require(response["statusCode"] == 503, "Local API ack returned " + str(response["statusCode"]))
        elif incidents and stage in (incidents[0].get("certified") or {}):
            require(action in journal, "A different caller confirmed the wait")
            journal[action]["certifiedAt"] = stamp()
            save(path, journal)
            return
        time.sleep(2)
    raise TimeoutError("Waiting for " + stage + "; resume this owned journal")


def evidence(sfn, table, case, journal):
    rows = rows_for_machine(table, case)
    incidents = [r for r in rows if r["SK"].startswith("INCIDENT#")]
    maintenance = [r for r in rows if r["SK"].startswith("MAINT#")]
    receipts = [r for r in rows if r["SK"].startswith("ACK#")]
    require(len(incidents) == 1 and incidents[0]["state"] == "RESOLVED", "Incident is not uniquely resolved")
    require(len(maintenance) == 1 and len(receipts) == 2, "Expected one maintenance and two receipts")
    require(all(r["status"] == "ACCEPTED" for r in receipts), "A receipt remains unconfirmed")
    ids = case["kit"]["run.json"]
    stock = table.get_item(Key={"PK": "PART#BRG-SPH-22", "SK": "STOCK#" + ids["warehouse"]}, ConsistentRead=True)[
        "Item"
    ]
    require(
        (stock["qty"], stock["available"], stock["reserved"]) == (0, 0, 0) and not stock["reservations"],
        "Unexpected final synthetic stock",
    )
    return {
        "checkedAt": stamp(),
        "operation": ids["operation"],
        "machine": ids["machine"],
        "incident": {
            k: from_ddb(incidents[0].get(k)) for k in ("incidentId", "state", "assignedTech", "partsUsed", "timeline")
        },
        "maintenanceKeys": [r["SK"] for r in maintenance],
        "receipts": [{k: r.get(k) for k in ("SK", "action", "status", "ackId", "at", "acceptedAt")} for r in receipts],
        "stock": {k: from_ddb(stock[k]) for k in ("PK", "SK", "qty", "available", "reserved")},
        "executions": journal["steps"],
        "authEvidence": journal["authEvidence"],
    }


def execute(folder, account, timeout, session=None):
    require(account == ACCOUNT, "Supply the reviewed Academy account")
    journal_path = folder / "journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    require(journal["caseSha256"] == fingerprint(case), "Frozen case was modified")
    require(journal["run"] == case["kit"]["run.json"]["run"], "Journal belongs to another kit")
    require(os.getenv("AF_AUTH_DISABLED") != "true", "Local API authentication must be enabled")
    session = session or boto3.Session(profile_name="default", region_name=REGION)
    config = Config(connect_timeout=5, read_timeout=20, retries={"mode": "standard", "total_max_attempts": 2})
    clients = {
        name: session.client(name, config=config) for name in ("sts", "sns", "ssm", "events", "lambda", "stepfunctions")
    }
    table = session.resource("dynamodb", config=config).Table(TABLE)
    journal["preflight"] = preflight(clients, table)
    save(journal_path, journal)
    seed(table, case, journal, journal_path)
    check_notifications(clients)
    sfn = clients["stepfunctions"]
    parent = start(sfn, MOTHER, case["event"], "entry", journal, journal_path)
    dispatched = json.loads(wait_execution(sfn, parent, timeout)["output"])
    child = dispatched.get("ExecutionArn") or dispatched.get("executionArn")
    require(
        isinstance(child, str) and child.startswith(CHILD.replace(":stateMachine:", ":execution:") + ":"),
        "The parent did not return the expected child",
    )
    journal["steps"]["child"] = {"arn": child}
    save(journal_path, journal)
    api = api_handler()
    for action in ("ARRIVED", "RESOLVED"):
        acknowledge(api, sfn, table, clients, case, journal, journal_path, action, timeout)
    wait_execution(sfn, child, timeout)
    before = evidence(sfn, table, case, journal)
    check_notifications(clients)
    replay_event = {k: case["event"][k] for k in ("source", "detail-type", "detail")}
    replay = start(sfn, CHILD, replay_event, "replay", journal, journal_path)
    wait_execution(sfn, replay, timeout)
    visited = []
    for page in sfn.get_paginator("get_execution_history").paginate(executionArn=replay, includeExecutionData=False):
        visited.extend(e["stateEnteredEventDetails"]["name"] for e in page["events"] if "stateEnteredEventDetails" in e)
    require("DuplicateIgnored" in visited, "The repeated anomaly did not take DuplicateIgnored")
    after = evidence(sfn, table, case, journal)
    require(
        before["incident"] == after["incident"]
        and before["maintenanceKeys"] == after["maintenanceKeys"]
        and before["stock"] == after["stock"],
        "Replay changed the closed result",
    )
    after["replayStates"] = visited
    journal["completedAt"] = stamp()
    save(journal_path, journal)
    save(folder / "evidence.json", after)
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--account")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    try:
        if args.execute:
            require(args.folder is not None and args.account is not None, "Execution needs --folder and --account")
            require(10 <= args.timeout <= 600, "Use a wait timeout between 10 and 600 seconds")
            result = execute(args.folder, args.account, args.timeout)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "incident": result["incident"]["incidentId"],
                        "evidence": str(args.folder / "evidence.json"),
                    }
                )
            )
        else:
            require(args.run is not None, "Offline preparation needs --run")
            folder = args.folder or ROOT / "build/night-20261115" / f"incident-{args.run:04d}"
            prepare(args.run, folder)
            print("Prepared offline: " + str(folder))
        return 0
    except (ClientError, ValueError, TimeoutError) as exc:
        detail = (
            exc.response.get("Error", {}).get("Code", type(exc).__name__) if isinstance(exc, ClientError) else str(exc)
        )
        print("Stopped: " + detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
