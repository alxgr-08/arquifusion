mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/LabRole" }
  }
  mock_resource "aws_amplify_app" {
    defaults = { default_domain = "example.amplifyapp.com" }
  }
}
mock_provider "archive" {}

variables { expected_account_id = "111122223333" }

run "hosting_absent_by_default" {
  command = plan
  assert {
    condition     = length(aws_amplify_app.web) == 0 && length(aws_amplify_branch.demo) == 0 && output.manual_web_origin == null
    error_message = "Hosting must be opted in independently."
  }
}

run "manual_static_branch_and_same_spa_rules_as_the_package" {
  command = plan
  variables { prepare_manual_web_hosting = true }
  assert {
    condition = (
      aws_amplify_app.web[0].platform == "WEB" &&
      !aws_amplify_app.web[0].enable_branch_auto_build && !aws_amplify_app.web[0].enable_auto_branch_creation &&
      !aws_amplify_branch.demo[0].enable_auto_build && !aws_amplify_branch.demo[0].enable_pull_request_preview &&
      aws_amplify_branch.demo[0].branch_name == "demo" && aws_amplify_branch.demo[0].stage == "DEVELOPMENT" &&
      one(aws_amplify_app.web[0].custom_rule).source == one(jsondecode(file("../hosting/amplify-rewrites.json"))).source &&
      one(aws_amplify_app.web[0].custom_rule).target == "/index.html" && one(aws_amplify_app.web[0].custom_rule).status == "200"
    )
    error_message = "Configure only the manual dev branch and the verified static-site rewrite."
  }
}
