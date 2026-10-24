# Local-to-BigQuery/dbt migration contract

Status: **design only**. No dataset, bucket, dbt adapter or cloud resource was
created.

| Local artifact / model | Grain | Proposed BigQuery layout |
|---|---|---|
| Bronze review body + sidecar | one fetched API page | GCS immutable object; external/load staging includes `run_id`, fetched time, request parameters, hashes |
| `silver_review_history` | one observed `(appid, recommendationid, updated_at)` version | partition `DATE(updated_at)`; cluster `appid, language, recommendationid`; restrict text/ID access |
| `silver_reviews_current` | latest observed row per `(appid, recommendationid)` | incremental dbt model or view over history; unique/not-null key tests |
| `silver_app_news` | one `(appid, news_id)` | partition `DATE(published_at)`; `effective_at` nullable and independently sourced |
| `fact_player_snapshot` | one `(appid, observed_at)` point | partition `DATE(observed_at)`; cluster `appid`; never fill historical gaps with zero |
| `mart_review_daily` | app × creation date × optional language | partition `review_date`; aggregate-only dashboard access |
| `mart_review_weekly` | app × UTC week | partition `week_start`; retain counts and Wilson intervals |
| `mart_issue_daily` | app × date × language × method × issue | partition date; classifier/method version required |
| `mart_update_impact` | event × window definition × cohort | cluster `appid, event_id`; retain samples, coverage, intervals and caveats |
| `mart_game_health_snapshot` | game × segment × scoring time | partition scoring date; retain signal coverage and scoring-policy version |
| `mart_risk_grade_performance` | model × version × cutoff/outcome window × grade | partition outcome-window end; only populated from matured labels |
| `mart_update_decision` | app × event | cluster `appid, primary_type`; keep prior, observed reaction and confidence separate |
| `mart_collection_health` | source × evaluation time | partition evaluation date; retain cadence, gaps, freshness, replay and readiness |

## Proposed dbt layers

1. `stg_steam_reviews`: strict types, UTC timestamps, source tests.
2. `int_review_versions`: uniqueness and payload-change checks.
3. `int_reviews_current`: deterministic latest-version selection.
4. `dim_update`: publication evidence plus independently validated nullable
   effective time; never silently promote a news date.
5. `mart_review_daily`, `mart_review_weekly`, `mart_issue_daily`,
   `mart_update_impact`, `fact_player_snapshot`.

Required tests: unique compound keys, not-null IDs/timestamps, accepted boolean
values, `updated_at >= created_at`, non-negative counts, current-row uniqueness,
relationships to app/event dimensions, source freshness and explicit coverage
gap checks. Partition filters and maximum bytes billed should be required in
operational queries. These choices remain proposals until GCP identifiers,
retention, IAM and IaC approach are approved.
