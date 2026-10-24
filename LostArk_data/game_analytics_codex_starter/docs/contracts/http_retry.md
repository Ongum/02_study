# HTTP retry contract (local v1)

Status: implemented and deterministically unit-tested; no failure was induced
against the live Steam service.

## Retryable conditions

- HTTP 429
- HTTP 500–599
- URL/transport errors and timeouts

Other 4xx responses fail immediately. The default is three retries after the
first attempt, for at most four HTTP attempts per page.

## Delay behavior

Without a valid `Retry-After`, delay is bounded exponential backoff plus
positive jitter:

```text
min(base_delay × 2^(retry_number-1) + jitter, max_delay)
```

With `Retry-After`, both delta-seconds and HTTP-date forms are supported. If the
requested delay fits within the job's configured maximum, it is honored without
jitter. If it exceeds the maximum, the job stops without retrying early and
records the recommended delay for a later scheduler run.

Current example defaults:

| Setting | Value |
|---|---:|
| `max_retries` | 3 |
| `base_delay_seconds` | 1 |
| `max_delay_seconds` | 30 |
| `jitter_ratio` | 0.25 |

Each successful page metadata record contains total HTTP attempts and applied
retry delays. Failure metadata contains status when available, attempts,
retryability, safe error kind, and an over-limit recommended delay. It never
contains response bodies, URLs with parameters, API keys, review text, or user
identifiers.
