terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.73, < 7.0"
    }
  }
}

variable "expected_account_id" {
  description = "AWS Academy account ID for the exclusive Terraform state bucket."
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

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type    = string
  default = "dev"
}

locals {
  bucket_name = "af-${var.env}-tfstate-${var.expected_account_id}"
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      project = "arquifusion"
      env     = var.env
      owner   = "rol1"
    }
  }
}

# The Academy SCP denies the Object Lock read required by the managed bucket resource.
removed {
  from = aws_s3_bucket.tfstate

  lifecycle {
    destroy = false
  }
}

data "aws_s3_bucket" "tfstate" {
  bucket = local.bucket_name

  lifecycle {
    postcondition {
      condition     = self.bucket_region == var.region
      error_message = "The existing state bucket must be in the configured region."
    }
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = data.aws_s3_bucket.tfstate.bucket

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = data.aws_s3_bucket.tfstate.bucket

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = data.aws_s3_bucket.tfstate.bucket

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket_name" {
  value = data.aws_s3_bucket.tfstate.bucket
}
