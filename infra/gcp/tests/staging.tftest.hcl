mock_provider "google" {}

variables {
  project_id = "arquifusion"
}

run "existing_datasets_untouched" {
  command = plan
  assert {
    condition     = length(google_bigquery_dataset.bridge_staging) == 0
    error_message = "Staging must be opt-in."
  }
}

run "temporary_tables_expire" {
  command = plan
  variables {
    enable_bridge_staging = true
  }
  assert {
    condition = (
      google_bigquery_dataset.bridge_staging[0].dataset_id == "af_bridge_stage" &&
      google_bigquery_dataset.bridge_staging[0].location == "US" &&
      google_bigquery_dataset.bridge_staging[0].default_table_expiration_ms == 86400000
    )
    error_message = "Only the approved US staging dataset with a 24-hour table TTL may be created."
  }
}
