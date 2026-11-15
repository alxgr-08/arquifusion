"""af-dev-offline-detector: marks machines OFFLINE when no telemetry arrived for N minutes (P4). Owner: Rol 2.

Trigger: EventBridge Scheduler (rate 5 minutes).
Env: AF_OPERATIONS (comma list, default MINA-NORTE), AF_OFFLINE_AFTER_MINUTES (default 5).
The update is conditional on lastSeen being unchanged, so a reading that lands meanwhile wins.
The health-evaluator (Rol 3) sets the status back from the next telemetry message.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from af_shared import keys
from af_shared.ddb import get_table, is_conditional_failure, iso, parse_iso
from af_shared.log import get_logger

logger = get_logger("offline-detector")


def machines(table: Any, operation_id: str) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    out, start = [], None
    while True:
        kwargs: dict[str, Any] = {
            "IndexName": "GSI1",
            "KeyConditionExpression": Key("GSI1PK").eq(f"OP#{operation_id}#MACHINE"),
        }
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table.query(**kwargs)
        out += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            return out


def mark_if_stale(
    table: Any,
    operation_id: str,
    machine: dict[str, Any],
    now: datetime,
    after_minutes: float,
    *,
    run: str | None = None,
) -> bool:
    cutoff = now - timedelta(minutes=after_minutes)
    seen = machine.get("lastSeen")
    if machine.get("status") == "OFFLINE" or (seen and parse_iso(seen) >= cutoff):
        return False
    machine_id = machine["PK"].removeprefix("MACHINE#")
    index = keys.machine_gsi1(operation_id, "OFFLINE", machine_id)
    values: dict[str, Any] = {":off": "OFFLINE", ":sk": index["GSI1SK"], ":group": index["GSI1PK"], ":at": iso(now)}
    names = {"#st": "status"}
    conditions = ["GSI1PK = :group", "(attribute_not_exists(#st) OR #st <> :off)"]
    if seen:
        conditions.append("lastSeen = :seen")
        values[":seen"] = seen
    else:
        conditions.append("attribute_not_exists(lastSeen)")
    if run is not None:
        conditions.append("operationId = :op AND machineId = :id AND zoneId = :zone AND #run = :run")
        values.update({":op": operation_id, ":id": machine_id, ":zone": machine["zoneId"], ":run": run})
        names["#run"] = "run"
    try:
        table.update_item(
            Key=keys.machine_profile(machine_id),
            UpdateExpression="SET #st = :off, GSI1SK = :sk, offlineSince = :at",
            ConditionExpression=" AND ".join(conditions),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise
        return False


def mark_offline(table: Any, operation_id: str, now: datetime, after_minutes: float) -> list[str]:
    return [
        machine["PK"].removeprefix("MACHINE#")
        for machine in machines(table, operation_id)
        if mark_if_stale(table, operation_id, machine, now, after_minutes)
    ]


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    table = get_table()
    after = float(os.environ.get("AF_OFFLINE_AFTER_MINUTES", "5"))
    result = {}
    for op in os.environ.get("AF_OPERATIONS", "MINA-NORTE").split(","):
        result[op] = mark_offline(table, op.strip(), datetime.now(UTC), after)
        logger.info("offline check", operationId=op, marked=result[op])
    return {"offline": result}
