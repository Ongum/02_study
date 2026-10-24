# Ten-stage development plan

| Step | Decision / build | Completion evidence |
|---|---|---|
| 01 | Business question and KPIs | KPI definitions and limits in metrics doc — CONFIRMED |
| 02 | Game and language | Lost Ark 1599340, all-language ingest / English-first NLP — CONFIRMED |
| 03 | Sources, cadence, incremental policy | Two non-overlapping 90d review ranges completed (881 reviews total); three current-player snapshots, 180d news backfill and repeated Updated continuations measured; a locked one-command local cycle is verified, while scheduled multi-day observations remain pending |
| 04 | GCP architecture / IAM / costs / IaC | Dedicated Tokyo project foundation, budget alert and one manual Cloud Run Job are Terraform-managed; least-privilege runtime path and no-drift plan verified, Scheduler intentionally absent |
| 05 | Data grain and data contracts | Local review history/current, player snapshot, news candidate and aggregate grains implemented; BigQuery mapping documented; effective update dates remain open |
| 06 | Robust ingest | Local recovery tests plus one deployed execution verified Raw/state/manifest writes and BigQuery loads for reviews, players and news; repeated deployed recovery remains unmeasured |
| 07 | dbt models and quality | Local daily/weekly/language/issue/decision/health marts, quality checks and one-command manifested rebuild implemented; dbt project/models not yet built |
| 08 | Updates/VOC analysis | 180d baseline, publication-proxy impact, multi-label classification, announced-time evidence and confidence-gated decision mart implemented; aligned player/issue signals, observed effective times, human validation and causal design pending |
| 09 | Reliability/performance and costs | Local health mart and one 24.35-second Cloud Run execution measured; multi-run reliability and actual billed cost remain pending, with no Scheduler created |
| 10 | GitHub/demo/career & social value | honest portfolio report and reproducible demo |

Do not silently promote a planned deliverable to implemented. Prefer small vertical slices. If requirements change, record an ADR and revisit acceptance criteria.

Current execution status is maintained in `docs/STATUS_CHECKLIST.md` using the
three states 완료, 진행 중 and 대기. This roadmap defines sequence and acceptance
scope rather than duplicating live status.
