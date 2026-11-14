import json
from pathlib import Path

import pytest
from af_shared import AnomalyDetected, Telemetry

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"


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
