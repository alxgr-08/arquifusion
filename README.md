# ArquiFusion Fleet Intelligence

Serverless, multi-cloud platform (AWS + GCP) that turns heavy-machinery telemetry into maintenance, parts, technician, fuel and productivity decisions.

## Architecture (summary)

```
Simulator -> IoT Core -> IoT Rule -+-> Firehose -> S3 (Parquet) -> BigQuery + BQ ML (GCP) -> DynamoDB
                                   +-> health-evaluator -> DynamoDB
                                                        -> EventBridge (AnomalyDetected)
                                                             -> Step Functions (incidents)
                                                                  -> Bedrock · Location · SNS
Frontend (S3 + CloudFront + Cognito) -> API Gateway -> Lambda -> DynamoDB
Security: Secrets Manager · SSM Parameter Store · KMS · IAM
```

Full diagram: `docs/architecture.png` (Rol 1).

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
pytest -q
```

## Git workflow

- `main` is protected: no direct pushes. Branches: `feat/rol3-health-score`, `fix/rol2-...`, `infra/rol1-...`.
- Conventional Commits in English. Small PRs (< 300 lines), 1 review, squash merge.
- Tag issues with `rol:N` and `P1`–`P13`.
