"""af-dev-assign-technician (P5). Owner: Rol 2.

Trigger: Step Functions. Payload {"action": "assign" | "release", "incident": ..., "assignment": ...,
"diagnosis": ..., "profile": ...}. Env AF_ROUTE_CALCULATOR (Amazon Location route calculator, optional).
"""

from __future__ import annotations

import os
from typing import Any

import assignment
from af_shared.ddb import get_table, now_iso
from af_shared.log import get_logger

logger = get_logger("assign-technician")
_table: Any = None
_location: Any = None


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    global _table, _location
    if _table is None:
        _table = get_table()
    calculator = os.environ.get("AF_ROUTE_CALCULATOR")
    if calculator and _location is None:
        import boto3

        _location = boto3.client("location")
    inc = event.get("incident") or {}
    logger.append_keys(incidentId=inc.get("incidentId"), machineId=inc.get("machineId"))
    if event.get("action") == "release":
        result = assignment.release(_table, event, now_iso())
    else:
        result = assignment.assign(_table, event, now_iso(), _location, calculator)
    logger.info(
        "assignment", assigned=result.get("assigned"), techId=result.get("techId"), attempt=result.get("attempt")
    )
    return result
