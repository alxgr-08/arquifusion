variable "enable_stream_exporter" {
  description = "Prepare the incident and parts exporter and its initially disabled DynamoDB stream mapping."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition     = !var.enable_stream_exporter || var.enable_telemetry_lake
    error_message = "The exporter needs the dedicated, verified ArquiFusion lake bucket."
  }
}

variable "bridge_wif" {
  description = "Public identifiers of the approved ArquiFusion WIF provider and service account. With the lake enabled this configures the loader. The selected return can reuse it independently."
  type = object({
    project_number     = string
    pool_id            = string
    provider_id        = string
    service_account_id = string
  })
  default = null

  validation {
    condition = var.bridge_wif == null || (
      can(regex("^[1-9][0-9]{5,19}$", try(var.bridge_wif.project_number, ""))) &&
      alltrue([for id in [try(var.bridge_wif.pool_id, ""), try(var.bridge_wif.provider_id, "")] :
        can(regex("^[a-z0-9-]{4,32}$", id)) && !startswith(id, "gcp-")
      ]) &&
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", try(var.bridge_wif.service_account_id, "")))
    )
    error_message = "Supply the four public identifiers agreed with the GCP project owner."
  }
}

variable "sync_back_trial" {
  description = "Optional single KPI return from af_out to the ArquiFusion DynamoDB table. Pin the synthetic machine, operation and UTC day agreed for the manual rehearsal."
  type = object({
    machine_id   = string
    operation_id = string
    kpi_date     = string
  })
  default = null

  validation {
    condition = var.sync_back_trial == null || (var.bridge_wif != null &&
      can(formatdate("YYYY-MM-DD", "${try(var.sync_back_trial.kpi_date, "")}T00:00:00Z")) &&
      can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", try(var.sync_back_trial.kpi_date, ""))) && (
        (try(var.sync_back_trial.machine_id, "") == "SYN-9001" && try(var.sync_back_trial.operation_id, "") == "AF-PARQUET-SMOKE") ||
        (can(regex("^ENS-[0-9]{4}$", try(var.sync_back_trial.machine_id, ""))) &&
        try(var.sync_back_trial.operation_id, "") == replace(try(var.sync_back_trial.machine_id, ""), "ENS-", "ENSAYO-")) ||
        (can(regex("^TL[A-Z]{3}-[0-9]{4}$", try(var.sync_back_trial.machine_id, ""))) &&
        can(regex("^AF-LAKE-TEST-[A-F0-9]{32}$", try(var.sync_back_trial.operation_id, ""))))
      )
    )
    error_message = "An approved WIF identity, a known synthetic reading scope and a valid YYYY-MM-DD date are required."
  }
}

locals {
  lake_loader_enabled = var.bridge_wif != null && var.enable_telemetry_lake
  sync_back_enabled   = var.sync_back_trial != null
  bridge_wif_enabled  = var.bridge_wif != null && (local.lake_loader_enabled || local.sync_back_enabled)
  bridge_zips         = "${path.module}/../../build/bridge"
  bridge_functions = merge(
    var.enable_stream_exporter ? { stream_exporter = "stream-exporter" } : {},
    local.lake_loader_enabled ? { lake_to_bigquery = "lake-to-bigquery" } : {},
    local.sync_back_enabled ? { bigquery_sync_back = "bigquery-sync-back" } : {}
  )
}

data "archive_file" "bridge_wif" {
  count       = local.bridge_wif_enabled ? 1 : 0
  type        = "zip"
  output_path = "${local.bridge_zips}/wif-config.zip"

  source {
    filename = "arquifusion/gcp-wif.json"
    content = jsonencode({
      type                              = "external_account"
      audience                          = "//iam.googleapis.com/projects/${var.bridge_wif.project_number}/locations/global/workloadIdentityPools/${var.bridge_wif.pool_id}/providers/${var.bridge_wif.provider_id}"
      subject_token_type                = "urn:ietf:params:aws:token-type:aws4_request"
      token_url                         = "https://sts.googleapis.com/v1/token"
      service_account_impersonation_url = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${var.bridge_wif.service_account_id}@arquifusion.iam.gserviceaccount.com:generateAccessToken"
      credential_source = {
        environment_id                 = "aws1"
        region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
        url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
        imdsv2_session_token_url       = "http://169.254.169.254/latest/api/token"
        regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
      }
    })
  }
}

resource "aws_lambda_layer_version" "bridge_wif" {
  count               = local.bridge_wif_enabled ? 1 : 0
  layer_name          = "${local.prefix}-bigquery-wif"
  description         = "Public federation settings for the ArquiFusion BigQuery loader"
  filename            = data.archive_file.bridge_wif[0].output_path
  source_code_hash    = data.archive_file.bridge_wif[0].output_base64sha256
  compatible_runtimes = ["python3.12"]
}

resource "aws_cloudwatch_log_group" "bridge" {
  for_each          = local.bridge_functions
  name              = "/aws/lambda/${local.prefix}-${each.value}"
  retention_in_days = 14
}

resource "aws_lambda_function" "bridge" {
  for_each         = local.bridge_functions
  function_name    = "${local.prefix}-${each.value}"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = each.key == "bigquery_sync_back" ? "services.bigquery_sync_back.handler.selected_handler" : "services.${each.key}.handler.handler"
  filename         = "${local.bridge_zips}/${each.value}.zip"
  source_code_hash = filebase64sha256("${local.bridge_zips}/${each.value}.zip")
  memory_size      = { stream_exporter = 256, lake_to_bigquery = 1024, bigquery_sync_back = 512 }[each.key]
  timeout          = { stream_exporter = 60, lake_to_bigquery = 300, bigquery_sync_back = 120 }[each.key]
  layers           = each.key != "stream_exporter" ? aws_lambda_layer_version.bridge_wif[*].arn : []

  environment {
    variables = merge(
      each.key != "bigquery_sync_back" ? {
        AF_LAKE_BUCKET     = module.telemetry[0].settings.AF_LAKE_BUCKET
        AF_LAKE_ACCOUNT_ID = var.expected_account_id
      } : {},
      each.key == "stream_exporter" ? {
        AF_SOURCE_STREAM_ARN = aws_dynamodb_table.arquifusion.stream_arn
        } : {
        GCP_PROJECT_ID                 = "arquifusion"
        GOOGLE_APPLICATION_CREDENTIALS = "/opt/arquifusion/gcp-wif.json"
      },
      each.key == "lake_to_bigquery" ? {
        AF_PARQUET_PREFIX     = module.telemetry[0].settings.AF_PARQUET_PREFIX
        AF_BQ_STAGING_DATASET = "af_bridge_stage"
      } : {},
      each.key == "bigquery_sync_back" ? {
        AF_TABLE_ARN         = aws_dynamodb_table.arquifusion.arn
        AF_SYNC_MACHINE_ID   = var.sync_back_trial.machine_id
        AF_SYNC_OPERATION_ID = var.sync_back_trial.operation_id
        AF_SYNC_DATE         = var.sync_back_trial.kpi_date
      } : {}
    )
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.bridge[each.key].name
  }
}

resource "aws_lambda_event_source_mapping" "archive" {
  count                              = var.enable_stream_exporter ? 1 : 0
  enabled                            = false
  event_source_arn                   = aws_dynamodb_table.arquifusion.stream_arn
  function_name                      = aws_lambda_function.bridge["stream_exporter"].arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 25
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]

  filter_criteria {
    dynamic "filter" {
      for_each = { "MACHINE#" = "INCIDENT#", "PART#" = "STOCK#" }
      content {
        pattern = jsonencode({ dynamodb = { Keys = {
          PK = { S = [{ prefix = filter.key }] }, SK = { S = [{ prefix = filter.value }] }
        } } })
      }
    }
  }
}
