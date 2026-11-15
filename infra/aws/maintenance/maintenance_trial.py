"""Manual entry point for a single configured operation from scripts/ensayo_kit.py."""

import os
import re
from datetime import UTC, datetime

from af_shared import ddb, keys
from botocore.exceptions import ClientError

from services.incident_tasks import preventive
from services.offline_detector.handler import mark_if_stale


def run_number(operation: str) -> str:
    match = re.fullmatch(r"ENSAYO-([0-9]{4})", operation)
    if not match:
        raise ValueError("Configure exactly one ENSAYO-nnnn operation from the incident rehearsal kit")
    return match[1]


def read_scope(table, operation, run):
    machine, warehouse = f"ENS-{run}", f"WH-E{run}"
    settings = table.get_item(Key={"PK": f"OP#{operation}", "SK": "INCIDENT_SETTINGS"}, ConsistentRead=True)
    row = settings.get("Item", {})
    if (
        row.get("run") != run
        or row.get("settings", {}).get("partsFromOtherWarehouses") is not False
        or row.get("zoneWarehouses") != {"ZE": [warehouse]}
    ):
        raise ValueError("The rehearsal kit must bind ZE exclusively to this run's warehouse")
    profile = table.get_item(Key=keys.machine_profile(machine), ConsistentRead=True).get("Item", {})
    expected = {
        "machineId": machine,
        "operationId": operation,
        "zoneId": "ZE",
        "run": run,
        "GSI1PK": f"OP#{operation}#MACHINE",
    }
    if any(profile.get(key) != value for key, value in expected.items()):
        raise ValueError("The configured operation does not own this rehearsal profile")
    return profile, warehouse


def save_order(table, item, operation, run, warehouse) -> bool:
    machine = f"ENS-{run}"
    conditions = [
        {
            "ConditionCheck": {
                "Key": {"PK": f"OP#{operation}", "SK": "INCIDENT_SETTINGS"},
                "ConditionExpression": "#run = :run AND zoneWarehouses = :zones AND #settings.#other = :off",
                "ExpressionAttributeNames": {
                    "#run": "run",
                    "#settings": "settings",
                    "#other": "partsFromOtherWarehouses",
                },
                "ExpressionAttributeValues": {":run": run, ":zones": {"ZE": [warehouse]}, ":off": False},
            }
        },
        {
            "ConditionCheck": {
                "Key": keys.machine_profile(machine),
                "ConditionExpression": "operationId = :op AND machineId = :mid AND zoneId = :zone "
                "AND #run = :run AND GSI1PK = :group",
                "ExpressionAttributeNames": {"#run": "run"},
                "ExpressionAttributeValues": {
                    ":op": operation,
                    ":mid": machine,
                    ":zone": "ZE",
                    ":run": run,
                    ":group": f"OP#{operation}#MACHINE",
                },
            }
        },
        {"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(PK)"}},
    ]
    try:
        ddb.transact_write(table, conditions)
        return True
    except ClientError as exc:
        reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
        if not ddb.is_transaction_conditional_failure(exc) or reasons != ["None", "None", "ConditionalCheckFailed"]:
            raise
        previous = table.get_item(Key={"PK": item["PK"], "SK": item["SK"]}, ConsistentRead=True).get("Item", {})
        for field in ("type", "maintenanceId", "machineId", "operationId", "zoneId"):
            if previous.get(field) != item[field]:
                raise ValueError("The order key is occupied by a different maintenance record") from exc
        return False


def dispatch(event, table, operation: str, now: datetime) -> dict:
    run = run_number(operation)
    machine = f"ENS-{run}"
    if event.keys() == {"kind", "summary"} and event["kind"] == "DAILY_SUMMARY" and isinstance(event["summary"], str):
        return {"externalDeliveries": 0}  # The ASL already stores the summary in $.plan.summary.
    action = event.get("action")
    if action in ("load_machines", "compute_due", "check_offline"):
        if event.get("operationId") != operation:
            raise ValueError("Invocation differs from the configured operation")
    elif action not in ("create_maintenance_order", "plan_restock"):
        raise ValueError("Unknown rehearsal action")
    profile, warehouse = read_scope(table, operation, run)
    approved = preventive.machine_fields(profile)
    if action == "check_offline":
        if event.keys() != {"action", "operationId"}:
            raise ValueError("The detector accepts only its action and the configured operation")
        marked = mark_if_stale(table, operation, profile, now, 5, run=run)
        return {"offline": {operation: [machine] if marked else []}}
    if action == "load_machines":
        return {"operationId": operation, "machines": [approved]}
    at = ddb.iso(now)
    if action == "plan_restock":
        results = event["results"]
        if len(results) != 1 or results[0].get("machineId") != machine:
            raise ValueError("Only the configured machine's result can enter restock planning")
        order = results[0].get("order")
        if order is not None:
            parts = [{"partId": p["partId"], "qty": p["qty"]} for p in order["parts"]]
            if order.get("machineId") != machine or order.get("zoneId") != "ZE" or parts != preventive.PREVENTIVE_KIT:
                raise ValueError("The order differs from the rehearsal machine, zone or preventive kit")
        return preventive.plan_restock(table, {"results": results}, at, warehouse=warehouse)
    supplied = event["machine"] if action == "compute_due" else event["due"]
    if supplied.get("machineId") != machine or supplied.get("zoneId") != "ZE":
        raise ValueError("Task input differs from the configured machine and zone")
    due = preventive.compute_due(table, {"machine": approved, "operationId": operation}, at, check_kpi_operation=True)
    if action == "compute_due":
        return due
    if supplied.get("operationId") != operation or not due["isDue"]:
        raise ValueError("The configured machine must currently be due for maintenance")
    return preventive.create_maintenance_order(
        table,
        {"due": due},
        at,
        warehouse=warehouse,
        save=lambda item: save_order(table, item, operation, run, warehouse),
    )


def handler(event, context):
    import boto3

    operation = os.environ["AF_MAINTENANCE_OPERATION"]
    run = run_number(operation)
    match = re.fullmatch(
        rf"arn:aws:lambda:us-east-1:([0-9]{{12}}):function:af-dev-maintenance-trial-{run}(?::[^:]+)?",
        context.invoked_function_arn,
    )
    if not match or os.environ.get("AF_TABLE_ARN") != f"arn:aws:dynamodb:us-east-1:{match[1]}:table/af-dev-arquifusion":
        raise ValueError("Expected the configured regional Academy Lambda and matching ArquiFusion table")
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("af-dev-arquifusion")
    return dispatch(event, table, operation, datetime.now(UTC))
