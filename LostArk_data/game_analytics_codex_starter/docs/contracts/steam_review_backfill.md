# Steam review fixed-range backfill contract

Status: **implemented, deployed and measured in one GCP batch** for Lost Ark
app `1599340`.

## Query and completion

- Official `IUserReviewsService/GetAppReviews/v1/` with `input_json`.
- `filter=Recent`, all languages, all purchase types, maximum 100 rows/page.
- Both inclusive `date_range_start` and `date_range_end` are always supplied.
- A run is complete only after a validated empty terminal page. Matching the
  first-page total alone is not treated as a terminal signal.
- `total_matching` must remain stable across the fixed-range run.
- Page/run limits are explicit; a cap is an incomplete state.

## Durability and resume

1. Write the exact response body under a content-addressed, create-only name.
2. Write a safe sidecar with range, page, response hash/size, attempt count,
   delays and only a cursor hash.
3. Validate and normalize the page.
4. Fsync deduplicated history rows.
5. Atomically commit the next cursor checkpoint.

The local checkpoint contains the returned cursor because it is needed to
resume, but the ignored `output/` directory is not a log or public artifact.
Rerunning a completed checkpoint makes zero HTTP requests. A replay before
checkpoint commit is safe because history dedupes by
`(appid, recommendationid, updated_at)`.

## One-shot GCP execution

- Fixed scope: `2021-01-01T00:00:00Z` through
  `2026-10-24T02:30:16Z`, inclusive.
- The operator starts one unscheduled Cloud Run Job.
- The Job partitions the range by UTC calendar month and keeps a separate
  cursor/data state for each partition.
- Local state advances after every durable page. GCS data and cursor state are
  persisted after each 50-page chunk; cursor state is written after data state
  so replay from an older cursor remains deduplicated.
- Completed partitions are combined only after an empty terminal page has been
  observed, observed rows equal stored unique reviews, and integrity checks
  find no duplicate versions, invalid timestamps or out-of-range rows.
- Steam's stable `total_matching` is retained as an independent source-quality
  measure. A reported-but-not-returned difference is recorded explicitly and
  is never imputed as a review row.
- A retry uses the persisted fixed specification and skips or resumes completed
  work. It does not change the analysis cutoff.
- Final acceptance requires the combined BigQuery row count to equal the sum of
  validated, actually returned partition rows. API-reported totals and their
  gaps remain separately auditable.

## Measured GCP result

- Execution `steam-review-backfill-7q5sk` completed successfully in 18m18.56s.
- All 70 UTC-month partitions reached an empty terminal page.
- BigQuery contains 203,144 rows and 203,144 unique review IDs.
- Duplicate version groups, wrong app IDs, invalid timestamps and out-of-range
  rows are all zero.
- Steam reported 203,166 matching rows; four partitions terminated after
  returning 22 fewer rows in total (0.0108%). The final manifest records both
  numbers and the per-partition gaps.
- GCS retains 148,682,182 bytes of immutable Raw evidence plus resumable state.
- No recurring Scheduler was created.

## Privacy and known limits

Raw API responses can contain public Steam author identifiers and therefore
remain ignored and access-restricted locally. Normalized rows omit author IDs.
A historical date-range query returns the latest review state visible at fetch
time; it cannot reconstruct earlier text/recommendation versions. The history
grain supports future versions captured by the Updated stream.

Official contract: <https://partner.steamgames.com/doc/webapi/IUserReviewsService>
