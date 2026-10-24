# Terraform foundation

ADR-001 option 3 (manual project/billing bootstrap, Terraform-managed workload
resources) was approved on 2026-10-22. This directory manages only the phase-1
foundation: required APIs, Bronze storage, BigQuery dataset, Artifact Registry,
least-privilege runtime service accounts/IAM and a JPY 1,000 monthly budget
alert. The budget is an alert, not a hard spending cap.

Cloud Run Job creation is gated by `collector_image`. Cloud Scheduler has a
separate `create_scheduler` gate and is omitted for the initial manual one-time
collection. If scheduling is later approved, create it first in a paused state
and enable it only after manual job verification. The Python GCS/BigQuery
adapter is covered by offline fakes; no deployed execution is claimed until an
actual run is checked.

The existing Bronze safety settings are preserved: 180-day lifecycle,
versioning enabled and seven-day soft delete. Reducing any of these settings is
a separate retention decision because it can make stored evidence harder to
recover.

The initial manual deployment was verified on 2026-10-24. Cloud Run Gen2
required at least 1 vCPU, so the job uses 1 vCPU, 512 MiB, one task, zero
platform retries and a 300-second timeout. The first execution succeeded; no
Scheduler resource was created.

The remote state bucket is a documented hybrid-bootstrap exception and must be
created once before `terraform init`. Keep `terraform.tfvars`, state and any
credentials out of Git.

```text
terraform init
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Every command must target the dedicated Lost Ark project explicitly. Never
change or import resources from the existing Contents Lakehouse project.
