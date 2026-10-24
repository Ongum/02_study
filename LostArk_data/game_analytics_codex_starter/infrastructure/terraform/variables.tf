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
