variable "expected_account_id" {
  description = "AWS Academy account ID. Providers refuse to operate in any other account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must contain exactly 12 digits."
  }
}

variable "aws_profile" {
  type    = string
  default = "default"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "replica_region" {
  type    = string
  default = "us-west-2"
}

variable "lab_role_arn" {
  description = "Verified existing execution role ARN for later service deployments. Bucket and table do not use it."
  type        = string
  default     = null
}

variable "operation_id" {
  type    = string
  default = "MINA-NORTE"
}
