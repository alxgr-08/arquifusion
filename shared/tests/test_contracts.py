import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from af_shared import AnomalyDetected, Telemetry
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"
TELEMETRY_SCHEMA = json.loads((EXAMPLES.parent / "telemetry.schema.json").read_text(encoding="utf-8"))
TELEMETRY_VALIDATOR = Draft202012Validator(TELEMETRY_SCHEMA, format_checker=FormatChecker())


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("telemetry.*.json")), ids=lambda p: p.name)
def test_telemetry_examples_parse(path: Path) -> None:
    Telemetry.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_anomaly_example_parses() -> None:
    event = AnomalyDetected.model_validate(json.loads((EXAMPLES / "anomaly-detected.json").read_text(encoding="utf-8")))
    assert event.detail.severity == "WARNING"


def test_null_metric_is_sensor_failure_not_zero() -> None:
    msg = Telemetry.model_validate(json.loads((EXAMPLES / "telemetry.sensor-null.json").read_text(encoding="utf-8")))
    assert msg.metrics.temperature_c is None
    assert msg.metrics.load_tons == 0


def test_ai_diagnosis_examples_parse() -> None:
    from af_shared import DiagnosisRequest, DiagnosisResult

    req = DiagnosisRequest.model_validate(
        json.loads((EXAMPLES / "ai-diagnosis-request.json").read_text(encoding="utf-8"))
    )
    res = DiagnosisResult.model_validate(
        json.loads((EXAMPLES / "ai-diagnosis-response.json").read_text(encoding="utf-8"))
    )
    assert len(req.zone_correlation) == 2
    assert res.diagnosis.component == "BEARING"


@pytest.mark.parametrize("machine_type", TELEMETRY_SCHEMA["properties"]["machineType"]["enum"])
def test_telemetry_accepts_each_canonical_machine_type(machine_type):
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["machineType"] = machine_type
    TELEMETRY_VALIDATOR.validate(payload)
    assert Telemetry.model_validate(payload).machine_type == machine_type


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("operationId",), ""),
        (("operationId",), "mina-norte"),
        (("operationId",), "MINA/NORTE"),
        (("operationId",), "MINA#OTHER"),
        (("machineType",), "UNRECOGNIZED"),
        (("timestamp",), "2026-11-13T12:00:00"),
        (("timestamp",), "2026-11-13T12:00:00+00:00"),
        (("timestamp",), 1794571200),
        (("timestamp",), True),
        (("context",), None),
        (("context", "fuelBatchId"), None),
        (("context", "ambientTempC"), None),
    ]
    + [
        (("metrics", name), value)
        for name in TELEMETRY_SCHEMA["properties"]["metrics"]["properties"]
        for value in (True, "0")
    ]
    + [(("location", name), value) for name in ("lat", "lon") for value in (False, "0")]
    + [(("context", "ambientTempC"), value) for value in (True, "0")],
)
def test_telemetry_model_rejects_values_rejected_by_the_wire_schema(path, value):
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    parent = payload
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = value
    assert not TELEMETRY_VALIDATOR.is_valid(payload)
    with pytest.raises(ValidationError):
        Telemetry.model_validate(payload)


@pytest.mark.parametrize("instant", ["2026-11-13T12:00:00Z", "2026-11-13T12:00:00.123456Z", "2026-11-13t12:00:00.01Z"])
def test_wire_timestamps_keep_utc_and_fractional_precision(instant):
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["timestamp"] = instant
    TELEMETRY_VALIDATOR.validate(payload)
    assert Telemetry.model_validate(payload).timestamp == datetime.fromisoformat(instant)


@pytest.mark.parametrize("instant", ["2026-11-13 12:00:00Z", "2026-02-30T12:00:00Z"])
def test_bad_rfc3339_dates_are_rejected_even_without_optional_jsonschema_format_packages(instant):
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["timestamp"] = instant
    with pytest.raises(ValidationError):
        Telemetry.model_validate(payload)


def test_programmatic_aware_datetime_normalizes_to_utc_and_naive_is_rejected():
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["timestamp"] = datetime(2026, 11, 13, 7, 0, tzinfo=timezone(timedelta(hours=-5)))
    parsed = Telemetry.model_validate(payload)
    assert parsed.timestamp == datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    assert parsed.model_dump(mode="json", by_alias=True)["timestamp"] == "2026-11-13T12:00:00Z"
    payload["timestamp"] = payload["timestamp"].replace(tzinfo=None)
    with pytest.raises(ValidationError):
        Telemetry.model_validate(payload)


def test_open_context_properties_are_preserved_and_context_can_be_omitted():
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["context"] = {"sensor": {"calibrated": True, "tags": ["A", None]}, "optionalExtra": None}
    TELEMETRY_VALIDATOR.validate(payload)
    parsed = Telemetry.model_validate(payload)
    assert parsed.context.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload["context"]
    TELEMETRY_VALIDATOR.validate(parsed.model_dump(mode="json", by_alias=True, exclude_unset=True))
    payload.pop("context")
    assert Telemetry.model_validate(payload).context is None


@pytest.mark.parametrize("number", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_numbers_cannot_become_telemetry(number):
    payload = json.loads((EXAMPLES / "telemetry.ok.json").read_text(encoding="utf-8"))
    payload["metrics"]["temperatureC"] = number
    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)
    with pytest.raises(ValidationError):
        Telemetry.model_validate(payload)
