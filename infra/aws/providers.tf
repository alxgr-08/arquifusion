locals {
  prefix = "af-${var.env}"
  tags = {
    project = "arquifusion"
    env     = var.env
    owner   = "rol1"
  }
}

provider "aws" {
  region              = var.region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = local.tags
  }
}

provider "aws" {
  alias               = "replica"
  region              = var.replica_region
  profile             = var.aws_profile
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = local.tags
  }
}
