# af-dev-bigquery-sync-back

- **Owner:** Rol 3
- **Trigger:** EventBridge Scheduler, daily, after the BigQuery scheduled queries (`analytics/run_pipeline.sh` order)
- **Input -> output:** `af_out.*` (BigQuery, GCP) -> DynamoDB `KPI#`, `MODEL# BASELINE`, `FORECAST#`
- **Problems:** P3, P6, P7, P8 · rubric: multi-cloud (GCP -> AWS direction)

| BigQuery | DynamoDB item | Read by |
|---|---|---|
| `daily_kpis` + `machine_clusters` + `fuel_trends` (last `AF_SYNC_DAYS`) | `MACHINE#{id}` / `KPI#{date}`, GSI1 `OP#{op}#KPI#{date}` | API `/operations/{op}/kpis` -> frontend KPIs |
| `model_baselines` | `MODEL#{model}` / `BASELINE` with `metrics.{metric}.{mean,std,n}` | health-evaluator (z_fleet) |
| `parts_forecast` + `parts_irregular` | `PART#{id}` / `FORECAST#{wh}#{isoWeek}` | preventive Step Function (reorder), API parts |

Writes are idempotent (`batch_writer` overwrites by PK/SK).

## Configuration

| Env var | Default |
|---|---|
| `AF_TABLE_NAME` | — |
| `AF_GCP_SECRET_ID` | `af/gcp/service-account` (service-account JSON in Secrets Manager) |
| `GCP_PROJECT_ID` | `project_id` from the secret |
| `BQ_LOCATION` | `US` |
| `AF_OPERATION_ID` | `MINA-NORTE` |
| `AF_SYNC_DAYS` | `7` |

Permissions: secretsmanager:GetSecretValue, dynamodb:BatchWriteItem (LabRole in Academy). Memory 512 MB, timeout 120 s.
`google-cloud-bigquery` is large: package it as a Lambda layer.

## How to test

```bash
python -m pytest -q services/bigquery_sync_back   # BigQuery faked, DynamoDB with moto
```
