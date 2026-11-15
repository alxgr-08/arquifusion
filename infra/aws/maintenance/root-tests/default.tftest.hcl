mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/LabRole" }
  }
}
mock_provider "aws" { alias = "replica" }
mock_provider "archive" {}
variables {
  expected_account_id = "111122223333"
}
run "no_maintenance_runtime_by_default" {
  command = plan
  assert {
    condition     = length(module.maintenance_trial) == 0 && output.maintenance_trial == null
    error_message = "Manual maintenance resources require an explicitly configured synthetic operation."
  }
}
