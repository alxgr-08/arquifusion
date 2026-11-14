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
  description = "AWS Academy LabRole ARN. Learner Lab cannot create IAM roles, so every service assumes this one."
  type        = string
}

variable "operation_id" {
  type    = string
  default = "MINA-NORTE"
}
