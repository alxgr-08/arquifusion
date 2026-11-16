resource "google_bigquery_table" "raw" {
  dataset_id  = google_bigquery_dataset.bridge.dataset_id
  table_id    = "telemetry_raw"
  description = "Complete Parquet readings from the private AWS lake"
  schema      = file("${local.build}/raw-schema.json")
  time_partitioning {
    type          = "DAY"
    field         = "timestamp"
    expiration_ms = 2678400000
  }
  clustering = ["operationId", "machineId"]
  labels     = local.tags
}
resource "google_bigquery_table" "telemetry" {
  dataset_id  = google_bigquery_dataset.bridge.dataset_id
  table_id    = "telemetry"
  description = "Canonical readings by producer message ID"
  view {
    query          = file("${local.build}/telemetry.sql")
    use_legacy_sql = false
  }
  depends_on = [google_bigquery_table.raw]
}
resource "google_bigquery_table" "daily" {
  dataset_id  = google_bigquery_dataset.bridge.dataset_id
  table_id    = "daily_kpis"
  description = "Daily KPI using the team's analytics SQL for the enrolled operation"
  view {
    query          = file("${local.build}/daily.sql")
    use_legacy_sql = false
  }
  depends_on = [google_bigquery_table.telemetry]
}
