"""Validate wire telemetry and map BigQuery's typed Parquet rows to the existing lake schema."""

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from af_shared import Telemetry
from jsonschema import Draft202012Validator, FormatChecker

from services.stream_exporter.lake_io import DataError


@cache
def validator():
    path = Path(__file__).resolve().parents[2] / "contracts" / "telemetry.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), format_checker=FormatChecker())


def telemetry_row(value):
    if not isinstance(value, dict) or not validator().is_valid(value):
        raise DataError("invalid telemetry schema")
    try:
        row = Telemetry.model_validate(value).model_dump(mode="json", by_alias=True)
    except ValueError:
        raise DataError("invalid telemetry values") from None
    context = {key: val for key, val in (row["context"] or {}).items() if val is not None}
    if context:
        row["context"] = context
    else:
        row.pop("context")
    return row


def contract_names(fields, schema):
    names = {name.lower(): name for name in schema["properties"]}
    result = {}
    for physical, value in fields.items():
        name = names.get(physical.lower(), physical)
        if name in result:
            raise DataError("ambiguous telemetry column names")
        child = schema["properties"].get(name, {})
        if isinstance(value, dict) and "properties" in child:
            value = contract_names(value, child)
        result[name] = value
    return result


def bigquery_row(record):
    # The BigQuery REST client returns TIMESTAMP as a UTC datetime and nested RECORD fields as dicts.
    row = contract_names(record, validator().schema)
    instant = row.get("timestamp")
    if not isinstance(instant, datetime) or instant.utcoffset() is None:
        raise DataError("BigQuery telemetry requires a TIMESTAMP column")
    row["timestamp"] = instant.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if row.get("context") is None:
        row.pop("context", None)
    elif isinstance(row["context"], dict):
        row["context"] = {key: val for key, val in row["context"].items() if val is not None}
    return telemetry_row(row)
