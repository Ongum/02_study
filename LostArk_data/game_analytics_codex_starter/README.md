# Entertainment Data Platform

> **Status: Local 180-day baseline plus one verified manual GCP batch; no recurring schedule.** A resumable one-shot full review-history Job is implemented and awaiting deployment/execution verification. No causal KPI result or business outcome is claimed.

[Current three-state implementation checklist](docs/STATUS_CHECKLIST.md)

A reusable, cloud-native analytics foundation for games and digital content. The first case study uses **Lost Ark public Steam signals** to investigate player feedback after updates and to prototype earlier detection of emerging issues.

## Why this exists
Data engineering should make better decisions possible. This project links **data acquisition → trusted marts → measurable analyses → actionable player-experience recommendations**, while building evidence of practical DA/DS/DE skills. The intended social value is to make player concerns visible sooner, support fairer and more inclusive LiveOps triage, and publish methodology honestly—not to claim access to game-internal user logs or actual operational outcomes.

## First case study: Lost Ark
- Source: Steam public reviews, Steam app news and current player-count snapshots (App ID `1599340`).
- Reviews: all available languages retained; initial English-language text classification; recommendations used for official negative/positive KPI.
- Primary KPI: after–before change in negative Steam review share (**percentage points**).
- Secondary: change in comparable observed concurrent-player snapshots; issue detection latency.
- Attribution caution: observational patch comparisons are **not causal proofs**. Public Steam reviews are a non-representative opt-in sample.

## Target architecture (manual cloud path verified; Scheduler absent)
`Cloud Scheduler → Cloud Run Jobs → GCS (Bronze) → BigQuery (Silver) → dbt (Gold) → analysis/dashboard`

Design for additional game/content connectors and marts; not for forcing all domains into one schema. ADR-001 approved hybrid bootstrap with Terraform-managed workload resources. No need to keep a personal computer always on after a verified deployment.

## Planned initial policy
| Parameter | Plan |
|---|---|
| GCP | Dedicated `lostark-ip-analyst` project, Tokyo (`asia-northeast1`); deployment verification pending |
| Review history | 90-day backfill after API validation |
| Review cadence | Every 6 h |
| Player snapshots | Hourly from system start, no invented past |
| News | Daily; patch effective dates separately verified |
| Reliability | bounded retries, idempotency, checkpoints, gap tracking |
| Budget | JPY 1,000/mo monitoring target, **not** a spending cap |
| Scheduler | Disabled until approved |

## Repo structure
- `src/entertainment_platform/common`: common retry/logging/checkpoint patterns
- `src/entertainment_platform/connectors`: source-specific extraction and contracts
- `src/entertainment_platform/jobs`: deployable entrypoints
- `dbt/models/staging`, `intermediate`, `marts`: reusable conformed layers and domain marts
- `analytics`: analysis and experiments
- `infrastructure/terraform`: infra-as-code after approval
- `config/sources`: source settings without secrets
- `tests`: unit/integration/quality checks
- `docs`: product definition, architecture, decision log, roadmap, metrics, impact evidence

## Getting started
1. Read `docs/PRODUCT_BRIEF.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`.
2. Open this repo in Codex and paste/use `CODEX_MASTER_PROMPT.md`.
3. Run the offline parser/storage tests; only then confirm API responses and GCP provisioning decisions.
4. Update `docs/STATUS_CHECKLIST.md` after each meaningful change; keep detailed measured history in `docs/PROGRESS.md`.

### Offline synthetic-fixture slice

```bash
python -m pip install -e ".[dev]"
pytest -q
python -m entertainment_platform.jobs.offline_steam_reviews \
  --input tests/fixtures/steam_reviews_page.synthetic.json \
  --output output/normalized_reviews.jsonl \
  --fetched-at 2026-10-21T12:00:00+00:00 \
  --run-id local-demo
```

Re-running the final command writes zero additional rows. The input is explicitly
synthetic. A single anonymous Lost Ark response has also passed the parser after
the observed Service API envelope was added. This does not establish cursor,
90-day coverage, cadence, or rate-limit behavior. See
`docs/API_SMOKE_TEST_PLAN.md` for the remaining gates.

For the bounded date-range diagnostic:

```bash
python -m entertainment_platform.jobs.steam_reviews_date_range_check \
  --start 2026-10-20T00:00:00+00:00 \
  --end 2026-10-20T23:59:59+00:00 \
  --run-id-prefix local-range-check \
  --max-pages 3 \
  --page-size 100
```

The job rejects caps above five pages, records only aggregate diagnostics to
stdout, and marks a page-cap stop separately from a terminal empty page.

For a bounded Updated-stream diagnostic:

```bash
python -m entertainment_platform.jobs.steam_reviews_updated_check \
  --run-id-prefix local-updated-check \
  --max-pages 2 \
  --page-size 5
```

This validates only a short prefix of update-time ordering. It does not advance
a production checkpoint or claim complete modification history.

The local checkpoint processor is intentionally separate from fetching:

```bash
python -m entertainment_platform.jobs.process_updated_batch \
  --input tests/fixtures/steam_reviews_page.synthetic.json \
  --output output/checkpoint_demo_reviews.jsonl \
  --checkpoint output/checkpoint_demo.json \
  --fetched-at 2026-10-21T12:00:00+00:00 \
  --run-id checkpoint-demo \
  --overlap-seconds 3600
```

The command validates all supplied pages and durably writes rows before it
atomically commits the checkpoint. The 3,600-second overlap is illustrative.

All Steam HTTP jobs use the shared bounded retry layer: 429, 5xx and transport
failures are retryable; ordinary 4xx errors are not. The default is at most four
attempts per page. See `docs/contracts/http_retry.md` for delay and
`Retry-After` behavior.

The integrated local Updated job is implemented in
`entertainment_platform.jobs.steam_reviews_incremental`. It requires an
existing bootstrap checkpoint and treats its page cap strictly as an
incomplete run. Only a validated terminal page or crossing the checkpoint's
overlap floor permits normalized write and checkpoint advancement. Synthetic
integration tests cover success, page-cap safety, later-page failure, and a
missing bootstrap; see `docs/contracts/steam_updated_incremental.md`. A full
live repeated run and production overlap selection remain unverified.

### Measured local dataset and marts

Two non-overlapping 90-day review ranges completed through their empty terminal
pages: 431 reviews for `2026-04-24..2026-07-22` and 450 for
`2026-07-23..2026-10-20`. Their idempotent merge contains 881 unique current
reviews. The ignored local outputs include history/current rows, daily and
weekly review marts, language rates, an English keyword issue baseline, and a
14-day publication-proxy update-impact draft. See `docs/progress_report.md` for
measured counts and limitations.

```bash
python -m entertainment_platform.analytics.review_analysis \
  --history output/silver_review_history_180d.jsonl \
  --current output/silver_reviews_current_180d.jsonl \
  --daily output/mart_review_daily_180d.jsonl \
  --weekly output/mart_review_weekly_180d.jsonl \
  --language output/mart_review_language_180d.jsonl \
  --issues output/mart_issue_language_180d.jsonl \
  --summary output/review_analysis_summary_180d.json

python -m entertainment_platform.analytics.update_impact \
  --reviews output/silver_reviews_current_180d.jsonl \
  --news output/silver_app_news_180d.jsonl \
  --output output/mart_update_impact_draft_180d.jsonl \
  --window-days 14
```

Two current-player snapshots and a bounded 180-day Steam news range were also
collected. Two observations at different times of day are not a trend. News
publication and announced maintenance times are not deployment completion;
all collected `effective_at` values remain null.

A live Updated-stream experiment was bootstrapped from the 180-day maximum
`updated_at` with an experimental 24-hour overlap. The first run reached its
floor in one 100-row page; an immediate replay wrote zero additional rows. A
roughly nine-hour time-separated run wrote three unseen keys and advanced the
watermark; its identical rerun wrote zero. Multi-day evidence is still required
before selecting a production overlap.

### One-command local analytics rebuild

The configured local DAG regenerates review marts, update classification,
event-time evidence, update impact/decision, player summary and collection
health without making network calls:

```text
python -m entertainment_platform.jobs.build_local_analytics --config config/local_analytics_pipeline.json --root . --run-id local-rebuild-YYYYMMDD --as-of 2026-10-22T00:00:00Z
```

Use the reporting cutoff that belongs to the data snapshot. The command writes
`output/local_analytics_run_manifest.json` with per-step metrics and artifact
hashes. Raw/Bronze and source review history files are not modified.

### One-command local incremental collection

This command calls only the bounded public Steam endpoints configured in
`config/local_incremental_collection.json`. It acquires an exclusive local
lock, attempts Updated reviews, current players and app news independently,
then writes a per-source manifest under `output/incremental_runs/`.

```text
python -m entertainment_platform.jobs.collect_local_incremental --run-id lostark-local-YYYYMMDD-NN
```

Exit code `0` means all three sources completed. Exit code `2` means at least
one source failed or reached its safety page cap; inspect the manifest instead
of treating partial Raw files as complete coverage. This command is not a
scheduler and does not create GCP resources.

### GCP collector adapter and first manual run

`entertainment_platform.jobs.collect_gcp_incremental` adapts the same bounded
collectors to GCS canonical state and Bronze objects, generation-based source
locks, deterministic BigQuery load job IDs and per-run manifests. Offline fake
tests cover recovery behavior. On 2026-10-24, one manual Cloud Run execution
completed in 24.35 seconds and produced 100 review rows, one current-player
snapshot and one recent-news row in BigQuery, with matching GCS state and
manifests. Scheduler was not created; this is one-run operational evidence, not
continuous reliability evidence.

```text
python -m pytest tests/test_gcp_collection.py -q
python -m entertainment_platform.jobs.collect_gcp_incremental --help
```

### One-shot full review-history backfill

The reviewed target is one fixed snapshot from `2021-01-01T00:00:00Z` through
`2026-10-24T02:30:16Z`. The API audit measured 203,166 matching reviews at the
cutoff. `collect_gcp_review_backfill` partitions the range by UTC month,
persists GCS state every 50 pages, resumes on rerun and replaces the dedicated
`silver_review_history` table only after terminal and integrity checks pass. A
measured GCP execution completed all 70 UTC-month partitions and loaded
**203,144 API-returned unique reviews**. Steam reported 203,166; the 22-row
(0.0108%) reported-but-not-returned difference is explicit in the final
manifest and was not imputed. BigQuery verification found zero duplicate
version groups, wrong-app rows, invalid timestamps or out-of-range rows. The
batch remains unscheduled.

```text
python -m entertainment_platform.jobs.collect_gcp_review_backfill --help
```

## Security / evidence
Never commit credentials, Terraform state, personal data or raw review dumps. Use GitHub OIDC / GCP Workload Identity Federation when ready. Explicitly distinguish **measured improvements**, **modeled potential**, and **proposed recommendations**. No causal or social-impact claim without supporting evidence.

This project currently lives inside the `Ongum/02_study` monorepo. GitHub only
discovers Actions workflows from the repository-root `.github/workflows/`
directory, so the project-local workflow is a reviewed template, not evidence
of an active CI run.

## References
- [Official Steam review API](https://partner.steamgames.com/doc/webapi/IUserReviewsService)
- [GCP scheduling Cloud Run Jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)
