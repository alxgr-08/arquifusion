locals {
  health_evaluator_name = "${local.prefix}-health-evaluator"
  health_evaluator_zip  = "${path.module}/../../build/health-evaluator/health-evaluator.zip"
}

data "aws_iam_role" "lab" {
  name = "LabRole"

  lifecycle {
    postcondition {
      condition     = var.lab_role_arn == null || var.lab_role_arn == self.arn
      error_message = "The supplied execution role must match the existing LabRole in the expected account."
    }
  }
}

resource "aws_cloudwatch_event_bus" "arquifusion" {
  name = "${local.prefix}-bus"
}

resource "aws_ssm_parameter" "health_weights" {
  name = "/af/health/weights"
  type = "String"
  value = jsonencode({
    temperatureC = 0.30
    vibrationMmS = 0.30
    pressureBar  = 0.15
    fuelRateLph  = 0.15
    rpm          = 0.10
  })
}

resource "aws_ssm_parameter" "health_thresholds" {
  name = "/af/health/thresholds"
  type = "String"
  value = jsonencode({
    watch_below        = 80
    warning_below      = 60
    critical_below     = 40
    streak_required    = 3
    hard_critical_risk = 4.5
    min_samples        = 30
  })
}

resource "aws_cloudwatch_log_group" "health_evaluator" {
  name              = "/aws/lambda/${local.health_evaluator_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "health_evaluator" {
  function_name    = local.health_evaluator_name
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.handler"
  memory_size      = 256
  timeout          = 10
  filename         = local.health_evaluator_zip
  source_code_hash = fileexists(local.health_evaluator_zip) ? filebase64sha256(local.health_evaluator_zip) : null

  lifecycle {
    precondition {
      condition     = fileexists(local.health_evaluator_zip)
      error_message = "Build the Lambda package with infra/aws/health_evaluator/build.py before planning."
    }
  }

  environment {
    variables = {
      AF_TABLE_NAME            = aws_dynamodb_table.arquifusion.name
      AF_EVENT_BUS             = aws_cloudwatch_event_bus.arquifusion.name
      AF_SSM_HEALTH_WEIGHTS    = aws_ssm_parameter.health_weights.name
      AF_SSM_HEALTH_THRESHOLDS = aws_ssm_parameter.health_thresholds.name
      POWERTOOLS_SERVICE_NAME  = "health-evaluator"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.health_evaluator.name
  }
}
