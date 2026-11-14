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
from af_shared.ddb import get_table, is_conditional_failure, iso
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


def mark_offline(table: Any, operation_id: str, now: datetime, after_minutes: float) -> list[str]:
    cutoff = iso(now - timedelta(minutes=after_minutes))
    marked = []
    for m in machines(table, operation_id):
        if m.get("status") == "OFFLINE":
            continue
        seen = m.get("lastSeen")
        if seen and seen >= cutoff:
            continue
        machine_id = m["PK"].removeprefix("MACHINE#")
        gsi1sk = keys.machine_gsi1(operation_id, "OFFLINE", machine_id)["GSI1SK"]
        values: dict[str, Any] = {":off": "OFFLINE", ":sk": gsi1sk, ":at": iso(now)}
        condition = "attribute_not_exists(lastSeen)"
        if seen:
            condition = "lastSeen = :seen"
            values[":seen"] = seen
        try:
            table.update_item(
                Key=keys.machine_profile(machine_id),
                UpdateExpression="SET #st = :off, GSI1SK = :sk, offlineSince = :at",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues=values,
            )
            marked.append(machine_id)
        except Exception as exc:
            if not is_conditional_failure(exc):
                raise
    return marked


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    table = get_table()
    after = float(os.environ.get("AF_OFFLINE_AFTER_MINUTES", "5"))
    result = {}
    for op in os.environ.get("AF_OPERATIONS", "MINA-NORTE").split(","):
        result[op] = mark_offline(table, op.strip(), datetime.now(UTC), after)
        logger.info("offline check", operationId=op, marked=result[op])
    return {"offline": result}
