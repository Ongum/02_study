# Local analytics pipeline contract

Status: implemented for already collected local data; no network calls.

The configured DAG runs input validation, review marts, update classification,
event-time evidence, update impact, decision mart, player summary and collection
health in order. Derived files are replaced atomically; Raw/Bronze and source
Silver histories are never modified.

Every completed step records metrics, output byte size and SHA-256 in
`local_analytics_run_manifest.json`. On the first failure, the manifest records
the failed step and exception, then the process exits unsuccessfully. Later
steps are not claimed as complete.

Reproduction:

```text
python -m entertainment_platform.jobs.build_local_analytics --config config/local_analytics_pipeline.json --root . --run-id local-rebuild-YYYYMMDD --as-of <UTC timestamp>
```

The explicit `as_of` controls collection freshness evaluation. Choose the data
cutoff being reported; do not substitute the current wall clock when rebuilding
a historical portfolio result.
