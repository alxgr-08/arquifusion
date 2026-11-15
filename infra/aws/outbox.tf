variable "prepare_anomaly_outbox_consumer" {
  type        = bool
  default     = false
  description = "Prepare a disabled recovery mapping for durable anomaly records. Manual recovery works independently."
}

resource "aws_lambda_event_source_mapping" "anomaly_outbox" {
  count                              = var.prepare_anomaly_outbox_consumer ? 1 : 0
  enabled                            = false
  event_source_arn                   = aws_dynamodb_table.arquifusion.stream_arn
  function_name                      = aws_lambda_function.health_evaluator.arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
  function_response_types            = ["ReportBatchItemFailures"]
  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName = ["INSERT"]
        dynamodb  = { Keys = { SK = { S = [{ prefix = "OUTBOX#ANOMALY#" }] } } }
      })
    }
  }
}
