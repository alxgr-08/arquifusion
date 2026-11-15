mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/LabRole"
    }
  }

  mock_resource "aws_apigatewayv2_api" {
    override_during = plan
    defaults = {
      execution_arn = "arn:aws:execute-api:us-east-1:123456789012:synthetic"
    }
  }
}

variables {
  expected_account_id = "123456789012"
}

run "disabled_by_default" {
  command = plan

  assert {
    condition = (
      length(aws_apigatewayv2_api.ack) == 0 && length(aws_apigatewayv2_route.ack) == 0 &&
      length(aws_lambda_permission.ack_gateway) == 0 && length(aws_lambda_function.ack) == 0 &&
      output.incident_api_base_url == null
    )
    error_message = "The default plan must omit the incident API, its Lambda and the stage URL."
  }
}

run "registered_development_origin" {
  command = plan

  variables {
    enable_incident_api      = true
    ack_google_web_client_id = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    ack_frontend_origin      = "http://localhost:5173"
  }

  assert {
    condition = (
      aws_apigatewayv2_api.ack[0].disable_execute_api_endpoint &&
      aws_apigatewayv2_route.ack["GET"].route_key == "GET /machines/{id}/incidents/{incId}" &&
      aws_apigatewayv2_route.ack["POST"].route_key == "POST /machines/{id}/incidents/{incId}/ack" &&
      length(aws_apigatewayv2_route.ack) == 2 &&
      alltrue([for route in aws_apigatewayv2_route.ack : route.authorization_type == "JWT"])
    )
    error_message = "The public endpoint must be closed, with only the protected incident detail and ACK routes defined."
  }

  assert {
    condition = (
      aws_lambda_permission.ack_gateway["GET"].source_arn == "arn:aws:execute-api:us-east-1:123456789012:synthetic/dev/GET/machines/*/incidents/*" &&
      aws_lambda_permission.ack_gateway["POST"].source_arn == "arn:aws:execute-api:us-east-1:123456789012:synthetic/dev/POST/machines/*/incidents/*/ack" &&
      length(aws_lambda_permission.ack_gateway) == 2
    )
    error_message = "Each Lambda permission must use the exact API, stage, method and resource path."
  }
}

run "enabled_requires_client_and_origin" {
  command = plan
  variables {
    enable_incident_api      = true
    ack_google_web_client_id = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
  }
  expect_failures = [var.enable_incident_api]
}

run "one_concrete_https_origin" {
  command = plan
  variables {
    enable_incident_api      = true
    ack_google_web_client_id = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    ack_frontend_origin      = "https://frontend.example.org:8443"
  }
  assert {
    condition     = one(aws_apigatewayv2_api.ack[0].cors_configuration).allow_origins == toset(["https://frontend.example.org:8443"])
    error_message = "A concrete HTTPS origin must be preserved exactly."
  }
}

run "reject_unregistered_loopback_port" {
  command = plan
  variables {
    ack_frontend_origin = "http://localhost:5174"
  }
  expect_failures = [var.ack_frontend_origin]
}

run "unregistered_audience" {
  command = plan
  variables {
    ack_google_web_client_id = "123456789-other.apps.googleusercontent.com"
  }
  expect_failures = [var.ack_google_web_client_id]
}

run "loopback_is_development_only" {
  command = plan
  variables {
    env                 = "prod"
    ack_frontend_origin = "http://localhost:5173"
  }
  expect_failures = [var.ack_frontend_origin]
}

run "reject_wildcard" {
  command = plan
  variables {
    ack_frontend_origin = "https://*.example.org"
  }
  expect_failures = [var.ack_frontend_origin]
}

run "reject_path" {
  command = plan
  variables {
    ack_frontend_origin = "https://example.org/app"
  }
  expect_failures = [var.ack_frontend_origin]
}

run "reject_out_of_range_port" {
  command = plan
  variables {
    ack_frontend_origin = "https://example.org:65536"
  }
  expect_failures = [var.ack_frontend_origin]
}

run "opening_requires_the_protected_api" {
  command = plan
  variables {
    open_incident_api_endpoint = true
  }
  expect_failures = [var.open_incident_api_endpoint]
}

run "fleet_reads_require_the_protected_api" {
  command = plan
  variables {
    enable_fleet_read_routes = true
  }
  expect_failures = [var.enable_fleet_read_routes]
}

run "open_incident_rehearsal" {
  command = plan
  variables {
    enable_incident_api        = true
    open_incident_api_endpoint = true
    ack_google_web_client_id   = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    ack_frontend_origin        = "http://localhost:5173"
  }
  assert {
    condition = (
      !aws_apigatewayv2_api.ack[0].disable_execute_api_endpoint &&
      toset(keys(aws_apigatewayv2_route.ack)) == toset(["GET", "POST"]) &&
      one(aws_apigatewayv2_api.ack[0].cors_configuration).allow_origins == toset(["http://localhost:5173"]) &&
      alltrue([for r in aws_apigatewayv2_route.ack : r.authorization_type == "JWT"])
    )
    error_message = "Opening the endpoint must preserve the single origin and the two protected incident routes."
  }
}

run "dashboard_reads_remain_closed_for_review" {
  command = plan
  variables {
    enable_incident_api      = true
    enable_fleet_read_routes = true
    ack_google_web_client_id = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    ack_frontend_origin      = "http://localhost:5173"
  }
  assert {
    condition = (
      aws_apigatewayv2_api.ack[0].disable_execute_api_endpoint &&
      toset([for r in aws_apigatewayv2_route.ack : r.route_key]) == toset([
        "GET /machines/{id}/incidents/{incId}", "POST /machines/{id}/incidents/{incId}/ack",
        "GET /operations/{op}/machines", "GET /operations/{op}/incidents",
        "GET /operations/{op}/technicians", "GET /operations/{op}/kpis",
        "GET /machines/{id}", "GET /machines/{id}/readings", "GET /machines/{id}/incidents",
        "GET /machines/{id}/maintenance", "GET /parts", "GET /parts/{partId}/stock"
      ]) && alltrue([for r in aws_apigatewayv2_route.ack : r.authorization_type == "JWT"])
    )
    error_message = "Fleet reads add only the ten protected dashboard GETs without opening the endpoint."
  }
  assert {
    condition = (
      toset(keys(aws_lambda_permission.ack_gateway)) == toset(keys(aws_apigatewayv2_route.ack)) &&
      toset([for p in aws_lambda_permission.ack_gateway :
        trimprefix(p.source_arn, "arn:aws:execute-api:us-east-1:123456789012:synthetic/dev/")
        ]) == toset([
        "GET/machines/*/incidents/*", "POST/machines/*/incidents/*/ack", "GET/operations/*/machines",
        "GET/operations/*/incidents", "GET/operations/*/technicians", "GET/operations/*/kpis",
        "GET/machines/*", "GET/machines/*/readings", "GET/machines/*/incidents", "GET/machines/*/maintenance",
        "GET/parts", "GET/parts/*/stock"
      ]) &&
      alltrue([for p in aws_lambda_permission.ack_gateway : p.source_account == var.expected_account_id])
    )
    error_message = "Every fleet route must have a separate account-, stage-, method- and path-bound Lambda permission."
  }
}

run "open_dashboard_rehearsal" {
  command = plan
  variables {
    enable_incident_api        = true
    open_incident_api_endpoint = true
    enable_fleet_read_routes   = true
    ack_google_web_client_id   = "197823740796-dkck6esc7b5o6gaemr4vv3eu9v79emki.apps.googleusercontent.com"
    ack_frontend_origin        = "http://localhost:5173"
  }
  assert {
    condition = (
      !aws_apigatewayv2_api.ack[0].disable_execute_api_endpoint && length(aws_apigatewayv2_route.ack) == 12 &&
      alltrue([for r in aws_apigatewayv2_route.ack : r.authorization_type == "JWT"]) &&
      toset([for r in aws_apigatewayv2_route.ack : r.route_key if startswith(r.route_key, "POST ")]) ==
      toset(["POST /machines/{id}/incidents/{incId}/ack"])
    )
    error_message = "The open dashboard must retain JWT on every route and expose only the contracted ACK as a write."
  }
}
