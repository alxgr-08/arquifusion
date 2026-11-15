variable "enable_telemetry_lake" {
  description = "Plan the Academy Firehose/Glue pipeline against the approved, dedicated data bucket."
  type        = bool
  default     = false
}

module "telemetry" {
  source = "./telemetry"
  count  = var.enable_telemetry_lake ? 1 : 0

  account_id   = var.expected_account_id
  env          = var.env
  region       = var.region
  lab_role_arn = data.aws_iam_role.lab.arn
}

output "telemetry_lake" {
  description = "Producer and consumer settings, Glue schema and error prefix when the regional lake is enabled."
  value       = one(module.telemetry[*].settings)
}
