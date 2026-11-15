# analytics — owner: Rol 3

BigQuery + BigQuery ML (GCP): the analytical half of the platform. Results flow back to DynamoDB through
`services/bigquery_sync_back`, so the second cloud adds value in both directions.

## Pipeline manual en el proyecto del equipo

| File | Output | What | Problem |
|---|---|---|---|
| `sql/00_setup.sql` | datasets, `af_lake.*` | DDL del equipo. Terraform conserva los datasets ya creados y solo prepara staging optativo. | — |
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

El equipo ejecutó el pipeline con cargas por lotes. Revisar cuota, expiración, permisos de jobs y alcance de cada script antes de repetirlo. La carga sintética y los scripts de reconstrucción escriben datos; el ensayo del puente usa un lote identificable. [Arquitectura y evidencia](../docs/arquitectura.md)

## Tests

```bash
python -m pytest -q analytics/tests   # the generator plants every pattern the SQL must find
```

Executed end to end in the BigQuery sandbox (project `arquifusion`, 2026-11-15). With the synthetic history it finds:
EXC-0051 progressive fuel increase (+2.7 %/week), LDR-0011 fuel anomaly (8 of its last 10 days), TRK-0104 and DRL-0003
as low utilization + high cost, TRK-0101 as overworked, a 4-week forecast for the 5 parts and the failure precursors.

On Windows, run the scripts from Git Bash; if `bq` says `python3.x: command not found`, set
`CLOUDSDK_PYTHON` to the Python bundled with the Cloud SDK (`.../google-cloud-sdk/platform/bundledpython/python.exe`).
