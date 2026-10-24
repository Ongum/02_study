# Metrics and evidence contracts

## negative_review_rate_delta (primary)
Take reviews created in a defined pre-update and post-update time window; classify negative via Steam `voted_up == false` (not NLP sentiment). Within each window compute `negative_count / all_review_count`. Delta = after_rate - before_rate; express as **percentage points**. Preserve numerator, denominator, `window_start`, `window_end`, data coverage, and review selection/filtering. Window default proposal: 14 days each, excluding the update calendar day if timing ambiguity requires; timezone UTC. Never average daily percentages without weights. Report Wilson confidence intervals or justified statistical intervals and small-sample caveats.

## player_count_change_pct (secondary)
Compute percentage change in mean *observed concurrent players* from time-of-week-comparable snapshots. Do not use incomplete day samples as daily averages. Historical observations before first ingestion are missing, not zero. Steam concurrent players are not DAU/retention.

## issue_detection_latency (secondary)
For reproducible evaluations: time from a *labeled signal onset* to first qualifying alert. Separate 'ingest lag', 'processing lag', and 'alert lag'. Real-world onset is not directly observable; label proxy and report error range. Pair with precision/recall, alert false positives and baseline detection method.

## social/career evidence
Record commits, tests, real API source coverage, achieved processing improvements (measured), issue detection quality on human-labeled data, data access limits, and whether any outcome was actually acted on by an operator (likely no). Unsupported societal impact belongs in proposals only.

## risk ranking metrics (implemented evaluator; no real churn labels yet)

- `AR = 2 * ROC_AUC - 1`; higher model scores must mean higher outcome risk.
- `CP@20 = outcomes in highest-risk 20% / all outcomes`.
- Report observed outcome/default-like rate per grade with numerator and
  denominator; `R1` is highest risk.
- Report Brier score or an equivalent calibration assessment. AR and CP@20
  measure ranking, not whether a predicted probability is honest.
- These metrics may be called churn metrics only when a forward-looking,
  matured per-user churn label exists. Current public Steam aggregates support
  game-health risk signals, not individual churn validation.
