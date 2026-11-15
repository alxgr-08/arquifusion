locals {
  # The owner's ASL comment documents the literal ${...}. Quote only that example
  # for Terraform's template reader so the rendered comment and all states survive.
  incident_template = replace(
    file("${path.module}/../../statemachines/incident.asl.json"),
    "$${...}", join("", ["$", "$${...}"])
  )
  incident_name = "${local.prefix}-incident"
  incident_functions = {
    incident_tasks    = "incident-tasks"
    assign_technician = "assign-technician"
    notify            = "notify"
    ai_diagnosis      = "ai-diagnosis"
  }
  # Render the actual definition during plan, even before the functions exist.
  incident_arns = {
    for key, suffix in local.incident_functions :
    "${key}_arn" => "arn:aws:lambda:${var.region}:${var.expected_account_id}:function:${local.prefix}-${suffix}"
  }
  incident_arn   = "arn:aws:states:${var.region}:${var.expected_account_id}:stateMachine:${local.incident_name}"
  incident_fleet = jsonencode(jsondecode(file("${path.module}/../../contracts/examples/fleet-config.json")))
  incident_zips  = "${path.module}/../../build/incident"
  incident_packages = {
    for key, suffix in local.incident_functions :
    key => "${local.incident_zips}/${key == "incident_tasks" ? "incident-tasks-catalog" : suffix}.zip"
  }
  catalog_bucket = "${local.prefix}-catalog-${var.expected_account_id}"
  catalog_key    = "catalogs/fleet-config.json"
  catalog_value = var.incident_catalog_s3_version == null ? jsonencode({
    format = "gzip+base64"
    data   = base64gzip(local.incident_fleet)
    sha256 = sha256(local.incident_fleet)
    }) : jsonencode({
    format    = "s3"
    bucket    = local.catalog_bucket
    key       = local.catalog_key
    versionId = var.incident_catalog_s3_version
    sha256    = sha256(data.aws_s3_object.incident_catalog[0].body)
  })
}

variable "incident_catalog_s3_version" {
  description = "Version ID of the approved fleet JSON in the dedicated ArquiFusion data bucket. Null stores the complete catalog in SSM Standard."
  type        = string
  default     = null

  validation {
    condition     = var.incident_catalog_s3_version == null || try(!contains(["", "null"], trimspace(var.incident_catalog_s3_version)), false)
    error_message = "Specify an immutable S3 version ID or omit this variable."
  }
}

data "aws_s3_bucket" "incident_catalog" {
  count  = var.incident_catalog_s3_version == null ? 0 : 1
  bucket = local.catalog_bucket

  lifecycle {
    postcondition {
      condition     = self.bucket_region == var.region
      error_message = "The dedicated catalog bucket must be in the application region."
    }
  }
}

data "aws_s3_object" "incident_catalog" {
  count      = var.incident_catalog_s3_version == null ? 0 : 1
  bucket     = data.aws_s3_bucket.incident_catalog[0].bucket
  key        = local.catalog_key
  version_id = var.incident_catalog_s3_version

  lifecycle {
    postcondition {
      condition     = try(jsondecode(self.body) == jsondecode(local.incident_fleet), false)
      error_message = "The selected S3 version must have a JSON content type and contain the complete catalog from this checkout."
    }
  }
}

variable "incident_test_operation_id" {
  description = "Enable the development rule only for this isolated synthetic operation. Null keeps it disabled."
  type        = string
  default     = null

  validation {
    condition     = var.incident_test_operation_id == null || can(regex("^AF-INC-TEST-[0-9a-f]{32}$", var.incident_test_operation_id))
    error_message = "Use an operation created by incident/prepare_case.py."
  }
}

resource "aws_cloudwatch_log_group" "incident_lambda" {
  for_each          = local.incident_functions
  name              = "/aws/lambda/${local.prefix}-${each.value}"
  retention_in_days = 14
}

resource "aws_lambda_function" "incident" {
  for_each         = local.incident_functions
  function_name    = "${local.prefix}-${each.value}"
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = each.key == "incident_tasks" ? "incident_catalog.handler" : "handler.handler"
  memory_size      = each.key == "ai_diagnosis" ? 512 : 256
  timeout          = each.key == "ai_diagnosis" ? 60 : 30
  filename         = local.incident_packages[each.key]
  source_code_hash = filebase64sha256(local.incident_packages[each.key])

  environment {
    variables = merge(
      { POWERTOOLS_SERVICE_NAME = each.value },
      each.key == "ai_diagnosis" ? { AF_AI_PROVIDER = "rules" } : { AF_TABLE_NAME = aws_dynamodb_table.arquifusion.name },
      each.key == "incident_tasks" ? { AF_INCIDENT_CATALOG = aws_ssm_parameter.incident_fleet.name } : {},
      each.key == "incident_tasks" && var.incident_catalog_s3_version != null ? {
        AF_CATALOG_BUCKET = local.catalog_bucket
        AF_CATALOG_OWNER  = var.expected_account_id
      } : {}
    )
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.incident_lambda[each.key].name
  }
}

resource "aws_sns_topic" "incident_notifications" {
  name = "${local.incident_name}-notifications"
}

resource "aws_ssm_parameter" "incident_topics" {
  name  = "/af/notify/topics"
  type  = "String"
  value = jsonencode({ for role in ["OPERATOR", "TECHNICIAN", "MAINT_LEAD", "SUPERVISOR", "MANAGER"] : role => aws_sns_topic.incident_notifications.arn })
}

resource "aws_ssm_parameter" "incident_settings" {
  name = "/af/incident/settings"
  type = "String"
  value = jsonencode({
    techAckTimeoutSeconds    = 1800
    resolutionTimeoutSeconds = 14400
    noTechnicianRetrySeconds = 120
    maxAssignAttempts        = 5
    zoneWindowMinutes        = 120
    zoneMinMachines          = 3
    recurrenceWindowDays     = 30
    technicianSpeedKmh       = 25
  })
}

resource "aws_ssm_parameter" "incident_fleet" {
  name  = "/af/incident/fleet-catalog"
  type  = "String"
  tier  = "Standard"
  value = local.catalog_value

  lifecycle {
    precondition {
      # Base64 length gives an exact UTF-8 byte count after removing padding.
      condition     = floor(length(trim(base64encode(local.catalog_value), "=")) * 3 / 4) <= 4096
      error_message = "The fleet catalog exceeds SSM Standard. Select a reviewed version in the dedicated S3 catalog bucket."
    }
  }
}

resource "aws_sfn_state_machine" "incident" {
  name       = local.incident_name
  type       = "STANDARD"
  role_arn   = data.aws_iam_role.lab.arn
  definition = templatestring(local.incident_template, local.incident_arns)
  depends_on = [aws_lambda_function.incident, aws_ssm_parameter.incident_fleet, aws_ssm_parameter.incident_topics, aws_ssm_parameter.incident_settings]
}

resource "aws_sfn_state_machine" "incident_entry" {
  name     = "${local.incident_name}-entry"
  type     = "STANDARD"
  role_arn = data.aws_iam_role.lab.arn
  definition = templatefile("${path.module}/incident/entry.asl.json", {
    incident_arn     = local.incident_arn
    execution_prefix = "arn:aws:states:${var.region}:${var.expected_account_id}:execution:${local.incident_name}:"
  })
  depends_on = [aws_sfn_state_machine.incident]
}

resource "aws_cloudwatch_event_rule" "incident" {
  name           = local.incident_name
  event_bus_name = aws_cloudwatch_event_bus.arquifusion.name
  state          = var.incident_test_operation_id == null ? "DISABLED" : "ENABLED"
  event_pattern = jsonencode({
    source        = ["arquifusion.health"]
    "detail-type" = ["AnomalyDetected"]
    detail = merge(
      { severity = ["WARNING", "CRITICAL"] },
      var.incident_test_operation_id == null ? {} : { operationId = [var.incident_test_operation_id] }
    )
  })
}

resource "aws_sqs_queue" "incident_delivery_dlq" {
  name                      = "${local.incident_name}-delivery-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "incident_delivery" {
  queue_url = aws_sqs_queue.incident_delivery_dlq.url
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "ArquiFusionIncidentDeliveryFailures"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.incident_delivery_dlq.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_cloudwatch_event_rule.incident.arn }
        StringEquals = { "aws:SourceAccount" = var.expected_account_id }
      }
    }]
  })
}

resource "aws_cloudwatch_event_target" "incident" {
  event_bus_name = aws_cloudwatch_event_bus.arquifusion.name
  rule           = aws_cloudwatch_event_rule.incident.name
  target_id      = "StartIncident"
  arn            = aws_sfn_state_machine.incident_entry.arn
  role_arn       = data.aws_iam_role.lab.arn
  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }
  dead_letter_config {
    arn = aws_sqs_queue.incident_delivery_dlq.arn
  }
  depends_on = [aws_sqs_queue_policy.incident_delivery]
}

resource "aws_cloudwatch_metric_alarm" "incident_execution_failures" {
  for_each = {
    workflow = aws_sfn_state_machine.incident.arn
    entry    = aws_sfn_state_machine.incident_entry.arn
  }
  alarm_name          = "${local.incident_name}-${each.key}-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 60
  treat_missing_data  = "notBreaching"
  dimensions          = { StateMachineArn = each.value }
}

resource "aws_cloudwatch_metric_alarm" "incident_delivery_dlq" {
  alarm_name          = "${local.incident_name}-delivery-dlq-visible"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.incident_delivery_dlq.name }
}
