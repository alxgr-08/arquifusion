variable "enable_bridge_staging" {
  description = "Prepare an expiring US staging dataset for the ArquiFusion Parquet loader."
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_bridge_staging || (var.project_id == "arquifusion" && var.bq_location == "US")
    error_message = "The approved bridge stages in the arquifusion project and the US multi-region."
  }
}

resource "google_bigquery_dataset" "bridge_staging" {
  count                       = var.enable_bridge_staging ? 1 : 0
  project                     = var.project_id
  dataset_id                  = "af_bridge_stage"
  location                    = var.bq_location
  description                 = "Temporary source-format staging for ArquiFusion Firehose Parquet files"
  default_table_expiration_ms = 86400000
  delete_contents_on_destroy  = false
  labels                      = { project = "arquifusion", env = "dev", owner = "rol1" }

  lifecycle {
    prevent_destroy = true
  }
}
