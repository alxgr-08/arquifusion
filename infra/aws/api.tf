variable "enable_incident_api" {
  description = "Create the protected incident API. The endpoint stays closed unless explicitly opened."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition     = !var.enable_incident_api || (var.ack_google_web_client_id != null && var.ack_frontend_origin != null)
    error_message = "Enabling the incident API requires the Google web client ID from #41 and its registered frontend origin."
  }
}

variable "open_incident_api_endpoint" {
  description = "Enable the HTTPS execute-api endpoint for the rehearsal with the configured JWT audience and origin."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition     = !var.open_incident_api_endpoint || var.enable_incident_api
    error_message = "Opening the endpoint requires enable_incident_api and its registered Google client and origin."
  }
}

variable "enable_fleet_read_routes" {
  description = "Add the ten authenticated reads used by the fleet, machine, KPI and parts screens."
  type        = bool
  default     = false
  nullable    = false

  validation {
    condition     = !var.enable_fleet_read_routes || var.enable_incident_api
    error_message = "Fleet read routes require the protected incident API."
  }
}

variable "ack_google_web_client_id" {
  description = "Public ArquiFusion Google Identity Services web client ID verified in #41."
  type        = string
  default     = null

  validation {
    condition     = var.ack_google_web_client_id == null || var.ack_google_web_client_id == "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    error_message = "Use the registered public ArquiFusion web client ID from #41."
  }
}

variable "ack_frontend_origin" {
  description = "One exact JavaScript origin registered for this Google client. The development origin is http://localhost:5173."
  type        = string
  default     = null

  validation {
    condition = var.ack_frontend_origin == null || (
      var.env == "dev" && var.ack_frontend_origin == "http://localhost:5173"
      ) || (
      can(regex("^https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?(:[1-9][0-9]{0,4})?$", var.ack_frontend_origin)) &&
      try(tonumber(regex(":([0-9]+)$", var.ack_frontend_origin)[0]) <= 65535, true)
    )
    error_message = "Use one exact HTTPS origin with a DNS hostname and optional valid port, or http://localhost:5173 in dev."
  }
}

locals {
  ack_name = "${local.prefix}-api-ack"
  ack_zip  = "${path.module}/../../build/api/api-ack.zip"
  fleet_read_paths = {
    machines         = "/operations/{op}/machines"
    incidents        = "/operations/{op}/incidents"
    technicians      = "/operations/{op}/technicians"
    kpis             = "/operations/{op}/kpis"
    machine          = "/machines/{id}"
    readings         = "/machines/{id}/readings"
    machineIncidents = "/machines/{id}/incidents"
    maintenance      = "/machines/{id}/maintenance"
    parts            = "/parts"
    partStock        = "/parts/{partId}/stock"
  }
  ack_routes = var.enable_incident_api ? merge(
    {
      GET  = { method = "GET", path = "/machines/{id}/incidents/{incId}" }
      POST = { method = "POST", path = "/machines/{id}/incidents/{incId}/ack" }
    },
    { for name, path in local.fleet_read_paths :
      name => { method = "GET", path = path } if var.enable_fleet_read_routes
    }
  ) : {}
}

resource "aws_apigatewayv2_api" "ack" {
  count                        = var.enable_incident_api ? 1 : 0
  name                         = "${local.prefix}-api"
  protocol_type                = "HTTP"
  disable_execute_api_endpoint = !var.open_incident_api_endpoint

  cors_configuration {
    allow_origins  = [var.ack_frontend_origin]
    allow_methods  = ["GET", "POST"]
    allow_headers  = ["Authorization", "Content-Type", "If-Match"]
    expose_headers = ["ETag", "Retry-After"]
  }
}

resource "aws_apigatewayv2_authorizer" "ack_google" {
  count            = var.enable_incident_api ? 1 : 0
  api_id           = aws_apigatewayv2_api.ack[0].id
  name             = "${local.prefix}-google-ack"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = "https://accounts.google.com"
    audience = [var.ack_google_web_client_id]
  }
}

resource "aws_cloudwatch_log_group" "ack" {
  count             = var.enable_incident_api ? 1 : 0
  name              = "/aws/lambda/${local.ack_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "ack" {
  count            = var.enable_incident_api ? 1 : 0
  function_name    = local.ack_name
  role             = data.aws_iam_role.lab.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handler.handler"
  memory_size      = 256
  timeout          = 15
  filename         = local.ack_zip
  source_code_hash = fileexists(local.ack_zip) ? filebase64sha256(local.ack_zip) : null

  lifecycle {
    precondition {
      condition     = fileexists(local.ack_zip)
      error_message = "Build the services/api Lambda with python infra/aws/health_evaluator/build.py --api before planning the enabled API."
    }
  }

  environment {
    variables = {
      AF_TABLE_NAME   = aws_dynamodb_table.arquifusion.name
      AF_OPERATION_ID = var.operation_id
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ack[0].name
  }
}

resource "aws_apigatewayv2_integration" "ack" {
  count                  = var.enable_incident_api ? 1 : 0
  api_id                 = aws_apigatewayv2_api.ack[0].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.ack[0].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 16000
}

resource "aws_lambda_permission" "ack_gateway" {
  for_each       = local.ack_routes
  statement_id   = contains(["GET", "POST"], each.key) ? "InvokeProtectedIncident${each.key}" : "ReadFleet${title(each.key)}"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ack[0].function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.expected_account_id
  source_arn     = "${aws_apigatewayv2_api.ack[0].execution_arn}/${var.env}/${each.value.method}${replace(each.value.path, "/\\{(id|incId|op|partId)\\}/", "*")}"
}

resource "aws_apigatewayv2_route" "ack" {
  for_each           = local.ack_routes
  api_id             = aws_apigatewayv2_api.ack[0].id
  route_key          = "${each.value.method} ${each.value.path}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.ack_google[0].id
  target             = "integrations/${aws_apigatewayv2_integration.ack[0].id}"
}

resource "aws_cloudwatch_log_group" "ack_gateway" {
  count             = var.enable_incident_api ? 1 : 0
  name              = "/aws/apigateway/${local.ack_name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "ack" {
  count       = var.enable_incident_api ? 1 : 0
  api_id      = aws_apigatewayv2_api.ack[0].id
  name        = var.env
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = var.enable_fleet_read_routes ? 4 : 2
    throttling_rate_limit  = 1
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.ack_gateway[0].arn
    format = jsonencode({
      requestId = "$context.requestId"
      routeKey  = "$context.routeKey"
      status    = "$context.status"
    })
  }

  depends_on = [aws_apigatewayv2_route.ack, aws_lambda_permission.ack_gateway]
}

output "incident_api_base_url" {
  description = "Reserved HTTPS stage URL. Reachable only when open_incident_api_endpoint is true."
  value       = one(aws_apigatewayv2_stage.ack[*].invoke_url)
}
