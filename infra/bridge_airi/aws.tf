resource "aws_sqs_queue" "dead" {
  name                      = "${local.prefix}-dead"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_sqs_queue" "delivery" {
  name                       = "${local.prefix}-delivery"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dead.arn, maxReceiveCount = 5 })
}
resource "aws_sqs_queue_policy" "from_lake" {
  queue_url = aws_sqs_queue.delivery.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Sid       = "FromDedicatedLake", Effect = "Allow", Principal = { Service = "s3.amazonaws.com" },
      Action    = "sqs:SendMessage", Resource = aws_sqs_queue.delivery.arn,
      Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = "arn:aws:s3:::${local.bucket}" } }
    }]
  })
}
resource "aws_s3_bucket_notification" "parquet" {
  bucket = local.bucket
  queue {
    id            = "arquifusion-parquet"
    queue_arn     = aws_sqs_queue.delivery.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "records/"
    filter_suffix = ".parquet"
  }
  depends_on = [module.lake, aws_sqs_queue_policy.from_lake]
}
resource "aws_cloudwatch_log_group" "auth" {
  name              = "/aws/lambda/${local.prefix}-auth"
  retention_in_days = 7
}
resource "aws_lambda_function" "auth" {
  function_name    = "${local.prefix}-auth"
  role             = data.aws_iam_role.lab.arn
  runtime          = "nodejs22.x"
  handler          = "authorizer.handler"
  filename         = "${local.build}/auth.zip"
  source_code_hash = filebase64sha256("${local.build}/auth.zip")
  memory_size      = 128
  timeout          = 8
  environment {
    variables = { GOOGLE_SUB = google_service_account.worker.unique_id, AUDIENCE = "https://arquifusion.airi-erp/bridge" }
  }
  depends_on = [aws_cloudwatch_log_group.auth]
}
resource "aws_api_gateway_rest_api" "bridge" {
  name             = "${local.prefix}-bridge"
  body             = file("${local.build}/gateway.json")
  fail_on_warnings = true
  endpoint_configuration { types = ["REGIONAL"] }
  depends_on = [aws_lambda_function.auth, aws_sqs_queue.delivery]
}
resource "aws_lambda_permission" "authorizer" {
  statement_id   = "OnlyBridgeAuthorizer"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.auth.function_name
  principal      = "apigateway.amazonaws.com"
  source_arn     = "${aws_api_gateway_rest_api.bridge.execution_arn}/authorizers/*"
  source_account = local.account
}
resource "aws_api_gateway_deployment" "bridge" {
  rest_api_id = aws_api_gateway_rest_api.bridge.id
  triggers    = { contract = filesha256("${local.build}/gateway.json") }
  lifecycle { create_before_destroy = true }
  depends_on = [aws_lambda_permission.authorizer]
}
resource "aws_api_gateway_stage" "bridge" {
  rest_api_id   = aws_api_gateway_rest_api.bridge.id
  deployment_id = aws_api_gateway_deployment.bridge.id
  stage_name    = "v1"
}
resource "aws_api_gateway_method_settings" "rate" {
  rest_api_id = aws_api_gateway_rest_api.bridge.id
  stage_name  = aws_api_gateway_stage.bridge.stage_name
  method_path = "*/*"
  settings {
    throttling_rate_limit  = 3
    throttling_burst_limit = 2
    data_trace_enabled     = false
    logging_level          = "OFF"
  }
}
output "bridge_api" { value = aws_api_gateway_stage.bridge.invoke_url }
