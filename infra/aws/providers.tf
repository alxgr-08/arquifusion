locals {
  prefix = "af-${var.env}"
  tags = {
    project = "arquifusion"
    env     = var.env
    owner   = "rol1"
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}

provider "aws" {
  alias  = "replica"
  region = var.replica_region
  default_tags {
    tags = local.tags
  }
}
