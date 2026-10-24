output "project_number" {
  value = data.google_project.current.number
}

output "bronze_bucket" {
  value = google_storage_bucket.bronze.name
}

output "bigquery_dataset" {
  value = google_bigquery_dataset.analytics.dataset_id
}

output "artifact_repository" {
  value = google_artifact_registry_repository.collectors.name
}

output "runtime_service_accounts" {
  value = {
    ingest    = google_service_account.ingest.email
    transform = google_service_account.transform.email
    scheduler = google_service_account.scheduler.email
  }
}

output "collector_jobs" {
  value = { for key, job in google_cloud_run_v2_job.collector : key => job.name }
}

output "collector_schedules_enabled" {
  value = var.collector_image == "" || !var.create_scheduler ? false : var.scheduler_enabled
}

output "collector_scheduler_created" {
  value = var.collector_image != "" && var.create_scheduler
}

output "review_backfill_job" {
  value = try(google_cloud_run_v2_job.review_backfill["one-shot"].name, null)
}
