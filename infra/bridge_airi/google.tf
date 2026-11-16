resource "google_bigquery_dataset" "bridge" {
  dataset_id                 = local.dataset
  location                   = "US"
  description                = "Private ArquiFusion telemetry and daily KPI from AWS Academy"
  labels                     = local.tags
  max_time_travel_hours      = 48
  delete_contents_on_destroy = false
}
resource "google_service_account" "worker" {
  account_id   = "af-bridge-worker"
  display_name = "ArquiFusion private telemetry processor"
}
resource "google_service_account" "scheduler" {
  account_id   = "af-bridge-scheduler"
  display_name = "ArquiFusion hourly job launcher"
}
resource "google_service_account" "builder" {
  account_id   = "af-bridge-builder"
  display_name = "ArquiFusion bridge image builder"
}
resource "google_project_iam_member" "jobs" {
  project = local.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_bigquery_dataset_iam_member" "worker" {
  dataset_id = google_bigquery_dataset.bridge.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_artifact_registry_repository" "images" {
  repository_id = "af-bridge"
  location      = local.location
  format        = "DOCKER"
  description   = "ArquiFusion bridge worker"
  labels        = local.tags
}
resource "google_artifact_registry_repository_iam_member" "builder" {
  location   = local.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.builder.email}"
}
resource "google_project_iam_member" "build_logs" {
  project = local.project
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.builder.email}"
}
resource "google_cloud_run_v2_job" "bridge" {
  count               = var.image_digest == "" ? 0 : 1
  name                = "af-bridge"
  location            = local.location
  labels              = local.tags
  deletion_protection = false
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.worker.email
      timeout         = "600s"
      max_retries     = 1
      containers {
        image = "us-east1-docker.pkg.dev/airi-erp/af-bridge/worker@${var.image_digest}"
        env {
          name  = "BRIDGE_URL"
          value = aws_api_gateway_stage.bridge.invoke_url
        }
        resources { limits = { cpu = "1", memory = "512Mi" } }
      }
    }
  }
}
resource "google_cloud_run_v2_job_iam_member" "invoke" {
  count    = var.image_digest == "" ? 0 : 1
  project  = local.project
  name     = google_cloud_run_v2_job.bridge[0].name
  location = local.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
resource "google_cloud_scheduler_job" "hourly" {
  count            = var.image_digest == "" ? 0 : 1
  name             = "af-bridge-hourly"
  region           = local.location
  description      = "Drain the ArquiFusion queue once per hour"
  schedule         = "52 * * * *"
  time_zone        = "America/Lima"
  attempt_deadline = "60s"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/airi-erp/locations/us-east1/jobs/af-bridge:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.invoke]
}
