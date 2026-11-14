# analytics — owner: Rol 3

BigQuery + BigQuery ML (GCP): the analytical half of the platform. Results flow back to DynamoDB through
`services/bigquery_sync_back`, so the second cloud adds value in both directions.

## Pipeline (run in order; daily as BigQuery scheduled queries)

| File | Output | What | Problem |
|---|---|---|---|
| `sql/00_setup.sql` | datasets, `af_lake.*` | Reference DDL (Rol 1 owns it in Terraform) | — |
| `sql/10_daily_kpis.sql` | `af_out.daily_kpis` | Utilization, idle/overuse hours, tons, L/t vs model, cost per productive hour | P6, P7 |
| `sql/20_model_baselines.sql` | `af_out.model_baselines` | Healthy mean/std per model and metric (fleet reference for the health score) | P1, P8 |
| `sql/30_fuel_anomalies.sql` | `af_ml.fuel_lpt_arima`, `af_out.fuel_anomalies`, `af_out.fuel_trends` | ARIMA_PLUS + `ML.DETECT_ANOMALIES` on daily L/t; progressive increase trend | P6 |
| `sql/40_machine_clusters.sql` | `af_ml.machine_clusters`, `af_out.machine_clusters` | KMEANS on relative usage features: balanced / overworked / low utilization + high cost | P7, P8 |
| `sql/50_parts_forecast.sql` | `af_ml.parts_demand`, `af_out.parts_forecast`, `af_out.parts_irregular` | ARIMA_PLUS 4-week demand per part and warehouse; irregular consumption | P3 |
| `sql/60_failure_precursors.sql` | `af_out.failure_precursors` | How each metric deviates 72/48/24 h before each failure type and the median lead time | P12 |

## Synthetic history

`synthetic/generate.py` builds 60 days of telemetry (12 machines × every 15 min), failures with 24-72 h
precursor drift, a contaminated fuel batch in Z3, progressive fuel wear (EXC-0051), a fuel anomaly (LDR-0011),
idle (TRK-0104, DRL-0003) and overloaded (TRK-0101) machines, and one year of part consumption.
The fleet comes from `contracts/examples/fleet-config.json` (same fleet as the simulator and the frontend mock).

```bash
gcloud auth login && gcloud auth application-default login
PROJECT=<gcp-project> ./analytics/load_synthetic.sh 60   # generate + load af_lake.*
PROJECT=<gcp-project> ./analytics/run_pipeline.sh         # run all SQL, prints top failure precursors
```

The BigQuery sandbox (no billing account) supports all of the above, including BigQuery ML.

## Tests

```bash
python -m pytest -q analytics/tests   # the generator plants every pattern the SQL must find
```

The SQL has not been executed yet (no GCP project at the time of writing): run `run_pipeline.sh` once the
project exists and fix any dialect issue there.
