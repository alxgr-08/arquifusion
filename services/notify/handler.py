"""af-dev-notify (P9). Owner: Rol 2.

Trigger: Step Functions. Payload {"kind": INCIDENT|ESCALATED|ZONE_INCIDENT|RESTOCK|NO_TECHNICIAN|TECH_TIMEOUT|
LONG_DOWNTIME|RESOLVED|DAILY_SUMMARY, "role"?: ..., "incident", "diagnosis", "assignment", "parts", ...}.
Topic ARNs: SSM /af/notify/topics = {"OPERATOR": "arn:...", "TECHNICIAN": ..., ...}.
Every pushed notification is also appended to the incident timeline so the web shows who was told what.
"""

from __future__ import annotations

from typing import Any

import notifier
from af_shared import config
from af_shared.ddb import get_table, is_conditional_failure, now_iso
from af_shared.log import get_logger

logger = get_logger("notify")
_store = config.ParamStore()
_clients: dict[str, Any] = {}


def record(table: Any, payload: dict[str, Any], result: dict[str, Any], now: str) -> None:
    inc = payload.get("incident") or {}
    if not result["sent"] or not inc.get("incidentSK"):
        return
    events = [
        {"at": now, "event": "NOTIFIED", "role": s["role"], "channel": s["channel"], "kind": result["kind"]}
        for s in result["sent"]
    ]
    try:
        table.update_item(
            Key={"PK": f"MACHINE#{inc['machineId']}", "SK": inc["incidentSK"]},
            UpdateExpression="SET timeline = list_append(timeline, :ev)",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":ev": events},
        )
    except Exception as exc:
        if not is_conditional_failure(exc):
            raise


def run(payload: dict[str, Any], sns: Any, table: Any, store: config.ParamStore, now: str) -> dict[str, Any]:
    topics = store.get_json(config.NOTIFY_TOPICS, default={}) or {}
    result = notifier.notify(payload, sns, topics)
    record(table, payload, result, now)
    return result


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    if not _clients:
        import boto3

        _clients["sns"] = boto3.client("sns")
        _clients["table"] = get_table()
    inc = event.get("incident") or {}
    logger.append_keys(incidentId=inc.get("incidentId"), machineId=inc.get("machineId"))
    result = run(event, _clients["sns"], _clients["table"], _store, now_iso())
    logger.info("notifications", kind=result["kind"], sent=len(result["sent"]), skipped=result["skipped"])
    return result
