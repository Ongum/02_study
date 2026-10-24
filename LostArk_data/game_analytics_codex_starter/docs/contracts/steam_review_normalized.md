# Normalized Steam review contract (local slice v0)

Status: implemented and exercised against two complete, non-overlapping 90-day
Lost Ark date ranges on 2026-10-21. The two runs stored 881 unique reviews and
reached validated empty terminal pages. Future schema variability, long-running
incremental behavior and historical edit completeness remain unverified.

## Grain and identity

One row is one observed version of a Steam review. The local idempotency key is
`(appid, recommendationid, updated_at)`. A later `updated_at` for the same
recommendation is a distinct history row. A future Silver current table selects
the most recently observed version; it must not overwrite history.

## Fields

| Field | Type | Constraint / meaning |
|---|---|---|
| `appid` | integer | Positive Steam application ID |
| `recommendationid` | string | Unsigned review ID; string avoids uint64 loss |
| `language` | string | Steam API language code |
| `review_text` | string | Raw public text; restricted access, never public by default |
| `created_at`, `updated_at` | UTC ISO-8601 string | Converted from required Unix seconds |
| `voted_up` | boolean | Steam recommendation, not NLP sentiment |
| `steam_purchase`, `received_for_free`, `written_during_early_access`, `refunded` | boolean | Required documented review attributes |
| `votes_up`, `votes_funny`, `comment_count` | non-negative integer | Observed counters |
| `weighted_vote_score` | number | Documented numeric helpfulness score |
| `fetched_at` | UTC ISO-8601 string | Time the page was obtained |
| `run_id` | string | Non-empty ingestion run identifier |
| `source_endpoint` | string | Versioned official endpoint |
| `page_fingerprint`, `payload_hash` | lowercase SHA-256 hex | Canonical JSON provenance fingerprints |

Full author Steam IDs are intentionally absent. Backfill raw responses use
content-addressed filenames and safe metadata sidecars in ignored local
storage. A later GCS implementation still needs approved restricted access,
lifecycle/retention policy and a deletion procedure.

## Validation behavior

Missing required fields, incorrect booleans, negative unsigned integers,
invalid IDs, naive fetch timestamps, and unsupported Unix timestamps fail the
whole page before output. Unknown fields are ignored by normalization but remain
covered by the page/payload hashes. Schema changes must be reviewed rather than
silently coerced.

The observed Service API payload has a top-level `response` object containing
`query_summary`, `reviews`, `cursor`, and `total_matching`. This envelope was
absent from the initial synthetic assumption and was added only after the first
real smoke response exposed it.

On the observed terminal cursor page, `query_summary.num_reviews` was zero and
Steam omitted `reviews` instead of returning `reviews: []`. The parser accepts
that omission only when `num_reviews == 0`; omission on a non-empty page remains
a validation error.
