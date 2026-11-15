#!/usr/bin/env bash
# Run the analytics pipeline in order (the same SQL runs daily as BigQuery scheduled queries).
# Usage: PROJECT=my-gcp-project ./analytics/run_pipeline.sh
set -euo pipefail
cd "$(dirname "$0")/sql"
: "${PROJECT:?set PROJECT=<gcp project id>}"
for f in 10_daily_kpis.sql 20_model_baselines.sql 30_fuel_anomalies.sql 40_machine_clusters.sql \
         50_parts_forecast.sql 60_failure_precursors.sql; do
  echo "== $f"
  bq --project_id="$PROJECT" query --use_legacy_sql=false --format=none < "$f"
done
bq --project_id="$PROJECT" query --use_legacy_sql=false \
  "SELECT component, metric, failures, avg_z_24_0h, median_lead_hours FROM af_out.failure_precursors ORDER BY ABS(avg_z_24_0h) DESC LIMIT 10"
