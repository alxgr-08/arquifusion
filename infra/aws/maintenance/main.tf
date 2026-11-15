terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

variable "operation" { type = string }
variable "account_id" { type = string }
variable "table_arn" { type = string }
variable "lab_role_arn" { type = string }
variable "region" { type = string }
variable "env" { type = string }

locals {
  run          = trimprefix(var.operation, "ENSAYO-")
  name         = "af-dev-maintenance-trial-${local.run}"
  function_arn = "arn:aws:lambda:us-east-1:${var.account_id}:function:${local.name}"
  root         = "${path.module}/../../.."
  zip          = "${local.root}/build/maintenance/trial.zip"
  manifest     = try(jsondecode(file("${local.root}/build/maintenance/trial.manifest.json")), {})
  sources = setunion(
    toset(["infra/aws/maintenance/maintenance_trial.py", "services/incident_tasks/preventive.py", "services/offline_detector/handler.py"]),
    toset([for p in fileset("${local.root}/shared/af_shared", "**/*.py") : "shared/af_shared/${p}"])
  )
  current = try(
    local.manifest.package == filesha256(local.zip) &&
    local.manifest.dependencies == filesha256("${local.root}/build/incident/incident-tasks-catalog.zip") &&
    toset(keys(local.manifest.sources)) == local.sources &&
    alltrue([for path in local.sources : local.manifest.sources[path] == filesha256("${local.root}/${path}")]),
    false
  )
}

resource "aws_cloudwatch_log_group" "trial" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "trial" {
  function_name    = local.name
  role             = var.lab_role_arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "maintenance_trial.handler"
  filename         = local.zip
  source_code_hash = try(filebase64sha256(local.zip), null)
  timeout          = 30
  memory_size      = 256
  environment {
    variables = {
      AF_MAINTENANCE_OPERATION = var.operation
      AF_TABLE_ARN             = var.table_arn
    }
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.trial.name
  }
  lifecycle {
    precondition {
      condition = (
        var.env == "dev" && var.region == "us-east-1" && can(regex("^ENSAYO-[0-9]{4}$", var.operation)) &&
        var.table_arn == "arn:aws:dynamodb:us-east-1:${var.account_id}:table/af-dev-arquifusion" &&
        var.lab_role_arn == "arn:aws:iam::${var.account_id}:role/LabRole"
      )
      error_message = "Use the approved single-run Academy operation, dev table and existing LabRole in us-east-1."
    }
    precondition {
      condition     = local.current
      error_message = "Rebuild the manual package from the current sources with python infra/aws/maintenance/build.py."
    }
  }
}

resource "aws_sfn_state_machine" "preventive" {
  name     = "af-dev-preventive-trial-${local.run}"
  type     = "STANDARD"
  role_arn = var.lab_role_arn
  definition = templatefile("${local.root}/statemachines/preventive.asl.json", {
    incident_tasks_arn = local.function_arn
    notify_arn         = local.function_arn
  })
  depends_on = [aws_lambda_function.trial]
}

output "targets" {
  value = {
    operation = var.operation
    workflow  = aws_sfn_state_machine.preventive.arn
    detector  = aws_lambda_function.trial.arn
  }
}
