"""af-{env}-health-evaluator: IoT Rule -> health score -> DynamoDB (+ AnomalyDetected on EventBridge)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from af_shared import AnomalyDetected, Telemetry, keys
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
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def persist(msg: Telemetry, profile: dict[str, Any] | None, ev: Evaluation) -> bool:
    """Atomically store the reading and update the profile. False if duplicate or out of order."""
    ts = _iso(msg.timestamp)
    metrics = msg.metrics.model_dump(by_alias=True)
    reading = {
        "PK": keys.machine_pk(msg.machine_id),
        "SK": keys.reading_sk(ts),
        "messageId": str(msg.message_id),
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
    if not profile or profile.get("status") != msg.status:  # GSI1SK only changes with status (fewer index writes)
        sets += ["GSI1PK = :gpk", "GSI1SK = :gsk"]
        gsi = keys.machine_gsi1(msg.operation_id, msg.status, msg.machine_id)
        values |= {":gpk": gsi["GSI1PK"], ":gsk": gsi["GSI1SK"]}

    client = _table().meta.client
    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": _table().name,
                        "Item": to_ddb(reading),
                        "ConditionExpression": "attribute_not_exists(SK)",
                    }
                },
                {
                    "Update": {
                        "TableName": _table().name,
                        "Key": keys.machine_profile(msg.machine_id),
                        "UpdateExpression": "SET " + ", ".join(sets),
                        "ConditionExpression": "attribute_not_exists(lastSeen) OR lastSeen < :ts",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": to_ddb(values),
                    }
                },
            ]
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "TransactionCanceledException":
            return False
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
    if response.get("FailedEntryCount"):
        raise RuntimeError(f"EventBridge rejected AnomalyDetected: {response['Entries']}")


def process(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = Telemetry.model_validate(payload)
    except ValidationError as err:
        logger.error("Invalid telemetry", extra={"errors": err.errors(include_url=False)[:5]})
        return {"result": "INVALID"}

    logger.append_keys(machineId=msg.machine_id)
    profile, fleet = load_context(msg.machine_id, msg.model)
    ev = evaluate(
        msg.metrics.model_dump(by_alias=True),
        state_from_profile(profile),
        fleet=fleet,
        status=msg.status,
        config=load_config(),
    )
    if not persist(msg, profile, ev):
        logger.info("Duplicate or out-of-order reading skipped")
        return {"result": "SKIPPED"}

    if ev.should_emit:
        event = build_event(msg, ev, datetime.now(UTC))
        publish(event)
        logger.warning("AnomalyDetected published", extra={"severity": ev.severity, "healthScore": ev.health_score})

    return {"result": "OK", "healthScore": ev.health_score, "severity": ev.severity, "emitted": ev.should_emit}


@logger.inject_lambda_context
def handler(event: dict[str, Any] | list[dict[str, Any]], context: object) -> dict[str, Any]:
    """IoT Rule `SELECT * FROM 'arquifusion/+/+/telemetry'` delivers one message; a list is accepted for batches."""
    messages = event if isinstance(event, list) else [event]
    results = [process(m) for m in messages]
    return results[0] if len(results) == 1 else {"results": results}
