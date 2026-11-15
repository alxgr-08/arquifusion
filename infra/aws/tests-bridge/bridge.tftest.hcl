mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/LabRole" }
  }
}

mock_provider "aws" {
  alias = "replica"
}

mock_provider "archive" {}

variables {
  expected_account_id = "111122223333"
}

run "default_no_runtime" {
  command = plan
  assert {
    condition     = length(aws_lambda_function.bridge) == 0 && length(aws_lambda_event_source_mapping.archive) == 0 && length(aws_lambda_layer_version.bridge_wif) == 0
    error_message = "Bridge resources must be explicitly selected."
  }
}

run "only_scoped_return" {
  command = plan
  variables {
    bridge_wif = {
      project_number = "111122223333", pool_id = "test-pool", provider_id = "test-academy", service_account_id = "af-bridge"
    }
    sync_back_trial = { machine_id = "SYN-9001", operation_id = "AF-PARQUET-SMOKE", kpi_date = "2026-11-15" }
  }
  assert {
    condition = (
      length(aws_lambda_function.bridge) == 1 && contains(keys(aws_lambda_function.bridge), "bigquery_sync_back") &&
      aws_lambda_function.bridge["bigquery_sync_back"].handler == "services.bigquery_sync_back.handler.selected_handler" &&
      aws_lambda_function.bridge["bigquery_sync_back"].environment[0].variables["AF_SYNC_MACHINE_ID"] == "SYN-9001" &&
      aws_lambda_function.bridge["bigquery_sync_back"].environment[0].variables["AF_SYNC_OPERATION_ID"] == "AF-PARQUET-SMOKE" &&
      aws_lambda_function.bridge["bigquery_sync_back"].environment[0].variables["AF_SYNC_DATE"] == "2026-11-15" &&
      aws_lambda_function.bridge["bigquery_sync_back"].environment[0].variables["GOOGLE_APPLICATION_CREDENTIALS"] == "/opt/arquifusion/gcp-wif.json" &&
      contains(keys(aws_lambda_function.bridge["bigquery_sync_back"].environment[0].variables), "AF_TABLE_ARN") &&
      length(aws_lambda_layer_version.bridge_wif) == 1 && length(aws_lambda_event_source_mapping.archive) == 0
    )
    error_message = "One manual function must receive the selected scope, the table ARN and public WIF configuration."
  }
}

run "return_requires_wif" {
  command = plan
  variables {
    sync_back_trial = { machine_id = "SYN-9001", operation_id = "AF-PARQUET-SMOKE", kpi_date = "2026-11-15" }
  }
  expect_failures = [var.sync_back_trial]
}

run "return_rejects_production_machine" {
  command = plan
  variables {
    bridge_wif = {
      project_number = "111122223333", pool_id = "test-pool", provider_id = "test-academy", service_account_id = "af-bridge"
    }
    sync_back_trial = { machine_id = "EXC-0042", operation_id = "MINA-NORTE", kpi_date = "2026-11-15" }
  }
  expect_failures = [var.sync_back_trial]
}

run "return_rejects_other_rehearsal_operation" {
  command = plan
  variables {
    bridge_wif = {
      project_number = "111122223333", pool_id = "test-pool", provider_id = "test-academy", service_account_id = "af-bridge"
    }
    sync_back_trial = { machine_id = "ENS-0007", operation_id = "ENSAYO-0008", kpi_date = "2026-11-15" }
  }
  expect_failures = [var.sync_back_trial]
}

run "manual_bridge" {
  command = plan
  variables {
    enable_telemetry_lake  = true
    enable_stream_exporter = true
    bridge_wif = {
      project_number     = "111122223333"
      pool_id            = "test-pool"
      provider_id        = "test-academy"
      service_account_id = "af-bridge"
    }
  }
  override_module {
    target          = module.telemetry[0]
    override_during = plan
    outputs = {
      settings = {
        AF_LAKE_BUCKET    = "af-dev-arquifusion-lake-111122223333-us-east-1-an"
        AF_PARQUET_PREFIX = "records/"
      }
    }
  }
  assert {
    condition = (
      length(aws_lambda_function.bridge) == 2 &&
      aws_lambda_function.bridge["stream_exporter"].handler == "services.stream_exporter.handler.handler" &&
      aws_lambda_function.bridge["lake_to_bigquery"].handler == "services.lake_to_bigquery.handler.handler" &&
      aws_lambda_function.bridge["lake_to_bigquery"].environment[0].variables["GCP_PROJECT_ID"] == "arquifusion" &&
      aws_lambda_function.bridge["lake_to_bigquery"].environment[0].variables["GOOGLE_APPLICATION_CREDENTIALS"] == "/opt/arquifusion/gcp-wif.json" &&
      length(aws_lambda_layer_version.bridge_wif) == 1
    )
    error_message = "Verify both real package entry points and the public WIF configuration in the loader."
  }
  assert {
    condition = (
      !aws_lambda_event_source_mapping.archive[0].enabled &&
      length(one(aws_lambda_event_source_mapping.archive[0].filter_criteria).filter) == 2 &&
      contains(aws_lambda_event_source_mapping.archive[0].function_response_types, "ReportBatchItemFailures") &&
      aws_cloudwatch_event_rule.incident.state == "DISABLED"
    )
    error_message = "The archive must remain inactive with only the incident/stock filters and partial batch responses."
  }
}
