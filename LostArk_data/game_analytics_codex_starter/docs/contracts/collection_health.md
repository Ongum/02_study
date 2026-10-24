# Collection-health mart contract

Status: local configuration-driven prototype implemented.

Grain: one row per configured source at an explicit `as_of` timestamp. The
explicit timestamp makes a historical status report reproducible.

Fields cover target cadence, execution status, observed runs, freshness age,
expected/actual run coverage, cadence gaps, row count, checkpoint completion,
raw observations, replay/duplicate ratio, analysis readiness and quality flags.

`manual_local` is distinct from an operational schedule. A recent manual run
may be fresh at the chosen timestamp but still receives
`not_operationally_scheduled`. Missing historical player snapshots are never
filled with zero.

Initial readiness thresholds are configuration policy, not statistical proof:
12 Updated runs, 168 hourly player snapshots and 7 news runs. They are intended
to prevent two observations from being presented as a stable time series and
must be revised with measured cadence/reliability evidence.
