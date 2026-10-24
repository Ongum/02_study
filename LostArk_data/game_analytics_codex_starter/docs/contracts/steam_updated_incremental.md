# Steam Updated incremental job contract

Status: **implemented locally, verified with synthetic failure cases, and used
for one bounded live run plus an immediate replay**. The live runs reached a
24-hour experimental overlap floor in one page. Time-separated recovery and the
production overlap duration remain unverified.

## Preconditions

- A valid `UpdatedReviewCheckpoint` for the same app must already exist.
- Bootstrap/backfill is a separate operation. The job never guesses a starting
  watermark from a capped newest-first prefix.
- `max_pages` is a cost/runtime safety bound, not evidence of completeness.
- The existing checkpoint's overlap is reused; a production overlap duration
  has not yet been selected.

## Transaction boundary

For each HTTP-success page, the job atomically writes the exact local raw body
and a privacy-safe metadata sidecar before parsing it. Metadata contains the
logical page number, HTTP attempts, retry delays, response size/hash and only a
hash of a returned cursor. It does not contain review text, author IDs or the
cursor itself.

The normalized batch and checkpoint are committed only after either:

1. a validated empty terminal page is observed; or
2. at least one validated review reaches or precedes
   `high_watermark_at - overlap_seconds`.

The whole collected batch is then validated again, deduplicated on
`(appid, recommendationid, updated_at)`, durably appended, and followed by an
atomic checkpoint replacement. Boundary-page rows older than the floor are
kept; deduplication makes replay safe.

## Incomplete and failed runs

- Page cap before a completion boundary: return `complete=false` with
  `completion_reason=page_cap`; do not write normalized rows or checkpoint.
- Fetch, JSON or schema failure: raise; keep already durable raw successes; do
  not write normalized rows or checkpoint.
- Repeated cursor before completion: raise; do not commit the batch.
- Missing bootstrap checkpoint or mismatched app: fail before making a request.

This local file boundary does not yet provide a distributed concurrency lock.
The future Cloud Run job must prevent overlapping executions before the same
checkpoint is shared by multiple workers.

## Reproduce without a live request

```bash
python -m pytest -q tests/test_steam_incremental.py
python -m ruff check src tests
python -m ruff format --check src tests
```

The tests use clearly synthetic fixture-derived pages and an injected fetcher.
They do not measure Steam availability, rate limits, cache behavior or a safe
production overlap.
