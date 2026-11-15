#!/usr/bin/env bash
# Generate synthetic history and load it into BigQuery (needs gcloud auth + a project with BigQuery).
# Usage: PROJECT=my-gcp-project ./analytics/load_synthetic.sh [days]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PROJECT:?set PROJECT=<gcp project id>}"
DAYS="${1:-60}"
OUT=analytics/synthetic/out

python analytics/synthetic/generate.py --days "$DAYS" --out "$OUT"
bq --project_id="$PROJECT" query --use_legacy_sql=false < analytics/sql/00_setup.sql

bq --project_id="$PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON af_lake.telemetry "$OUT/telemetry.ndjson.gz"
bq --project_id="$PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON af_lake.incidents "$OUT/incidents.ndjson"
bq --project_id="$PROJECT" load --replace --source_format=NEWLINE_DELIMITED_JSON af_lake.parts_movements "$OUT/parts_movements.ndjson"
echo "Loaded. Now run: PROJECT=$PROJECT ./analytics/run_pipeline.sh"
