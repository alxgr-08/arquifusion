terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73.0"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 8.11.0"
    }
  }
  backend "s3" {
    bucket              = "af-dev-tfstate-975050277936"
    key                 = "bridge/airi-erp/terraform.tfstate"
    region              = "us-east-1"
    profile             = "default"
    encrypt             = true
    use_lockfile        = true
    allowed_account_ids = ["975050277936"]
  }
}
locals {
  account  = "975050277936"
  region   = "us-east-1"
  project  = "airi-erp"
  location = "us-east1"
  dataset  = "af_bridge"
  prefix   = "af-dev-airi"
  bucket   = "af-dev-arquifusion-lake-975050277936-us-east-1-an"
  build    = "${path.module}/../../build/bridge-permanent"
  tags     = { project = "arquifusion", env = "dev", owner = "rol1", purpose = "bridge" }
}
provider "aws" {
  region              = local.region
  profile             = "default"
  allowed_account_ids = [local.account]
  default_tags { tags = local.tags }
}
provider "google" {
  project = local.project
  region  = local.location
}
variable "image_digest" {
  type    = string
  default = "sha256:2c32a3a6c5d2f473b847c4c4bbee9866d758748f513f3582a207535370c0b488"
  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "Use a published image digest."
  }
}
data "aws_iam_role" "lab" { name = "LabRole" }
module "lake" {
  source       = "../aws/telemetry"
  account_id   = local.account
  region       = local.region
  env          = "dev"
  lab_role_arn = data.aws_iam_role.lab.arn
}
