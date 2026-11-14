"""af-{env}-ai-diagnosis: incident Step Function task. Claude on Bedrock, deterministic rules as fallback."""

from __future__ import annotations

import os
from typing import Any

import llm
import rules
from af_shared import DiagnosisRequest, DiagnosisResult
from aws_lambda_powertools import Logger

logger = Logger(service="ai-diagnosis")
_cache: dict[str, Any] = {}


def _client() -> Any:
    if "client" not in _cache:
        _cache["client"] = llm.build_client()
    return _cache["client"]


def run(payload: dict[str, Any]) -> DiagnosisResult:
    req = DiagnosisRequest.model_validate(payload)
    logger.append_keys(machineId=req.anomaly.machine_id, incidentId=req.incident_id)

    if os.environ.get("AF_AI_PROVIDER", "bedrock").lower() == "bedrock":
        try:
            return llm.diagnose(req, _client())
        except Exception:  # noqa: BLE001 - any LLM failure must degrade to rules, never break the incident flow
            logger.exception("LLM diagnosis failed, using rules")
    return rules.diagnose(req)


@logger.inject_lambda_context
def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    result = run(event)
    logger.info("Diagnosis ready", extra={"source": result.source, "component": result.diagnosis.component})
    return result.model_dump(mode="json", by_alias=True)
