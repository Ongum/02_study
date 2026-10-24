variable "project_id" {
  description = "Dedicated GCP project for the Lost Ark analytics platform."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account used only for the project-scoped budget alert."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Primary workload and data region."
  type        = string
  default     = "asia-northeast1"
}

variable "monthly_budget_jpy" {
  description = "Monitoring target; GCP budgets do not hard-stop spending."
  type        = number
  default     = 1000
}

variable "raw_retention_days" {
  description = "Age after which Bronze objects are automatically deleted."
  type        = number
  default     = 30

  validation {
    condition     = var.raw_retention_days >= 30 && var.raw_retention_days <= 365
    error_message = "raw_retention_days must be between 30 and 365."
  }
}

variable "collector_image" {
  description = "Immutable Artifact Registry image URI. Empty keeps Cloud Run and Scheduler undeployed."
  type        = string
  default     = ""

  validation {
    condition     = var.collector_image == "" || startswith(var.collector_image, "asia-northeast1-docker.pkg.dev/${var.project_id}/collectors/")
    error_message = "collector_image must come from this project's collectors repository."
  }
}

variable "scheduler_enabled" {
  description = "Unpauses collection schedules only after manual job verification."
  type        = bool
  default     = false
}

variable "create_scheduler" {
  description = "Creates the Scheduler resource. Keep false for manual one-time collection."
  type        = bool
  default     = false

  validation {
    condition     = var.create_scheduler || !var.scheduler_enabled
    error_message = "scheduler_enabled cannot be true when create_scheduler is false."
  }
}

variable "create_review_backfill" {
  description = "Creates the manual one-shot full-history Steam review backfill job."
  type        = bool
  default     = false

  validation {
    condition     = !var.create_review_backfill || var.review_backfill_image != ""
    error_message = "review_backfill_image is required when create_review_backfill is true."
  }
}

variable "review_backfill_image" {
  description = "Immutable Artifact Registry image URI for the one-shot review backfill."
  type        = string
  default     = ""

  validation {
    condition     = var.review_backfill_image == "" || startswith(var.review_backfill_image, "asia-northeast1-docker.pkg.dev/${var.project_id}/collectors/")
    error_message = "review_backfill_image must come from this project's collectors repository."
  }
}

variable "review_backfill_start" {
  description = "Inclusive fixed UTC creation-time floor for the one-shot review backfill."
  type        = string
  default     = "2021-01-01T00:00:00+00:00"
}

variable "review_backfill_end" {
  description = "Inclusive fixed UTC creation-time ceiling for the one-shot review backfill."
  type        = string
  default     = "2026-10-24T02:30:16+00:00"
}
