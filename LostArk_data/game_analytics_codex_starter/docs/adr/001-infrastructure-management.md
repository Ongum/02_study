# ADR-001 Infrastructure management approach

Status: Accepted  
Date: 2026-10-21

## Context / problem

The confirmed target is a separate GCP project in `asia-northeast1` using Cloud
Scheduler, Cloud Run Jobs, GCS, BigQuery, and dbt. No paid resource may be
created without owner approval. The repository needs a reproducible, auditable
path without making experimentation unnecessarily slow.

## Options with tradeoffs

1. **Terraform-first.** Strongest reproducibility, reviewable IAM/cost settings,
   drift detection, and portfolio evidence. It adds state/bootstrap decisions
   and more setup before the first cloud smoke run.
2. **Console-first, document and codify later.** Fastest initial exploration,
   but prone to drift, missed IAM details, weak teardown reproducibility, and
   unreviewed cost changes.
3. **Hybrid bootstrap.** Manually create only the new project/billing linkage and
   Terraform state prerequisites when necessary; manage workload resources in
   Terraform. This handles organization/billing actions that often need owner
   access while keeping the data platform reviewable.

## Decision and reason

The owner approved option 3 on 2026-10-22, with workload resources
Terraform-managed from their first
creation. Treat unavoidable project/billing bootstrap actions as an explicit,
documented exception. This balances owner-controlled billing with least
privilege, repeatability, and a credible infrastructure portfolio artifact.

The dedicated project is `lostark-ip-analyst` in `asia-northeast1`. The project
and billing link are the approved manual bootstrap exception. The remote state
bucket is also a one-time bootstrap prerequisite; workload APIs, storage,
BigQuery, registry, IAM and budget monitoring remain Terraform-managed.

## Cost, security and reversibility

Keep Scheduler disabled, use separate service accounts and keyless CI, review
plans before apply, pin provider versions, and avoid storing secrets/state in
Git. A remote state backend itself is a resource requiring approval. Terraform
improves teardown visibility but deletion remains a separately reviewed action.

## How to validate

After approval: format and validate locally, review a no-secret plan, check IAM
against least privilege, estimate recurring cost, apply only owner-approved
resources, then perform a manual dry run with Scheduler disabled.
