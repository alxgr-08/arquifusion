# af-dev-ai-diagnosis

- **Owner:** Rol 3
- **Trigger:** incident Step Function (`AIDiagnosis` state)
- **Input -> output:** `contracts/ai-diagnosis-request` -> `contracts/ai-diagnosis-response`
- **Problems:** P1, P9, P11

## How it works

1. Validates the request (anomaly + history + zone correlation) with `af_shared.DiagnosisRequest`.
2. `llm.py`: Claude on Amazon Bedrock (`AnthropicBedrockMantle`, model `anthropic.claude-opus-5-5`) with structured
   output (`messages.parse` + pydantic). Returns the probable cause, component, action, zone common cause and one
   message per role (OPERATOR, TECHNICIAN, MAINT_LEAD, SUPERVISOR, MANAGER), in Spanish.
   The SDK refusal-fallback middleware retries on `anthropic.claude-opus-5` if the main model declines.
3. `rules.py`: deterministic diagnosis used when Bedrock fails, times out, refuses, or `AF_AI_PROVIDER=rules`.
   The incident flow never breaks because of the AI step; `source` tells which path answered.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `AF_AI_PROVIDER` | `bedrock` | `rules` to skip the LLM (e.g. Bedrock not enabled in the lab) |
| `AF_BEDROCK_MODEL_ID` | `anthropic.claude-opus-5-5` | Must be enabled in the Bedrock console |
| `AF_BEDROCK_FALLBACK_MODEL_ID` | `anthropic.claude-opus-5` | Used only on refusals |
| `AF_BEDROCK_EFFORT` | `medium` | `low` for lower latency |
| `AF_BEDROCK_REGION` | `AWS_REGION` | |
| `AF_BEDROCK_TIMEOUT_S` | `40` | Lambda timeout should be >= 60 s |

Permissions: Bedrock model invocation (LabRole in Academy). Memory 512 MB.

## How to test

```bash
python -m pytest -q services/ai_diagnosis
```

En el despliegue de Academy se seleccionó `AF_AI_PROVIDER=rules`. La evidencia de BigQuery ML corresponde al histórico analítico. La invocación de Bedrock requiere disponibilidad y permisos propios. [Arquitectura](../../docs/arquitectura.md)
