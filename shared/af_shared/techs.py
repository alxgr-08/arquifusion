"""Technician availability shared by assign-technician and incident-tasks. Owner: Rol 2.

availability: AVAILABLE (queue 0) | ASSIGNED (queue >= 1) | OFF_SHIFT. GSI1SK = {availability}#{techId}.
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
            UpdateExpression="ADD queueLength :minus DELETE incidentQueue :inc",
            ConditionExpression="contains(incidentQueue, :id)",
            ExpressionAttributeValues={":minus": -1, ":inc": {incident_id}, ":id": incident_id},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except Exception as exc:
        if is_conditional_failure(exc):
            return None
        raise
    if int(updated.get("queueLength", 0)) <= 0 and updated.get("availability") == "ASSIGNED":
        try:
            table.update_item(
                Key={"PK": f"TECH#{tech_id}", "SK": "PROFILE"},
                UpdateExpression="SET availability = :a, GSI1SK = :sk, queueLength = :zero REMOVE currentIncidentId",
                ConditionExpression="availability = :assigned AND queueLength <= :zero",
                ExpressionAttributeValues={
                    ":a": "AVAILABLE",
                    ":assigned": "ASSIGNED",
                    ":zero": 0,
                    ":sk": keys.technician_keys(tech_id, operation_id, "AVAILABLE")["GSI1SK"],
                },
            )
        except Exception as exc:
            if not is_conditional_failure(exc):
                raise
    return from_ddb(updated)
