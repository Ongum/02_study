# Implementation status checklist

Last updated: **2026-10-24 (Asia/Tokyo)**

This is the single operational checklist for implementation status. Detailed
measurements and historical execution notes remain in `docs/PROGRESS.md`; the
ten-stage product sequence remains in `docs/ROADMAP.md`.

## Status rules

- ✅ **완료 (Done):** the scoped item has reproducible evidence and no required
  work remains inside that scope.
- 🚧 **진행 중 (In progress):** implementation or verification evidence exists,
  but at least one stated completion condition remains.
- ⏳ **대기 (Waiting):** work has not started, or requires an explicit approval,
  external prerequisite or earlier checklist item.

Update this file at the end of every meaningful implementation change:

1. Change a status only when the evidence or blocker column is also updated.
2. Record measured results as measured; keep planned, illustrative and
   simulated results distinguishable.
3. Do not mark cloud deployment, tests or business impact complete without an
   actual verified result.
4. Add material architecture decisions to `docs/DECISIONS.md` as well.
5. Keep exactly one current next action in the section below.

## Current status

| ID | Work item | Status | Evidence or current blocker | Completion condition |
|---|---|---|---|---|
| ST-01 | Product question, KPI and evidence boundaries | ✅ 완료 | Product brief and metrics define negative-review delta, player snapshot change and issue latency without causal overclaim | Definitions remain referenced by implementation and reporting |
| ST-02 | Steam source, game and language scope | ✅ 완료 | Lost Ark app 1599340; all-language ingestion and English-first analysis recorded in decisions | Source legitimacy and constraints remain documented |
| ST-03 | Review history baseline | ✅ 완료 | Two terminal-verified 90-day ranges produced 881 unique review versions across 180 days | Reproduction and coverage evidence retained |
| ST-04 | Local incremental collectors | ✅ 완료 | Updated reviews, current players and news have bounded retry, checkpoint, provenance and one-command local execution | Local failure/replay tests continue passing |
| ST-05 | Local Silver/Gold-like analytics | ✅ 완료 | Review, language, issue, update-impact, decision and collection-health outputs rebuild through an eight-step offline DAG | Manifested offline rebuild remains reproducible |
| ST-06 | Update/VOC prototype and evidence separation | ✅ 완료 | Expected risk, observed reaction and confidence are separate; announced time is not promoted to `effective_at` | Prototype retains non-causal labels and null missing signals |
| ST-07 | Initial local collection-health assessment | ✅ 완료 | Fixed-clock freshness, gaps, replay ratio and readiness statuses were generated | Results remain labeled as manual local evidence |
| ST-08 | Offline GCP storage/load adapter | ✅ 완료 | GCS state/locks, immutable Raw retry, zero-row state and deterministic BigQuery load behavior covered by fake-client tests | Offline adapter regression tests continue passing |
| ST-09 | Multi-day cadence and Updated overlap selection | 🚧 진행 중 | Immediate and roughly nine-hour replays measured; three player snapshots exist, but no multi-day same-time series | Enough scheduled or controlled observations to justify production overlap and cadence tolerances |
| ST-10 | Container and Terraform deployment validation | ✅ 완료 | Cloud Build succeeded for immutable image digest `sha256:47e8c5…db84a`; Terraform validate passed. The destructive first plan was rejected, the revised plan preserved Bronze recovery settings, and post-deployment detailed plan exited 0 | Image provenance, configuration validation and no-drift plan remain reproducible |
| ST-11 | Effective update times and human validation | 🚧 진행 중 | Announced maintenance/start evidence exists; all `effective_at` values remain null and classifier rows are not human-validated | Independent effective-time evidence and documented review of event/issue labels |
| ST-12 | GCP foundation apply and least-privilege verification | ✅ 완료 | One manual Cloud Run execution completed in 24.35s with one successful task. GCS Raw/state/manifests and BigQuery rows were verified; ingest roles are limited to BigQuery jobUser, logging writer, bucket objectUser and dataset WRITER. Scheduler was not created | Preserve reviewed IAM and require a new decision before scheduling or reducing retention |
| ST-13 | dbt BigQuery models and quality tests | ⏳ 대기 | Local marts exist; dbt project/models have not been implemented against BigQuery | dbt compile/run/tests succeed on reviewed Silver inputs |
| ST-14 | Scheduled reliability and cloud-cost measurement | ⏳ 대기 | Only manual local cadence evidence exists; no deployed execution or measured cloud cost | Multi-day job history, gaps, latency and cost reported from verified deployment |
| ST-15 | Dashboard delivery choice and implementation | ⏳ 대기 | Public dashboard versus bounded static report remains open as D-011 | Owner selects approach and privacy/cost-bounded artifact is verified |
| ST-16 | Portfolio publication and impact evidence | 🚧 진행 중 | Initial project was published to GitHub `Ongum/02_study` on `master` in commit `e96539c`; career/social evidence and an audited demo remain incomplete | Audited demo, reproduction guide and evidence-limited impact narrative are complete |

## Current next action

**ST-13:** implement the first dbt staging models and schema tests for the three
verified BigQuery tables. Compile first; do not schedule recurring collection.

## Verification snapshot

Measured on 2026-10-24:

- `python -m pytest -q` → **61 passed in 0.82s**.
- `python -m ruff check src tests` → passed.
- `python -m ruff format --check src tests` → 55 files already formatted.
- `python -m compileall -q src tests` → passed.
- Collector CLI `--help` → passed without credentials or network access.
- Terraform `fmt -check` → passed with bundled Terraform 1.15.9.
- Docker image run → not verified; local Docker daemon was not running.
- Terraform `validate` → not verified; Google provider plugin did not respond.
- GCP apply/deployment/API mutation → not performed.
- GitHub initial publication → commit `e96539c` pushed to
  `Ongum/02_study` `master`; ignored Raw/output/Terraform state files were not
  included.
- GitHub Actions → not active for this nested project; a reviewed monorepo-root
  workflow still needs to be installed. The project-local workflow file is a
  template only.
- Cloud Build → succeeded for the current collector source; immutable digest
  `sha256:47e8c5…db84a` is deployed.
- Terraform apply → budget monitoring target and one Cloud Run Job exist;
  Scheduler does not exist. Post-deployment detailed plan exited **0**.
- Manual Cloud Run execution `steam-incremental-collector-x9qnd` → completed in
  **24.35 seconds**, one task succeeded.
- Verified BigQuery rows → reviews **100**, player snapshots **1**, news **1**.
- Measured current-player observation → **6,369** at
  `2026-10-24T02:16:20.066178Z`; this single point is not a trend.

