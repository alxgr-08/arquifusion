terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }

  # State stored in the same AWS S3 bucket (key gcp/terraform.tfstate)
  backend "s3" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
