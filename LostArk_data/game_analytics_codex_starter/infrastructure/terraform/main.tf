locals {
  labels = {
    application = "lostark-analytics"
    environment = "portfolio"
    managed_by  = "terraform"
  }

  required_services = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ])
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "bronze" {
  name                        = "${var.project_id}-bronze"
  project                     = var.project_id
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = false
  }

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    condition {
      age = var.raw_retention_days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset" "analytics" {
  dataset_id                 = "game_analytics"
  friendly_name              = "Lost Ark Game Analytics"
  description                = "Silver and Gold tables for the Lost Ark public-data case study."
  location                   = var.region
  project                    = var.project_id
  delete_contents_on_destroy = false
  labels                     = local.labels

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository" "collectors" {
  location      = var.region
  repository_id = "collectors"
  description   = "Container images for bounded public Steam collectors."
  format        = "DOCKER"
  project       = var.project_id
  labels        = local.labels

  cleanup_policy_dry_run = true

  depends_on = [google_project_service.required]
}

resource "google_service_account" "ingest" {
  project      = var.project_id
  account_id   = "sa-steam-ingest"
  display_name = "Steam public-data ingestion"
}

resource "google_service_account" "transform" {
  project      = var.project_id
  account_id   = "sa-analytics-transform"
  display_name = "Silver and Gold transformations"
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "sa-job-scheduler"
  display_name = "Paused scheduler invoker"
}

resource "google_storage_bucket_iam_member" "ingest_bronze_creator" {
  bucket = google_storage_bucket.bronze.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_storage_bucket_iam_member" "transform_bronze_reader" {
  bucket = google_storage_bucket.bronze.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.transform.email}"
}

resource "google_bigquery_dataset_iam_member" "ingest_dataset_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_bigquery_dataset_iam_member" "transform_dataset_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.transform.email}"
}

resource "google_project_iam_member" "transform_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.transform.email}"
}

resource "google_project_iam_member" "ingest_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ingest.email}"
}

resource "google_project_iam_member" "runtime_log_writer" {
  for_each = toset([
    google_service_account.ingest.email,
    google_service_account.transform.email,
  ])

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${each.value}"
}

resource "google_billing_budget" "monthly_monitoring_target" {
  billing_account = var.billing_account_id
  display_name    = "Lost Ark analytics monthly monitoring target"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "JPY"
      units         = tostring(var.monthly_budget_jpy)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.8
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.required]
}

locals {
  collectors = {
    incremental = {
      schedule = "5 * * * *"
    }
  }

  review_schema = [
    { name = "appid", type = "INTEGER", mode = "REQUIRED" },
    { name = "recommendationid", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "updated_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "fetched_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "language", type = "STRING", mode = "REQUIRED" },
    { name = "voted_up", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "votes_up", type = "INTEGER", mode = "REQUIRED" },
    { name = "votes_funny", type = "INTEGER", mode = "REQUIRED" },
    { name = "weighted_vote_score", type = "FLOAT", mode = "REQUIRED" },
    { name = "comment_count", type = "INTEGER", mode = "REQUIRED" },
    { name = "steam_purchase", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "received_for_free", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "written_during_early_access", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "refunded", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "review_text", type = "STRING", mode = "NULLABLE" },
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "source_endpoint", type = "STRING", mode = "REQUIRED" },
    { name = "page_fingerprint", type = "STRING", mode = "REQUIRED" },
    { name = "payload_hash", type = "STRING", mode = "REQUIRED" },
  ]
  player_schema = [
    { name = "appid", type = "INTEGER", mode = "REQUIRED" },
    { name = "observed_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "player_count", type = "INTEGER", mode = "REQUIRED" },
    { name = "response_sha256", type = "STRING", mode = "REQUIRED" },
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "source_endpoint", type = "STRING", mode = "REQUIRED" },
  ]
  news_schema = [
    { name = "appid", type = "INTEGER", mode = "REQUIRED" },
    { name = "news_id", type = "STRING", mode = "REQUIRED" },
    { name = "title", type = "STRING", mode = "REQUIRED" },
    { name = "url", type = "STRING", mode = "REQUIRED" },
    { name = "is_external_url", type = "BOOLEAN", mode = "REQUIRED" },
    { name = "feed_label", type = "STRING", mode = "NULLABLE" },
    { name = "feed_name", type = "STRING", mode = "NULLABLE" },
    { name = "published_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "effective_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "candidate_event_type", type = "STRING", mode = "REQUIRED" },
    { name = "event_confidence", type = "STRING", mode = "REQUIRED" },
    { name = "fetched_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "source_endpoint", type = "STRING", mode = "REQUIRED" },
    { name = "payload_hash", type = "STRING", mode = "REQUIRED" },
  ]
}

resource "google_bigquery_table" "review_updates" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "silver_review_history_updates"
  deletion_protection = true
  schema              = jsonencode(local.review_schema)

  time_partitioning {
    type  = "DAY"
    field = "updated_at"
  }
  clustering = ["appid", "language", "recommendationid"]
}

resource "google_bigquery_table" "player_snapshots" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "fact_player_snapshot"
  deletion_protection = true
  schema              = jsonencode(local.player_schema)

  time_partitioning {
    type  = "DAY"
    field = "observed_at"
  }
  clustering = ["appid"]
}

resource "google_bigquery_table" "app_news" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "silver_app_news"
  deletion_protection = true
  schema              = jsonencode(local.news_schema)

  time_partitioning {
    type  = "DAY"
    field = "published_at"
  }
  clustering = ["appid", "candidate_event_type"]
}

resource "google_cloud_run_v2_job" "collector" {
  for_each = var.collector_image == "" ? {} : local.collectors

  project             = var.project_id
  name                = "steam-incremental-collector"
  location            = var.region
  deletion_protection = true

  template {
    parallelism = 1
    task_count  = 1

    template {
      service_account = google_service_account.ingest.email
      max_retries     = 0
      timeout         = "300s"

      containers {
        image = var.collector_image
        args  = ["--source", "scheduled"]

        resources {
          limits = {
            cpu    = "0.5"
            memory = "512Mi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GCP_BUCKET"
          value = google_storage_bucket.bronze.name
        }
        env {
          name  = "BQ_DATASET"
          value = google_bigquery_dataset.analytics.dataset_id
        }
        env {
          name  = "STEAM_APP_ID"
          value = "1599340"
        }
      }
    }
  }

  depends_on = [
    google_bigquery_table.app_news,
    google_bigquery_table.player_snapshots,
    google_bigquery_table.review_updates,
    google_project_iam_member.ingest_job_user,
    google_project_iam_member.runtime_log_writer,
    google_storage_bucket_iam_member.ingest_bronze_creator,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  for_each = google_cloud_run_v2_job.collector

  project  = var.project_id
  location = var.region
  name     = each.value.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_service_account_iam_member" "scheduler_token_creator" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

resource "google_cloud_scheduler_job" "collector" {
  for_each = google_cloud_run_v2_job.collector

  project          = var.project_id
  region           = var.region
  name             = "steam-incremental-collector"
  description      = "One bounded cadence-aware collector for Lost Ark public Steam data."
  schedule         = local.collectors[each.key].schedule
  time_zone        = "Asia/Tokyo"
  attempt_deadline = "320s"
  paused           = !var.scheduler_enabled

  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
    max_doublings        = 2
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${each.value.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
    google_service_account_iam_member.scheduler_token_creator,
  ]
}
