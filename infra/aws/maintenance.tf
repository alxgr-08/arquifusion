variable "maintenance_trial_operation" {
  description = "One approved ENSAYO-nnnn operation from the incident rehearsal kit. Null disables the manual workflow and detector."
  type        = string
  default     = null

  validation {
    condition     = var.maintenance_trial_operation == null || can(regex("^ENSAYO-[0-9]{4}$", var.maintenance_trial_operation))
    error_message = "Set exactly one approved ENSAYO-nnnn operation or leave the trial disabled."
  }
}

module "maintenance_trial" {
  count        = var.maintenance_trial_operation == null ? 0 : 1
  source       = "./maintenance"
  operation    = var.maintenance_trial_operation
  account_id   = var.expected_account_id
  table_arn    = aws_dynamodb_table.arquifusion.arn
  lab_role_arn = data.aws_iam_role.lab.arn
  region       = var.region
  env          = var.env
}

output "maintenance_trial" {
  description = "Regional manual targets scoped to the configured synthetic operation."
  value       = one(module.maintenance_trial[*].targets)
}
