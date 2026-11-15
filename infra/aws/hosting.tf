variable "prepare_manual_web_hosting" {
  type        = bool
  default     = false
  nullable    = false
  description = "Prepare the static Amplify app and its manual demo branch. Upload the reviewed web bundle separately."
}

resource "aws_amplify_app" "web" {
  count                       = var.prepare_manual_web_hosting ? 1 : 0
  name                        = "${local.prefix}-arquifusion-web"
  platform                    = "WEB"
  enable_branch_auto_build    = false
  enable_auto_branch_creation = false
  enable_branch_auto_deletion = false

  dynamic "custom_rule" {
    for_each = jsondecode(file("${path.module}/../hosting/amplify-rewrites.json"))
    content {
      source = custom_rule.value.source
      target = custom_rule.value.target
      status = custom_rule.value.status
    }
  }

  lifecycle {
    precondition {
      condition     = var.env == "dev" && var.region == "us-east-1"
      error_message = "Prepare hosting only in the ArquiFusion dev region."
    }
  }
}

resource "aws_amplify_branch" "demo" {
  count                       = var.prepare_manual_web_hosting ? 1 : 0
  app_id                      = aws_amplify_app.web[0].id
  branch_name                 = "demo"
  stage                       = "DEVELOPMENT"
  enable_auto_build           = false
  enable_pull_request_preview = false
}

output "manual_web_origin" {
  description = "Exact origin to register with the Google web client and API CORS after an approved deployment."
  value       = var.prepare_manual_web_hosting ? "https://${aws_amplify_branch.demo[0].branch_name}.${aws_amplify_app.web[0].default_domain}" : null
}
