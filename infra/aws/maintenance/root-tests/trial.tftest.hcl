mock_provider "aws" {}

variables {
  operation    = "ENSAYO-0007"
  account_id   = "111122223333"
  table_arn    = "arn:aws:dynamodb:us-east-1:111122223333:table/af-dev-arquifusion"
  lab_role_arn = "arn:aws:iam::111122223333:role/LabRole"
  region       = "us-east-1"
  env          = "dev"
}

run "manual_targets_use_team_definition_and_one_scope" {
  command = plan
  module { source = "./maintenance" }
  assert {
    condition = (
      local.current && aws_lambda_function.trial.handler == "maintenance_trial.handler" &&
      aws_lambda_function.trial.function_name == "af-dev-maintenance-trial-0007" &&
      aws_lambda_function.trial.environment[0].variables.AF_MAINTENANCE_OPERATION == "ENSAYO-0007" &&
      aws_sfn_state_machine.preventive.type == "STANDARD" &&
      jsondecode(aws_sfn_state_machine.preventive.definition).States.LoadMachines.Resource == local.function_arn &&
      jsondecode(aws_sfn_state_machine.preventive.definition).States.SendDailySummary.Resource == local.function_arn
    )
    error_message = "Use a checked package, the team ASL and the manual Lambda for this single approved run."
  }
}

run "reject_shared_operation" {
  command = plan
  module { source = "./maintenance" }
  variables {
    operation = "MINA-NORTE"
  }
  expect_failures = [aws_lambda_function.trial]
}

run "reject_another_table" {
  command = plan
  module { source = "./maintenance" }
  variables {
    table_arn = "arn:aws:dynamodb:us-east-1:111122223333:table/other"
  }
  expect_failures = [aws_lambda_function.trial]
}
