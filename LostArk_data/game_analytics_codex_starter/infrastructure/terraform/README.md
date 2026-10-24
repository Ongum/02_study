# Terraform foundation

ADR-001 option 3 (manual project/billing bootstrap, Terraform-managed workload
resources) was approved on 2026-10-22. This directory manages only the phase-1
foundation: required APIs, Bronze storage, BigQuery dataset, Artifact Registry,
least-privilege runtime service accounts/IAM and a JPY 1,000 monthly budget
alert. The budget is an alert, not a hard spending cap.

Cloud Run Job and Cloud Scheduler definitions are gated by `collector_image`.
With its default empty value Terraform omits both resources. When an immutable
image URI is supplied, the job can be created while the scheduler still remains
paused unless `scheduler_enabled = true`. The Python GCS/BigQuery adapter is
covered by offline fakes; no deployed execution is claimed yet.

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
