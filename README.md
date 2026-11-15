# ArquiFusion Fleet Intelligence

Serverless, multi-cloud platform (AWS + GCP) that turns heavy-machinery telemetry into maintenance, parts, technician, fuel and productivity decisions.

## Team

| Role | Member | Owns |
|---|---|---|
| Rol 1 · Cloud architect & infra | Leonardo (`leonardopanduro-rgb`) | `infra/`, security, CI/CD, lake-to-bigquery, stream-exporter, diagram |
| Rol 2 · Backend & orchestration | Sebastián (`sebastianperez72`) | simulator, fault-injector, offline-detector, `statemachines/`, incident-tasks, assign-technician, notify, api |
| Rol 3 · Data, AI & product | Alexis (`alxgr-08`) | health-evaluator, ai-diagnosis, `analytics/`, bigquery-sync-back, `frontend/`, pitch |

New here? Start with [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Arquitectura vigente

AWS Academy opera la telemetría e incidentes. Google Cloud analiza el histórico con BigQuery ML. El simulador direct alimenta el evaluador y el archivo Parquet. La API utiliza Google Sign-In, un JWT authorizer y concesiones por operación en DynamoDB. El recorrido manual de incidentes y la analítica con histórico sintético tienen evidencia independiente.

[Arquitectura, madurez por componente y ensayo pendiente](docs/arquitectura.md) · [Diagrama editable](docs/architecture.drawio) · [PNG](docs/architecture.png) · [Relevo actual del 15/11](docs/revision-nocturna-2026-11-15.md)

## Repository layout

| Path | Content | Owner |
|---|---|---|
| `contracts/` | JSON Schemas + examples | All (PR approved by 3) |
| `infra/aws`, `infra/gcp` | Terraform | Rol 1 |
| `services/` | One folder per Lambda | See each README |
| `shared/` | Pydantic contract models, shared helpers | All |
| `statemachines/` | Step Functions ASL | Rol 2 |
| `analytics/` | BigQuery SQL + BigQuery ML | Rol 3 |
| `frontend/` | React + Vite + TS | Rol 3 |
| `docs/` | Architecture, decisions, pitch script | All |

## Local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python scripts/run_tests.py
```

## Git workflow

- `main` is protected: no direct pushes. Branches: `feat/rol3-health-score`, `fix/rol2-...`, `infra/rol1-...`.
- Conventional Commits in English. Small PRs (< 300 lines), 1 review, squash merge.
- Tag issues with `rol:N` and `P1`–`P13`.
