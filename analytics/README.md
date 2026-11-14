# analytics (Rol 3)

BigQuery SQL and BigQuery ML.

| Object | Content | Problem |
|---|---|---|
| af_lake.telemetry | Telemetry (partitioned by date, clustered by machineId) | P6, P7, P12 |
| af_ml.parts_demand (ARIMA_PLUS) | Weekly demand forecast per part and warehouse | P3 |
| af_ml.machine_clusters (KMEANS) | Machines with similar usage | P7, P8 |
| ML.DETECT_ANOMALIES on fuel | Unexpected fuel consumption | P6 |
| af_out.daily_kpis, af_out.model_baselines, af_out.parts_forecast | Results synced back to DynamoDB | P3, P7, P8 |
