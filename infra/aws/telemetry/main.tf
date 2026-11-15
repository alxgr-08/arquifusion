terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.73, < 7.0"
    }
  }
}

variable "account_id" {
  type = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use the ID of the approved Academy account."
  }
}

variable "env" {
  type = string

  validation {
    condition     = var.env == "dev"
    error_message = "The current lake consumer validates the dev environment."
  }
}

variable "region" {
  type = string

  validation {
    condition     = var.region == "us-east-1"
    error_message = "This regional pipeline and its consumers use us-east-1."
  }
}

variable "lab_role_arn" {
  description = "ARN read from IAM by the root provider in the expected account."
  type        = string
}

locals {
  prefix       = "af-${var.env}"
  bucket_name  = "${local.prefix}-arquifusion-lake-${var.account_id}-${var.region}-an"
  bucket_arn   = "arn:aws:s3:::${local.bucket_name}"
  data_prefix  = "records/"
  error_prefix = "errors/"
  bucket_tags = {
    project = "arquifusion"
    env     = var.env
    owner   = "rol1"
    purpose = "telemetry-lake"
  }
}

# The separate bootstrap creates the bucket. The general bucket resource refreshes
# Object Lock, a read denied in Academy. Verify the exact ARN and tags before any write.
data "aws_resourcegroupstaggingapi_resources" "lake" {
  resource_arn_list = [local.bucket_arn]
}

data "aws_s3_bucket" "lake" {
  bucket = local.bucket_name

  lifecycle {
    precondition {
      condition = anytrue([
        for bucket in data.aws_resourcegroupstaggingapi_resources.lake.resource_tag_mapping_list :
        bucket.resource_arn == local.bucket_arn && alltrue([
          for key, value in local.bucket_tags : lookup(bucket.tags, key, null) == value
        ])
      ])
      error_message = "The dedicated ArquiFusion lake bucket is missing or its ownership tags do not match. Check the bootstrap in telemetry/README.md."
    }
    postcondition {
      condition     = self.bucket_region == var.region
      error_message = "The dedicated bucket must be in the Academy pipeline region."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = data.aws_s3_bucket.lake.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
  skip_destroy            = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = data.aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = data.aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }

  lifecycle {
    prevent_destroy = true
  }
}
