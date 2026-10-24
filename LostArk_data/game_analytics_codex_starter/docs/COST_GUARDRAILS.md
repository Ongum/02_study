# Cost and workload guardrails

Status: **proposal; no GCP resources exist or are authorized**

## Before provisioning

- Owner approves the IaC approach, project ID, billing account/owner, resource
  names, deployment principal, and a written dry-run estimate.
- Scheduler remains disabled. The first live source calls and all transforms run
  manually with fixed page/byte/query limits.
- The JPY 1,000/month target is monitoring guidance, not a guaranteed cap.
  Budget alerts do not stop spend.

## Proposed bounded defaults

- Reviews: maximum 100 rows/page; explicit maximum pages and wall-clock duration
  per run; stop rather than silently claiming complete coverage.
- API: at most 4 attempts including the first; bounded exponential backoff with
  jitter; honor `Retry-After`; no deliberate limit testing.
- Bronze: immutable, compressed batches with lifecycle/retention and deletion
  policy selected before collection. Store provenance and hashes, restrict raw
  review text and identifiers, and never make raw data public.
- BigQuery: partition by event/observation date, require partition filters where
  practical, cluster only after measured benefit, set per-job maximum bytes
  billed, and expose dashboards through bounded aggregate tables.
- Cloud Run Jobs: explicit task timeout, memory/CPU ceiling, max retries, and no
  overlapping scheduled runs. Log counts and bytes, not sensitive payloads.
- Alerts: budget thresholds plus API/page/bytes/query-bytes/runtime anomaly
  alerts. Pause schedules manually when a guardrail triggers.

## Evidence needed to tune limits

Local response sizes, pages/day, compressed Bronze growth, BigQuery dry-run
bytes, job duration, retry frequency, and actual billed cost. Until measured,
all capacity and savings figures are proposals.

## Initial measured local evidence

The 2026-10-21 completed review collections measured 450 reviews in the latest
90 complete UTC days and 431 in the preceding non-overlapping 90 days. Each
range used five data pages plus one empty terminal request. Raw response plus
metadata storage measured 364,667 bytes and 355,739 bytes respectively. All 12
successful review requests completed on their first HTTP attempt. These local
bytes exclude cloud object overhead, compression, future growth and BigQuery
scans; no cloud cost was incurred or measured.
