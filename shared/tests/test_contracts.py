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
