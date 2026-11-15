"""Diagnosis with Claude on Amazon Bedrock (Mantle client) using structured outputs."""

from __future__ import annotations

import os
from typing import Any

from af_shared import Diagnosis, DiagnosisRequest, DiagnosisResult, RoleMessages, ZoneAnalysis
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL_ID = "anthropic.claude-opus-5-5"
DEFAULT_FALLBACK_MODEL_ID = "anthropic.claude-opus-5"  # used by the SDK only if the main model refuses
DEFAULT_EFFORT = "medium"

SYSTEM_PROMPT = """Eres el asistente de mantenimiento predictivo de ArquiFusion para flotas de maquinaria pesada \
(minería, construcción, infraestructura).

Recibes en JSON una anomalía detectada por el motor de salud de una máquina: señales (métrica, valor, línea base, \
z-score y tendencia), historial reciente de incidentes y mantenimientos, contexto (lote de combustible, clima) y \
otras máquinas con incidentes en la misma zona durante las últimas 2 horas (zoneCorrelation).

Devuelve:
1. diagnosis: causa probable concreta, el componente más probable, tu confianza, la acción recomendada, si la \
máquina debe detenerse (sí cuando la gravedad es CRITICAL o hay riesgo de daño mayor) y los minutos estimados de \
reparación. Temperatura y vibración subiendo juntas suelen indicar rodamientos; presión hidráulica baja, fugas; \
consumo de combustible alto con temperatura, pérdida de eficiencia del motor.
2. zoneAnalysis: solo si zoneCorrelation tiene 2 o más máquinas. Compara lo que tienen en común (lote de \
combustible, modelo, clima, métrica afectada) y propone la causa común con evidencia concreta tomada de la \
entrada. En otro caso, null.
3. messages: un mensaje por rol, en español, de 1 a 2 frases:
   - OPERATOR: qué hacer ahora mismo (detener o continuar con precaución).
   - TECHNICIAN: diagnóstico, señales con su valor y línea base, repuestos o herramientas a llevar, máquina y zona.
   - MAINT_LEAD: gravedad, componente, tiempo estimado y si hay incidente de zona.
   - SUPERVISOR: impacto en la producción y máquinas afectadas en la zona.
   - MANAGER: resumen ejecutivo con impacto en disponibilidad y causa de zona si existe.

Usa solo datos presentes en la entrada; no inventes valores. Si los datos no permiten un diagnóstico claro, usa \
component UNKNOWN y confidence LOW."""


class LlmUnavailableError(RuntimeError):
    """The model refused, returned nothing parseable, or the call failed."""


class LlmDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    diagnosis: Diagnosis
    zone_analysis: ZoneAnalysis | None = Field(alias="zoneAnalysis")
    messages: RoleMessages


def build_client() -> Any:
    from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

    fallback = os.environ.get("AF_BEDROCK_FALLBACK_MODEL_ID", DEFAULT_FALLBACK_MODEL_ID)
    return AnthropicBedrockMantle(
        aws_region=os.environ.get("AF_BEDROCK_REGION") or os.environ.get("AWS_REGION", "us-east-1"),
        timeout=float(os.environ.get("AF_BEDROCK_TIMEOUT_S", "40")),
        max_retries=1,
        middleware=[BetaRefusalFallbackMiddleware([{"model": fallback}])],
    )


def diagnose(req: DiagnosisRequest, client: Any) -> DiagnosisResult:
    response = client.beta.messages.parse(
        model=os.environ.get("AF_BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": req.model_dump_json(by_alias=True, exclude_none=True)}],
        output_format=LlmDiagnosis,
        output_config={"effort": os.environ.get("AF_BEDROCK_EFFORT", DEFAULT_EFFORT)},
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise LlmUnavailableError(f"No structured output (stop_reason={response.stop_reason})")

    out: LlmDiagnosis = response.parsed_output
    return DiagnosisResult(
        source="BEDROCK",
        modelId=response.model,
        diagnosis=out.diagnosis,
        zoneAnalysis=out.zone_analysis,
        messages=out.messages,
    )
