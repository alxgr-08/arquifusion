"""DynamoDB helpers shared by the Lambdas: Decimal conversion, time helpers, conditional-check detection."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

CONDITIONAL_FAILED = "ConditionalCheckFailedException"


def table_name() -> str:
    return os.environ.get("AF_TABLE_NAME", "af-dev-arquifusion")


def get_table(resource: Any | None = None) -> Any:
    """boto3 Table for AF_TABLE_NAME. boto3 is imported lazily so pure logic stays importable without it."""
    if resource is None:
        import boto3

        resource = boto3.resource("dynamodb")
    return resource.Table(table_name())


def to_ddb(value: Any) -> Any:
    """Recursively convert floats to Decimal (boto3 rejects float)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    """Recursively convert Decimal to int/float so results are JSON serialisable."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [from_ddb(v) for v in value]
    if isinstance(value, set):
        return sorted(from_ddb(v) for v in value)
    return value


def is_conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    return response.get("Error", {}).get("Code") == CONDITIONAL_FAILED


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(datetime.now(UTC))


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def minus(value: str, **delta: float) -> str:
    return iso(parse_iso(value) - timedelta(**delta))


def strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    """Drop storage keys (PK/SK/GSI*) and convert Decimals before returning an item to a client."""
    hidden = {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "expiresAt"}
    return from_ddb({k: v for k, v in item.items() if k not in hidden})


def transact_write(table: Any, actions: list[dict[str, Any]]) -> None:
    """TransactWriteItems on `table` with resource-style values (plain Python, like Table.update_item).

    Each action is {"ConditionCheck"|"Update"|"Put"|"Delete": {...}} without TableName. Raises the ClientError
    `TransactionCanceledException` if any condition fails (see `is_transaction_conditional_failure`).
    """
    from boto3.dynamodb.types import TypeSerializer

    ser = TypeSerializer()

    def typed(values: dict[str, Any]) -> dict[str, Any]:
        return {k: ser.serialize(to_ddb(v)) for k, v in values.items()}

    items = []
    for action in actions:
        ((kind, spec),) = action.items()
        spec = {**spec, "TableName": table.name}
        for field in ("Key", "Item", "ExpressionAttributeValues"):
            if field in spec:
                spec[field] = typed(spec[field])
        items.append({kind: spec})
    table.meta.client.transact_write_items(TransactItems=items)


def is_transaction_conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    if response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = response.get("CancellationReasons") or []
    return any(r.get("Code") == "ConditionalCheckFailed" for r in reasons) or (
        not reasons and "ConditionalCheckFailed" in str(response.get("Error", {}).get("Message", ""))
    )
