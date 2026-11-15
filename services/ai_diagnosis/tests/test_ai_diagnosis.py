import copy
import json
from pathlib import Path
from types import SimpleNamespace

import handler
import llm
import pytest
import rules
from af_shared import DiagnosisRequest
from anthropic.lib._parse._transform import to_json_output_format
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
REQUEST = json.loads((CONTRACTS / "examples" / "ai-diagnosis-request.json").read_text(encoding="utf-8"))
RESPONSE_SCHEMA = json.loads((CONTRACTS / "ai-diagnosis-response.schema.json").read_text(encoding="utf-8"))


def request(signals: list[tuple[str, float]], severity: str = "WARNING", zone: bool = False, **extra) -> dict:
    payload = copy.deepcopy(REQUEST)
    payload["anomaly"]["severity"] = severity
    payload["anomaly"]["signals"] = [
        {"metric": m, "value": 1.0, "baseline": 0.5, "zScore": z, "trend": "RISING"} for m, z in signals
    ]
    if not zone:
        payload.pop("zoneCorrelation")
    payload.update(extra)
    return payload


def assert_contract(result_json: dict) -> None:
    errors = list(Draft202012Validator(RESPONSE_SCHEMA).iter_errors(result_json))
    assert not errors, errors


# --- rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "component"),
    [
        ([("temperatureC", 3.1), ("vibrationMmS", 2.6)], "BEARING"),
        ([("pressureBar", 3.0)], "HYDRAULIC_SYSTEM"),
        ([("temperatureC", 3.0)], "COOLING_SYSTEM"),
        ([("fuelRateLph", 2.5)], "FUEL_SYSTEM"),
        ([("fuelRateLph", 2.5), ("temperatureC", 2.2)], "ENGINE"),
        ([("vibrationMmS", 4.0)], "DRIVETRAIN"),
        ([("temperatureC", 1.2), ("vibrationMmS", 1.1)], "COOLING_SYSTEM"),  # no strong signal -> top metric
    ],
)
def test_rules_component(signals, component) -> None:
    result = rules.diagnose(DiagnosisRequest.model_validate(request(signals)))
    assert result.diagnosis.component == component
    assert result.zone_analysis is None
    assert_contract(result.model_dump(mode="json", by_alias=True))


def test_critical_stops_machine_and_tells_operator() -> None:
    result = rules.diagnose(DiagnosisRequest.model_validate(request([("vibrationMmS", 5.0)], severity="CRITICAL")))
    assert result.diagnosis.stop_machine
    assert result.messages.OPERATOR.startswith("Detén")


def test_zone_incident_detects_shared_fuel_batch() -> None:
    result = rules.diagnose(DiagnosisRequest.model_validate(REQUEST))
    assert result.zone_analysis is not None
    assert result.zone_analysis.is_zone_incident
    assert "FB-1120" in result.zone_analysis.common_cause
    assert "FB-1120" in result.messages.MANAGER
    assert_contract(result.model_dump(mode="json", by_alias=True))


def test_zone_incident_same_model_without_fuel_batch() -> None:
    payload = copy.deepcopy(REQUEST)
    payload.pop("context")
    for m in payload["zoneCorrelation"]:
        m.pop("fuelBatchId")
        m["model"] = "EXC-320"
    result = rules.diagnose(DiagnosisRequest.model_validate(payload))
    assert "EXC-320" in result.zone_analysis.common_cause


def test_recurrence_is_mentioned() -> None:
    history = [{"type": "INCIDENT", "at": "2026-11-01T10:00:00Z", "summary": "Bearing overheating"}]
    result = rules.diagnose(DiagnosisRequest.model_validate(request([("temperatureC", 3.0)], history=history)))
    assert "Reincidencia" in result.diagnosis.probable_cause


# --- LLM path --------------------------------------------------------------------------------


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response, self._error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def llm_response(stop_reason: str = "end_turn") -> SimpleNamespace:
    expected = json.loads((CONTRACTS / "examples" / "ai-diagnosis-response.json").read_text(encoding="utf-8"))
    parsed = llm.LlmDiagnosis.model_validate({k: expected[k] for k in ("diagnosis", "zoneAnalysis", "messages")})
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=None if stop_reason == "refusal" else parsed,
        model="anthropic.claude-opus-5-5",
    )


@pytest.fixture
def bedrock(monkeypatch):
    monkeypatch.setenv("AF_AI_PROVIDER", "bedrock")
    handler._cache.clear()
    yield lambda client: handler._cache.__setitem__("client", client)
    handler._cache.clear()


def test_llm_result_is_used_and_request_is_well_formed(bedrock) -> None:
    client = FakeClient(llm_response())
    bedrock(client)
    result = handler.run(REQUEST)

    assert result.source == "BEDROCK"
    assert result.model_id == "anthropic.claude-opus-5-5"
    assert_contract(result.model_dump(mode="json", by_alias=True))
    call = client.calls[0]
    assert call["model"] == "anthropic.claude-opus-5-5"
    assert call["output_format"] is llm.LlmDiagnosis
    assert call["output_config"] == {"effort": "medium"}
    assert json.loads(call["messages"][0]["content"])["anomaly"]["machineId"] == "EXC-0042"


@pytest.mark.parametrize(
    "client", [FakeClient(error=TimeoutError("bedrock down")), FakeClient(llm_response("refusal"))]
)
def test_llm_failure_falls_back_to_rules(bedrock, client) -> None:
    bedrock(client)
    assert handler.run(REQUEST).source == "RULES"


def test_rules_provider_skips_llm(monkeypatch) -> None:
    monkeypatch.setenv("AF_AI_PROVIDER", "rules")
    handler._cache.clear()
    assert handler.run(REQUEST).source == "RULES"


def test_output_schema_is_accepted_by_sdk() -> None:
    schema = to_json_output_format(llm.LlmDiagnosis)["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"diagnosis", "zoneAnalysis", "messages"}


class LambdaContext:
    function_name = "af-test-ai-diagnosis"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:af-test-ai-diagnosis"
    aws_request_id = "req-1"


def test_lambda_entrypoint_returns_contract_json(monkeypatch) -> None:
    monkeypatch.setenv("AF_AI_PROVIDER", "rules")
    assert_contract(handler.handler(REQUEST, LambdaContext()))
