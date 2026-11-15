mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::111122223333:role/LabRole"
    }
  }
}

mock_provider "aws" {
  alias = "replica"
}

variables {
  expected_account_id = "111122223333"
}

run "academy_default" {
  command = plan

  assert {
    condition     = aws_cloudwatch_event_rule.incident.state == "DISABLED"
    error_message = "The incident rule must be disabled in the default Academy deployment."
  }

  assert {
    condition = (
      aws_dynamodb_table.arquifusion.name == "af-dev-arquifusion" &&
      aws_dynamodb_table.arquifusion.deletion_protection_enabled &&
      aws_dynamodb_table.arquifusion.stream_view_type == "NEW_AND_OLD_IMAGES" &&
      length(aws_dynamodb_table.arquifusion.replica) == 0
    )
    error_message = "The Academy table requires delete protection, both stream images and a single region."
  }
}

run "optional_outbox_is_narrow_and_disabled" {
  command = plan

  variables {
    prepare_anomaly_outbox_consumer = true
  }

  assert {
    condition = (
      aws_lambda_event_source_mapping.anomaly_outbox[0].enabled == false &&
      contains(aws_lambda_event_source_mapping.anomaly_outbox[0].function_response_types, "ReportBatchItemFailures") &&
      jsondecode(one(one(aws_lambda_event_source_mapping.anomaly_outbox[0].filter_criteria).filter).pattern).eventName == ["INSERT"]
    )
    error_message = "Prepare only an explicitly disabled recovery mapping for new outbox records."
  }
}
