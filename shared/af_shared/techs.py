"""Technician availability shared by assign-technician and incident-tasks. Owner: Rol 2.

availability: AVAILABLE (queue 0) | BUSY (queue >= 1) | OFF (frontend Technician type).
GSI1SK = {availability}#{techId}.
"""

from __future__ import annotations

from typing import Any

from af_shared import keys
from af_shared.ddb import from_ddb, is_conditional_failure


def release(table: Any, tech_id: str, operation_id: str, incident_id: str) -> dict[str, Any] | None:
    """Remove the incident from the technician's queue; back to AVAILABLE when the queue is empty.

    Idempotent: the conditional on the queue set makes a retried release a no-op.
    """
    try:
        updated = table.update_item(
            Key={"PK": f"TECH#{tech_id}", "SK": "PROFILE"},
            UpdateExpression="ADD #q :minus DELETE incidentQueue :inc",
            ConditionExpression="contains(incidentQueue, :id)",
            ExpressionAttributeNames={"#q": "queue"},
            ExpressionAttributeValues={":minus": -1, ":inc": {incident_id}, ":id": incident_id},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except Exception as exc:
        if is_conditional_failure(exc):
            return None
        raise
    if int(updated.get("queue", 0)) <= 0 and updated.get("availability") == "BUSY":
        try:
            table.update_item(
                Key={"PK": f"TECH#{tech_id}", "SK": "PROFILE"},
                UpdateExpression="SET availability = :a, GSI1SK = :sk, #q = :zero REMOVE currentIncidentId",
                ConditionExpression="availability = :busy AND #q <= :zero",
                ExpressionAttributeNames={"#q": "queue"},
                ExpressionAttributeValues={
                    ":a": "AVAILABLE",
                    ":busy": "BUSY",
                    ":zero": 0,
                    ":sk": keys.technician_keys(tech_id, operation_id, "AVAILABLE")["GSI1SK"],
                },
            )
        except Exception as exc:
            if not is_conditional_failure(exc):
                raise
    return from_ddb(updated)
