# Progress — evidence-based

## Current stage

Roadmap steps 03, 05, 06, 07 and 08 now have measured local evidence: complete
180-day review coverage assembled from two terminal-verified ranges, local
history/current and aggregate marts, two current-player snapshots, and a
bounded news/event-candidate collection. Longer repeated Updated/player observations,
validated patch effective times, dbt/BigQuery implementation and deployment
remain open. No cloud resource has been created.

## 2026-10-21 repository audit and first implementation

Reviewed every starter source/config/document/workflow file present before the
change, with `AGENTS.md` and `CODEX_MASTER_PROMPT.md` taking precedence.

Confirmed design: Lost Ark app 1599340; all-language source collection with
English-first NLP; Scheduler → Cloud Run Jobs → GCS Bronze → BigQuery Silver →
dbt Gold; independent domain marts with reusable connectors/utilities; initial
90-day review backfill and 6h/1h/24h cadences; bounded retry/checkpoint/rerun;
Tokyo region and a separate GCP project; the three evidence-limited KPIs.

Still open: IaC choice (ADR-001 is only a proposal), GCP identifiers/IAM/billing
owner, dashboard publication, exact retention/access settings, and all live API
behavior/coverage. Terraform, dbt models, GCP deployment, and KPI analysis are
not implemented.

Implemented and measured locally:

- Synthetic-only Steam review page fixture; it is not a captured response.
- Typed validation/normalization with UTC timestamps, provenance hashes, and no
  normalized author Steam ID.
- Dependency-light durable JSONL adapter and history-aware deduplication on
  `(appid, recommendationid, updated_at)`.
- Runnable offline job wiring parser → normalized rows → local JSONL.
- Partial normalized contract, API smoke-test checklist, cost guardrails, and
  proposed infrastructure-management ADR.
- `python -m pytest -q`: **3 passed in 0.10s**.
- `python -m ruff check src tests`: **passed**.
- `python -m ruff format --check src tests`: **8 files already formatted**.
- CLI verification: first run wrote **2 synthetic rows**; identical retry wrote
  **0 rows**; resulting file contained **2 rows**.

The test timings and row counts above are measured local test results, not
Steam production measurements or business outcomes.

### Initial live Steam review smoke — measured

One anonymous HTTPS request was made to the official
`IUserReviewsService/GetAppReviews/v1/` endpoint for app 1599340 with a single
review requested. The raw response is retained only under ignored local
`raw_data/`; neither its review text nor author ID was printed or committed.

- HTTP status: **200**; request count: **1**; response bytes: **761**.
- SHA-256:
  `32952496ee9407a5c8828388211e92ca4d7b3e5118e03bac5c1fafe8235db0bf`.
- Observed top-level field: `response`; its fields were `cursor`,
  `query_summary`, `reviews`, and `total_matching`.
- Initial parser result: **failed truthfully** because the synthetic assumption
  omitted the top-level Service envelope.
- After contract correction, the same saved response was reprocessed without a
  second request: **1 normalized row written**, with no author/Steam ID field.
- A second request used page 1's returned cursor without logging it: HTTP 200,
  **1 review**, **1,118 bytes**, schema validation passed, and one additional
  normalized row was written.
- Across the two pages: cursor advanced, review-ID overlap was **0**, Recent
  creation times were non-increasing, and the normalized output held **2 rows**.
- Tests after the cursor-safe implementation: `python -m pytest -q` — **5
  passed**; Ruff checks passed.

This establishes one observed response shape and one cursor transition. It does
not establish pagination termination/completeness, stable fields, 90-day
availability, rate-limit behavior, or production readiness.

### Bounded UTC date-range validation — measured

A commented, typed diagnostic job now enforces `max_pages <= 5`,
`page_size <= 100`, paired inclusive Unix boundaries, bounded cursor traversal,
and aggregate-only logging. The range `2026-10-20T00:00:00Z` through
`2026-10-20T23:59:59Z` was run with a three-page cap and 100-row page size.

- Successful integrated run: **2 requests**, **4 unique reviews**, reported
  `total_matching=4`.
- All 4 `timestamp_created` values were within the requested range.
- Duplicate reviews: **0**; cursor advanced: **yes**.
- Terminal page observed: **yes**; stopped by page cap: **no**.
- Observed timestamps ranged from `2026-10-20T17:42:43Z` to
  `2026-10-20T21:48:24Z`.
- The first attempt made two requests and truthfully failed when the zero-row
  terminal response omitted `reviews`. D-014 records the guarded correction;
  the successful run was repeated, for **4 date-range requests total**.
- Current checks: `python -m pytest -q` — **8 passed**; Ruff lint/format passed.

These four reviews are a measured one-day observation, not a representative
daily average and not a basis for claiming that a 90-day backfill contains 360
reviews. A capped 90-day probe is still required.

### 90-day capped volume probe — measured

One request, capped to one 100-row page, covered the 90 complete UTC days from
`2026-07-23T00:00:00Z` through `2026-10-20T23:59:59Z`.

- HTTP 200; schema validation passed; request count: **1**.
- API-reported `total_matching`: **450** at the observation time.
- Rows actually fetched: **100**; unique: **100**; outside range: **0**.
- Estimated data pages at 100 rows/page: **5**, plus a possible terminal-page
  request if collection continues until an empty page.
- First-page size: **75,013 bytes**. A simple five-page multiplication is
  **375,065 bytes (0.358 MiB)** uncompressed.
- The page cap stopped the job as intended. Full 90-day coverage was **not**
  collected or verified.
- First-page timestamps covered `2026-09-29T19:01:20Z` through
  `2026-10-20T21:48:24Z`; older rows are expected on later pages.

The 450 count is a time-specific API observation. The byte estimate is
illustrative, based on one page, and must not be reported as measured full
storage volume.

### Updated-stream bounded probe — measured

Two requests were made with Steam's documented Updated filter (`filter=2`), a
five-row page size, and a two-page cap.

- Rows observed: **10**; unique reviews: **10**; duplicates: **0**.
- `timestamp_updated` was non-increasing across both pages.
- Cursor advanced on every observed page.
- **4** rows had `updated_at > created_at`; **6** had equal timestamps; none had
  an update time before creation.
- Observed update range: `2026-10-20T14:26:47Z` through
  `2026-10-21T12:09:54Z`.
- Combined response bytes: **13,627**.
- The job stopped by its two-page cap; no terminal page was observed.
- `2026-10-20T14:26:47Z` is the bounded-prefix coverage floor;
  `2026-10-21T12:09:54Z` is the next high-watermark candidate. No production
  checkpoint was advanced.
- Current checks: `python -m pytest -q` — **10 passed**; Ruff lint/format passed.

This confirms observed ordering and that the stream can contain both edited and
new reviews. It does not prove complete edit history or safe recovery after a
checkpoint; a later repeated run and overlap policy are still required.

### Local durable Updated checkpoint — implemented and tested

The local transaction order is now explicit: validate every page → durably
append deduplicated rows → atomically replace the checkpoint. The checkpoint
stores the greatest fully processed `updated_at`, not the oldest row in a
newest-first prefix. Its configurable overlap replays rows before the previous
high-watermark so late/cache effects can be handled by idempotent storage.

Measured synthetic verification:

- Invalid second page: **no output and no checkpoint**.
- Injected partial write failure: one row reached disk, **no checkpoint**.
- Recovery rerun: remaining row written, final output **2 rows**, checkpoint
  committed only after success.
- Safe identical rerun: **0 rows written**, high-watermark unchanged.
- CLI first run: 2 rows written and checkpoint advanced.
- CLI retry: 0 rows written and checkpoint not advanced.
- Synthetic demo overlap: 3,600 seconds. This is not the selected production
  overlap policy.
- Current checks: `python -m pytest -q` — **13 passed**; Ruff lint/format passed.

The implementation is local only. It is not yet wired to a complete live
Updated traversal, scheduler, concurrent-run lock, GCS, or BigQuery.

### Bounded HTTP retry layer — implemented and tested

The standard-library HTTP client now applies a maximum of three retries after
the first attempt. It retries only 429, 5xx, and transport/timeout failures;
other 4xx responses fail immediately. Page metadata distinguishes logical pages
from actual HTTP attempts and records safe delay values without response bodies
or identifiers.

Deterministic tests use injected open/sleep/random/time functions and perform no
real waiting:

- 429 delta-seconds `Retry-After`: honored.
- 429 HTTP-date `Retry-After`: honored.
- 5xx sequence: 1s then 2s exponential delays with test jitter disabled.
- Transport failure: stopped after the configured total attempts.
- HTTP 400: failed immediately without retry.
- `Retry-After: 120` with a 30s job cap: no early retry; execution stopped and
  the recommended 120s delay was retained.
- Current checks: `python -m pytest -q` — **19 passed**; Ruff lint/format passed.

These are controlled unit-test results. No rate limit or server failure was
deliberately triggered against Steam.

### Integrated local Updated job — implemented and tested

The local job now wires request construction → bounded fetch → durable raw body
and safe metadata → page validation → completion-boundary detection → durable
deduplicated normalized write → atomic checkpoint. It requires an existing
bootstrap checkpoint, so a capped newest-first prefix cannot silently become a
false starting point.

Measured synthetic/injected verification (no network request):

- Two pages crossing the saved one-hour demo overlap floor: **4 rows observed,
  4 rows written**, checkpoint advanced from `02:00Z` to `04:00Z`.
- One-page safety cap before the floor: `complete=false`; raw body/metadata
  retained; **0 normalized rows** and checkpoint bytes unchanged.
- Injected second-page failure: first raw page retained; no normalized file;
  checkpoint bytes unchanged.
- Missing bootstrap checkpoint: failed before the injected fetcher was called.
- Shared request builder moved to the Steam connector boundary so smoke and
  incremental jobs apply one parameter contract.
- Current checks: `python -m pytest -q` — **23 passed in 0.35s**; Ruff lint and
  format checks passed; Python source compilation passed.

This verifies the local transaction semantics only. The integrated job has not
yet completed a live repeated watermark run, selected a production overlap, or
implemented distributed locking/cloud storage.

### Complete local review baseline and analytics — measured

The fixed-range collector now uses content-addressed create-only raw files,
safe metadata, durable normalized writes and an atomic per-page cursor
checkpoint. It completed two disjoint ranges and observed a validated empty
terminal page for each:

- `2026-07-23..2026-10-20`: API total **450**, stored **450**, six requests.
- `2026-04-24..2026-07-22`: API total **431**, stored **431**, six requests.
- Combined 180 days: **881 unique reviews/versions**, zero duplicate version
  keys, zero range violations, zero missing required fields, zero invalid or
  reversed timestamps.
- Completed rerun of the latest range: zero requests and zero writes.
- Current-state materialization: **881 rows**. Historical backfill exposes 54
  reviews whose visible `updated_at` is later than creation, but cannot recover
  their earlier text/recommendation versions.

Measured 180-day descriptive results:

- Positive **427**, negative **454**, negative rate **51.53%** (Wilson 95% CI
  48.23%–54.82%).
- 21 languages. Largest cohorts: English 418 (55.26% negative), French 99
  (72.73%), Spanish 99 (26.26%), Brazilian Portuguese 84 (30.95%), German 69
  (76.81%). Language comparisons remain self-selected and confounded.
- 179 days and 27 UTC weeks contained reviews; weekly count median **35**, range
  **6–47**. Every daily count was below 30; 9 weekly rows were below 30.
- The two 90-day halves differed by **+0.96 percentage points**, approximate
  95% interval **−5.65 to +7.56 pp**; this is descriptive and not an update
  effect.
- English negative-review keyword baseline: 231 negatives, 104 matched at least
  one category. Leading multi-label matches were balance/gameplay 52,
  content/progression 47, monetization/grind 43. The rules are not a validated
  classifier; 127 negatives were unclassified.
- Average density remained about 4.89 reviews/day. A 7-day pre/post side has an
  estimated worst-case difference half-width of **±23.68 pp** and a 14-day side
  **±16.74 pp**. Extending history adds events/baseline but does not increase
  reviews near one event.

Other source and Gold-like prototype evidence:

- One official current-player snapshot: **5,260** at
  `2026-10-21T13:38:20.197810Z`; identical run ID rerun made zero requests.
- One news request covered the 180-day floor and stored **10** posts, including
  8 title-derived update candidates. Author/content were omitted from Silver.
- The 14-day update-impact draft produced 8 rows; 7 had complete review windows,
  one was post-window censored/low-sample, and **0** had a validated effective
  time. Publication-proxy deltas are not deployment or causal results.
- A live Updated checkpoint was bootstrapped at `2026-10-20T21:48:24Z` with an
  experimental 24-hour overlap. First run: one page/100 rows, floor reached,
  100 rows written, high watermark advanced to `2026-10-21T13:20:57Z`.
  Immediate replay: one page/100 rows observed, **0 written**, checkpoint not
  advanced. Comparison found 60 exact baseline versions, 37 reviews created
  before the 180-day cohort and recently visible in Updated, and 3 reviews
  created after the cohort end. No new version of a baseline review appeared
  during this short interval.
- Current checks after implementation at that stage: **34 tests passed**; Ruff lint/format and
  Python compile checks passed.

The 365-day expansion is deferred. At the observed rate it would be roughly
1,786 reviews and 18 data pages (illustrative), useful mainly for more historical
events; it would not improve the sample immediately around a fixed update.

Unverified assumptions and skipped checks: full all-history Updated traversal,
multi-day live watermark recovery, production overlap/retry tuning, real
429/cache behavior, comparable-time player series, independently validated patch
effective times, multilingual classifier quality, raw retention automation,
dbt/Terraform validation, GCP cost/deployment, alert latency and social-impact
outcomes.

Reproduce:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m entertainment_platform.jobs.offline_steam_reviews --input tests/fixtures/steam_reviews_page.synthetic.json --output output/normalized_reviews.jsonl --fetched-at 2026-10-21T12:00:00+00:00 --run-id local-demo
```

The previously listed bootstrap/time-separated action was completed by the
2026-10-22 continuation below. Production overlap selection still requires
multi-day evidence and does not require GCP resources.

### 2026-10-22 time-separated live continuation — measured

- Updated run 03 fetched one 100-row page, crossed the saved 24-hour overlap
  floor, wrote **3** unseen version keys, and advanced the watermark from
  `2026-10-21T13:20:57Z` to `2026-10-21T19:25:31Z`.
- Re-running run 03 observed 100 rows and wrote **0**, confirming storage
  idempotency. It still made one HTTP request, so HTTP-free same-run recovery
  is not claimed for this path.
- Updated cumulative output is **103 rows / 103 review IDs**. Against the
  881-review baseline: 60 are exact replayed versions, 38 were created before
  the baseline, and 5 were created after its end. No new version of a baseline
  review has yet been observed.
- A second player snapshot measured **7,201** at
  `2026-10-21T22:47:10.037952Z`, versus 5,260 about 9.15 hours earlier:
  **+1,941 (+36.90%)**. Two different-time-of-day points are insufficient for
  a trend or update-effect claim.
- The latest news range (`2026-10-21..2026-10-22` UTC) returned **0** in-range
  rows in one request and completed at its range floor.
- Regression verification: **36 tests passed in 0.77s**; Ruff lint and format
  checks passed; source/test compilation passed.
- No GCP resource, paid API, deployment, push or external publication occurred.

Next implementation action: accumulate same-time-of-day player snapshots and
several more Updated runs before selecting a production overlap. Independently
verify release-note effective times before rerunning event-impact analysis.

### Reusable risk evaluation foundation — implemented, labels unavailable

- Confirmed the product boundary between aggregate public-data game-health
  grading and true first-party user-churn prediction (D-023).
- Added a dependency-free binary-risk evaluator for ROC AUC, Accuracy Ratio
  (`AR = 2*AUC-1`), deterministic capture at the highest-risk 20%, Brier score,
  and equal-count `R1..R5` grade outcome tables.
- Added strict validation for score range, unique entity keys and both outcome
  classes. Synthetic tests cover perfect ranking, tied scores and invalid
  single-class outcomes.
- No actual Steam-user churn score, AR or CP@20 was produced. Public Steam data
  has no continuous per-user activity outcome, so those metrics currently have
  no matured label.
- Current regression result: **39 tests passed**; Ruff and compile checks pass.

### Original update/VOC scope resumed — update classification implemented

- Individual churn scoring remains an optional future extension (D-024); it is
  not a current KPI or claimed outcome.
- Used the locally retained public news excerpts to build a privacy-minimized
  multi-label event dimension. Article bodies remain only in Raw and are not
  copied to the derived output.
- Ten measured news rows were classified: 5 raid/dungeon content primaries,
  2 live events, 1 new-class update, 1 technical/operations update and 1 live
  service transition. Secondary labels capture progression, balance,
  monetization, new/returning UX, operations and community communication.
- Expected scope/technical/monetization values are deterministic monitoring
  priors, explicitly not observed business impact. `effective_at` remains null
  and every row is `human_verified=false`.
- Output: `output/dim_update_event_classification.jsonl` (10 rows).
- Current regression result: **41 tests passed in 0.80s**; Ruff lint/format and
  Python compilation passed.

### Update decision mart — implemented with measured limitations

- Joined 10 classified events to 8 measured 14-day review-impact rows without
  dropping the 2 unevaluated live events.
- Added separate expected monitoring prior, observed negative-review reaction,
  evidence confidence, decision priority, quality flags and recommended action
  fields (D-025). Missing player/issue signals remain null, never zero-filled.
- Output bands: 1 high, 3 medium, 3 low and 3 evidence-limited. Actions: 2
  triage while validating event time, 5 validate time/monitor, 1 collect more
  data/monitor and 2 collect aligned measurements.
- The September class update ranked highest (priority 88.43, confidence 60,
  measured review delta +18.32 pp). Because `effective_at` is unverified, its
  action is investigation plus time validation, not a causal harm claim.
- The October update had only 4 post reviews and an incomplete window, so its
  apparent +15.35 pp change is explicitly `evidence_limited` (confidence 25).
- Current regression result: **43 tests passed in 0.67s**; Ruff lint/format and
  Python compilation passed.

### Announced event-time evidence — implemented without false promotion

- Extracted one evidence row for each of the 10 news events. Three announced
  maintenance starts and one live-event start were parsed to UTC; one
  maintenance date and one future service-transition period remain unparsed;
  four rows had no time evidence in the retained excerpt.
- Parsed examples: Argos maintenance `2026-05-13T07:00:00Z`, Twilight
  maintenance `2026-06-10T07:00:00Z`, Ratlympics start
  `2026-06-12T19:00:00Z`, and Bitter Cold maintenance
  `2026-08-12T07:00:00Z`.
- All 10 `effective_at` values remain null. Announced maintenance/start is
  operational context, not observed deployment completion (D-026).
- The decision mart now carries the announced time and evidence type while
  leaving confidence unchanged and adding an explicit quality flag.
- First execution exposed missing Windows IANA `tzdata`; no package was added.
  The corrected dependency-free implementation codifies the US Pacific 2007+
  DST rule and stores the conversion basis. Regression result: **47 tests
  passed in 0.62s**; Ruff lint/format and Python compilation passed.

### Collection-health mart — implemented at a reproducible clock

- Added a configuration-driven health mart evaluated at
  `2026-10-22T00:00:00Z`, covering review backfill, Updated reviews, current
  players and app news.
- The 180-day review backfill is the only `ready` source. Updated, players and
  news are `limited` because their local run histories are below configured
  minimums; all three are explicitly `manual_local`, not scheduled production.
- At the fixed clock all three cadence sources were recently fresh, but
  Updated had one interval just over its 9-hour tolerance and players had a
  9.15-hour interval against a 1-hour target. Player run coverage over its
  observed span was **20%**.
- Updated Raw contained 300 observed rows versus 103 unique stored version
  keys, giving a measured replay/duplicate ratio of **65.67%**. This is expected
  from overlap/replay and is not labeled as data loss.
- Complete-range checkpoints and watermark checkpoints are reported separately.
- Outputs: `output/mart_collection_health.jsonl` (4 rows) and
  `output/collection_health_summary.json`.
- Current regression result: **49 tests passed in 0.65s**; Ruff lint/format and
  Python compilation passed.

### One-command local analytics rebuild — implemented and executed

- Added a configuration-driven eight-step DAG: validate inputs → review marts
  → update classification → time evidence → update impact → decision mart →
  player summary → collection health.
- Actual run `local-rebuild-20261022-02` completed all **8 steps** with **0
  network calls** at explicit data clock `2026-10-22T00:00:00Z`.
- The run manifest separates input and output artifacts and records byte size,
  SHA-256, metrics and duration for every completed step.
- A missing-input regression test verifies a `failed` manifest is written at
  `validate_inputs` and no later step is claimed.
- Output: `output/local_analytics_run_manifest.json`.
- Current regression result: **50 tests passed in 0.54s**; Ruff lint/format and
  Python compilation passed.

The former mixed checkbox list has moved to
`docs/STATUS_CHECKLIST.md`. That file is the single current status view using
the three states **완료 / 진행 중 / 대기**. This document remains the historical
evidence log. No cloud resource is claimed from offline code or tests.

### Locked local incremental cycle — implemented and live-verified

- Added one configuration-driven command for Updated reviews, current-player
  snapshots and app news. An atomic exclusive lock prevents overlapping local
  cycles; each source is attempted independently and receives its own truthful
  status in a durable manifest (D-029).
- A sandboxed run (`lostark-local-20261022-03`) remains recorded as
  `partial_failure`: all three sources exhausted four transport attempts. This
  is failure evidence, not an API success claim.
- The permitted network run (`lostark-local-20261022-04`) completed all three
  source steps in **0.92 seconds**. Updated fetched 1 page/100 observations and
  wrote 0 new versions after reaching the 24-hour overlap floor; current-player
  collection wrote 1 snapshot with **5,476** observed players; the 48-hour news
  overlap fetched 1 page and wrote 0 new news rows.
- No GCP resource was created. The local lock was released, per-run news
  checkpoint was written, and the success manifest is
  `output/incremental_runs/lostark-local-20261022-04.json`.
- Regression result before the live call: **55 tests passed in 0.78s**; Ruff
  lint/format and Python compilation passed.
- The post-collection offline rebuild `local-rebuild-20261022-03` then completed
  all 8 steps with 0 network calls. At `2026-10-22T11:31:00Z`, all three
  cadence sources were fresh but still `limited`; only the completed 180-day
  review backfill was analysis-ready. Final regression: **55 tests passed in
  0.76s** with lint, format and compilation passing.

### 2026-10-24 cloud-adapter continuation — offline verified only

- Reviewed the GCS/BigQuery adapter and conditional Cloud Run/Scheduler
  Terraform definitions without applying or changing any cloud resource.
- A successful zero-row source run now creates and uploads an explicit empty
  canonical JSONL state. This prevents a valid first news run with no in-range
  rows from failing with a missing local file.
- Immutable Bronze and manifest uploads still use a create-only generation
  precondition. A retry with the same execution ID is accepted only when the
  existing object is byte-identical; a same-name/different-body collision is
  rejected without overwrite (D-030).
- Offline tests cover a byte-identical full execution retry, zero-row state and
  differing immutable-object collision. Full regression: **61 tests passed in
  0.82s**; Ruff lint/format and Python compilation passed.
- The collector CLI help path executed without credentials or network access.
  A Docker image run was not verified because the local Docker daemon was not
  running.
- `terraform fmt -check` passed using the bundled Terraform 1.15.9 binary.
  `terraform validate` could not be completed because the local Google provider
  plugin did not respond. Remote state inspection also remained unavailable
  without Application Default Credentials. These are unverified checks, not
  deployment failures or proof that resources do or do not exist.
- No GCP apply, API call, deployment, scheduler change or paid-resource action
  was performed.

Reproduce the verified offline checks:

```text
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m compileall -q src tests
python -m entertainment_platform.jobs.collect_gcp_incremental --help
```

### 2026-10-24 GitHub portfolio publication

- Added `docs/STATUS_CHECKLIST.md` as the single current status source with
  three states: 완료, 진행 중 and 대기 (D-031). `docs/PROGRESS.md` remains the
  historical evidence log and `docs/ROADMAP.md` remains the sequence definition.
- Scanned the publishable tree for common private-key/token patterns; none were
  found. Confirmed that local `terraform.tfvars`, plans/state, Raw data,
  generated outputs and bundled tools are ignored.
- Committed 105 project files as `e96539c` (`feat: add Lost Ark analytics
  portfolio project`) and pushed it to `Ongum/02_study` `master`.
- Other untracked directories in the parent repository were not staged or
  committed.
- GitHub does not execute workflow files nested inside a project subdirectory.
  The project-local workflow is explicitly labeled as a monorepo-aware template;
  installing it in the repository-root `.github/workflows/` remains part of
  ST-10.
