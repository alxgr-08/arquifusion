"""Evaluate telemetry and deliver durable anomaly events from the regional table."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import boto3
from af_shared import AnomalyDetected, Telemetry, keys
from af_shared.ddb import is_transaction_conditional_failure, parse_iso, transact_write
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError
from pydantic import ValidationError
from scoring import Evaluation, FleetBaseline, HealthConfig, MachineState, MetricBaseline, evaluate

logger = Logger(service="health-evaluator")

READING_TTL = timedelta(hours=48)
CONFIG_CACHE_SECONDS = 300
_cache: dict[str, Any] = {}


def _table():
    if "table" not in _cache:
        _cache["table"] = boto3.resource("dynamodb").Table(os.environ["AF_TABLE_NAME"])
    return _cache["table"]


def _events():
    if "events" not in _cache:
        _cache["events"] = boto3.client("events")
    return _cache["events"]


def _ssm_json(name: str) -> dict[str, Any] | None:
    try:
        value = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        return json.loads(value)
    except ClientError as err:
        if err.response["Error"]["Code"] != "ParameterNotFound":
            raise
        logger.warning("SSM parameter not found, using defaults", extra={"parameter": name})
        return None


def load_config() -> HealthConfig:
    cached = _cache.get("config")
    if cached and time.monotonic() - cached[0] < CONFIG_CACHE_SECONDS:
        return cached[1]
    config = HealthConfig.from_params(
        _ssm_json(os.environ.get("AF_SSM_HEALTH_WEIGHTS", "/af/health/weights")),
        _ssm_json(os.environ.get("AF_SSM_HEALTH_THRESHOLDS", "/af/health/thresholds")),
    )
    _cache["config"] = (time.monotonic(), config)
    return config


def to_ddb(value: Any) -> Any:
    return json.loads(json.dumps(value), parse_float=Decimal)


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_context(machine_id: str, model: str) -> tuple[dict[str, Any] | None, dict[str, FleetBaseline]]:
    table = _table()
    profile = table.get_item(Key=keys.machine_profile(machine_id), ConsistentRead=True).get("Item")
    model_item = table.get_item(Key=keys.model_baseline_keys(model)).get("Item") or {}
    fleet = {
        metric: FleetBaseline(mean=float(v["mean"]), std=float(v["std"]))
        for metric, v in model_item.get("metrics", {}).items()
    }
    return (from_ddb(profile) if profile else None), fleet


def state_from_profile(profile: dict[str, Any] | None) -> MachineState:
    if not profile:
        return MachineState()
    return MachineState(
        baselines={m: MetricBaseline.from_dict(b) for m, b in profile.get("baseline", {}).items()},
        severity=profile.get("severity", "OK"),
        anomaly_streak=int(profile.get("anomalyStreak", 0)),
    )


def persist(
    msg: Telemetry, profile: dict[str, Any] | None, ev: Evaluation, alert: AnomalyDetected | None = None
) -> bool:
    """Atomically store the reading and update the profile. False if duplicate or out of order.

    With `alert`, the profile also keeps `pendingAlert` (outbox) until EventBridge accepts the event.
    """
    ts = _iso(msg.timestamp)
    metrics = msg.metrics.model_dump(by_alias=True)
    reading = {
        "PK": keys.machine_pk(msg.machine_id),
        "SK": keys.reading_sk(ts),
        "messageId": str(msg.message_id),
        "operationId": msg.operation_id,
        "status": msg.status,
        "metrics": metrics,
        "healthScore": ev.health_score,
        "severity": ev.severity,
        "expiresAt": int((msg.timestamp + READING_TTL).timestamp()),
    }
    if msg.context:
        context = msg.context.model_dump(by_alias=True, exclude_none=True)
        if context:
            reading["context"] = context

    values: dict[str, Any] = {
        ":model": msg.model,
        ":type": msg.machine_type,
        ":op": msg.operation_id,
        ":zone": msg.location.zone_id,
        ":lat": msg.location.lat,
        ":lon": msg.location.lon,
        ":status": msg.status,
        ":health": ev.health_score,
        ":sev": ev.severity,
        ":streak": ev.state.anomaly_streak,
        ":ts": ts,
        ":hours": metrics["engineHours"],
        ":baseline": {m: b.to_dict() for m, b in ev.state.baselines.items()},
    }
    sets = [
        "model = :model",
        "machineType = :type",
        "operationId = :op",
        "zoneId = :zone",
        "lat = :lat",
        "lon = :lon",
        "#status = :status",
        "healthScore = :health",
        "severity = :sev",
        "anomalyStreak = :streak",
        "lastSeen = :ts",
        "baseline = :baseline",
    ]
    if metrics["engineHours"] is not None:
        sets.append("engineHours = :hours")
    else:
        values.pop(":hours")
    if alert:
        sets.append("pendingAlert = :alert")
        values[":alert"] = {
            "alertId": str(msg.message_id),
            "detail": alert.detail.model_dump(mode="json", by_alias=True),
        }
    if not profile or profile.get("status") != msg.status or profile.get("operationId") != msg.operation_id:
        sets += ["GSI1PK = :gpk", "GSI1SK = :gsk"]
        gsi = keys.machine_gsi1(msg.operation_id, msg.status, msg.machine_id)
        values |= {":gpk": gsi["GSI1PK"], ":gsk": gsi["GSI1SK"]}

    observed = (profile or {}).get("lastSeen")
    if observed and parse_iso(observed) >= msg.timestamp:
        return False
    condition = "attribute_not_exists(lastSeen)"
    if observed:
        values[":observed"] = observed
        # Compare instants above. The exact version check also supports legacy second-only timestamps.
        condition = "lastSeen = :observed"
    if not profile:
        owner_condition = "attribute_not_exists(PK)"
    elif profile.get("operationId"):
        values[":owner"] = profile["operationId"]
        owner_condition = "attribute_exists(PK) AND operationId = :owner"
    else:
        owner_condition = "attribute_exists(PK) AND attribute_not_exists(operationId)"
    condition = f"({condition}) AND ({owner_condition})"
    actions = [
        {"Put": {"Item": reading, "ConditionExpression": "attribute_not_exists(SK)"}},
        {
            "Update": {
                "Key": keys.machine_profile(msg.machine_id),
                "UpdateExpression": "SET " + ", ".join(sets),
                "ConditionExpression": condition,
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": values,
            }
        },
    ]
    if alert:
        alert_id = str(msg.message_id)
        pending = (profile or {}).get("pendingAlert")
        if pending and pending["alertId"] != alert_id:
            # PROFILE-only versions must acquire a durable row before a new escalation replaces their pointer.
            previous = AnomalyDetected.model_validate({"detail": pending["detail"]}).detail
            if previous.machine_id != msg.machine_id:
                raise ValueError("Pending alert belongs to another machine")
            actions.append(
                {
                    "Update": {
                        "Key": outbox_key(msg.machine_id, pending["alertId"]),
                        "UpdateExpression": "SET alertId = :id, operationId = :op, #detail = :detail, "
                        "GSI1PK = :gpk, GSI1SK = :gsk",
                        "ConditionExpression": "attribute_not_exists(SK) OR "
                        "(alertId = :id AND operationId = :op AND #detail = :detail)",
                        "ExpressionAttributeNames": {"#detail": "detail"},
                        "ExpressionAttributeValues": {
                            ":id": pending["alertId"],
                            ":op": previous.operation_id,
                            ":detail": pending["detail"],
                            ":gpk": "OUTBOX#ANOMALY",
                            ":gsk": f"{previous.operation_id}#{msg.machine_id}#{pending['alertId']}",
                        },
                    }
                }
            )
        actions.append(
            {
                "Put": {
                    "Item": {
                        **outbox_key(msg.machine_id, alert_id),
                        **values[":alert"],
                        "operationId": msg.operation_id,
                        "GSI1PK": "OUTBOX#ANOMALY",
                        "GSI1SK": f"{msg.operation_id}#{msg.machine_id}#{alert_id}",
                    },
                    "ConditionExpression": "attribute_not_exists(SK)",
                }
            }
        )
    try:
        transact_write(_table(), to_ddb(actions))
    except ClientError as err:
        if is_transaction_conditional_failure(err):
            current = _table().get_item(Key=keys.machine_profile(msg.machine_id), ConsistentRead=True).get("Item", {})
            if current.get("lastSeen") and parse_iso(current["lastSeen"]) >= msg.timestamp:
                return False
        # A concurrent older reading changed the baseline, or the service rejected the write.
        # The invocation must retry against the fresh profile.
        raise
    return True


def build_event(msg: Telemetry, ev: Evaluation, detected_at: datetime) -> AnomalyDetected:
    return AnomalyDetected.model_validate(
        {
            "detail": {
                "schemaVersion": "1.0",
                "machineId": msg.machine_id,
                "operationId": msg.operation_id,
                "zoneId": msg.location.zone_id,
                "model": msg.model,
                "detectedAt": _iso(detected_at),
                "healthScore": ev.health_score,
                "severity": ev.severity,
                "signals": [s.to_contract() for s in ev.signals],
                "comparedAgainst": ev.compared_against,
            }
        }
    )


def publish(event: AnomalyDetected) -> None:
    response = _events().put_events(
        Entries=[
            {
                "Source": event.source,
                "DetailType": event.detail_type,
                "Detail": event.detail.model_dump_json(by_alias=True),
                "EventBusName": os.environ.get("AF_EVENT_BUS", "default"),
            }
        ]
    )
    entries = response.get("Entries", [])
    if response.get("FailedEntryCount"):
        raise RuntimeError("EventBridge rejected the anomaly")
    if response.get("FailedEntryCount") != 0 or len(entries) != 1 or not entries[0].get("EventId"):
        raise RuntimeError("EventBridge did not confirm the anomaly")
    logger.info(
        "EventBridge accepted anomaly",
        extra={
            "eventId": entries[0]["EventId"],
            "machineId": event.detail.machine_id,
            "operationId": event.detail.operation_id,
        },
    )


def outbox_key(machine_id: str, alert_id: str) -> dict[str, str]:
    return {"PK": keys.machine_pk(machine_id), "SK": f"OUTBOX#ANOMALY#{UUID(alert_id)}"}


def clear_pending_alert(machine_id: str, alert_id: str, operation_id: str) -> None:
    try:
        _table().update_item(
            Key=keys.machine_profile(machine_id),
            UpdateExpression="REMOVE pendingAlert",
            ConditionExpression="pendingAlert.alertId = :id",
            ExpressionAttributeValues={":id": alert_id},
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    # A newer alert may already occupy PROFILE. Remove only this accepted event.
    try:
        _table().delete_item(
            Key=outbox_key(machine_id, alert_id),
            ConditionExpression="operationId = :op",
            ExpressionAttributeValues={":op": operation_id},
        )
    except _table().meta.client.exceptions.ConditionalCheckFailedException:
        pass  # legacy PROFILE or another delivery already removed the durable row


def process(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = Telemetry.model_validate(payload)
    except ValidationError as err:
        logger.error("Invalid telemetry", extra={"errors": err.errors(include_url=False, include_input=False)[:5]})
        return {"result": "INVALID"}
    if msg.timestamp.utcoffset() is None:
        logger.error("Invalid telemetry timestamp", extra={"field": "timestamp", "reason": "timezone required"})
        return {"result": "INVALID"}

    logger.append_keys(machineId=msg.machine_id)
    profile, fleet = load_context(msg.machine_id, msg.model)
    if profile and profile.get("operationId") not in (None, msg.operation_id):
        logger.error("Telemetry operation does not own the machine")
        return {"result": "INVALID"}
    ev = evaluate(
        msg.metrics.model_dump(by_alias=True),
        state_from_profile(profile),
        fleet=fleet,
        status=msg.status,
        config=load_config(),
    )
    event = build_event(msg, ev, datetime.now(UTC)) if ev.should_emit else None
    saved = persist(msg, profile, ev, event)

    # Persist new telemetry before attempting the bus. Every failed delivery remains in its own durable row.
    deliveries = {}
    pending = (profile or {}).get("pendingAlert")
    if pending:
        deliveries[pending["alertId"]] = pending["detail"]["operationId"]
    if event or not saved:
        # A replay can outlive PROFILE.pendingAlert, including after a newer escalation replaces the pointer.
        deliveries[str(msg.message_id)] = msg.operation_id
    first_error = None
    for alert_id, operation_id in deliveries.items():
        try:
            outcome = reconcile(msg.machine_id, alert_id, operation_id)
            if outcome == "ACCEPTED":
                logger.warning("AnomalyDetected accepted", extra={"alertId": alert_id})
        except Exception as err:
            logger.warning(
                "Anomaly delivery remains pending", extra={"alertId": alert_id, "errorType": type(err).__name__}
            )
            if first_error is None:
                first_error = err
    if first_error is not None:
        raise first_error

    if not saved:
        logger.info("Duplicate or out-of-order reading skipped")
        return {"result": "SKIPPED"}

    return {"result": "OK", "healthScore": ev.health_score, "severity": ev.severity, "emitted": ev.should_emit}


def reconcile(machine_id: str, alert_id: str, operation_id: str) -> str:
    key = outbox_key(machine_id, alert_id)
    pending = _table().get_item(Key=key, ConsistentRead=True).get("Item")
    if not pending:
        profile = _table().get_item(Key=keys.machine_profile(machine_id), ConsistentRead=True).get("Item", {})
        legacy = profile.get("pendingAlert") or {}
        if legacy.get("alertId") != alert_id:
            return "ALREADY_DELIVERED_OR_ABSENT"
        pending = {**legacy, "operationId": legacy["detail"]["operationId"]}
    event = AnomalyDetected.model_validate({"detail": from_ddb(pending["detail"])})
    if (pending["operationId"], event.detail.operation_id, event.detail.machine_id, pending["alertId"]) != (
        operation_id,
        operation_id,
        machine_id,
        alert_id,
    ):
        raise ValueError("Outbox identity disagrees with its source")
    publish(event)
    clear_pending_alert(machine_id, alert_id, operation_id)
    return "ACCEPTED"


@logger.inject_lambda_context
def handler(event: dict[str, Any] | list[dict[str, Any]], context: object) -> dict[str, Any]:
    """Direct telemetry, one operator-selected recovery, or a filtered DynamoDB stream."""
    if isinstance(event, dict) and "reconcileAlert" in event:
        selected = event["reconcileAlert"]
        return {"result": reconcile(selected["machineId"], selected["messageId"], selected["operationId"])}
    if isinstance(event, dict) and "Records" in event:
        prefix = _table().table_arn + "/stream/"
        for record in event["Records"]:
            if record.get("eventSource") != "aws:dynamodb" or not record.get("eventSourceARN", "").startswith(prefix):
                raise ValueError("Unexpected outbox stream")
            data = record["dynamodb"]
            if record["eventName"] == "INSERT" and data["Keys"]["SK"]["S"].startswith("OUTBOX#ANOMALY#"):
                try:
                    reconcile(
                        data["Keys"]["PK"]["S"].removeprefix("MACHINE#"),
                        data["Keys"]["SK"]["S"].removeprefix("OUTBOX#ANOMALY#"),
                        data["NewImage"]["operationId"]["S"],
                    )
                except Exception:
                    logger.exception("Outbox delivery pending", extra={"sequence": data["SequenceNumber"]})
                    return {"batchItemFailures": [{"itemIdentifier": data["SequenceNumber"]}]}
        return {"batchItemFailures": []}
    messages = event if isinstance(event, list) else [event]
    results = [process(m) for m in messages]
    return results[0] if len(results) == 1 else {"results": results}
