# Steam API smoke-test plan

Status: **fixed-range Recent, news and current-player gates passed; time-separated Updated recovery pending**  
Last documentation review: 2026-10-21

This checklist validates the public Steam interfaces with the smallest useful
request before any scheduled collector or 90-day backfill is enabled. A passing
offline fixture test is not evidence that a live response is compatible.

## Official basis already checked

- `IUserReviewsService/GetAppReviews/v1/` is the current documented review
  interface and replaces the deprecated Store `appreviews` endpoint.
- It is a Service interface: request fields belong in URL-encoded `input_json`.
- `appid` is required; an API key is optional for this public method. Anonymous
  responses may be cached for up to 10 minutes and share a lower rate limit;
  HTTP 429 indicates rate limiting.
- Pagination starts with omitted cursor or `"*"`; subsequent calls use the
  returned cursor. Cursors must be URL-encoded. `Recent` (1) or `Updated` (2),
  rather than `Helpful` (0), must be used when paging to an empty result.
- Documented filter enums are Helpful=0, Recent=1, Updated=2, Funny=3;
  review type All=0, Positive=1, Negative=2; purchase type Steam=0, All=1,
  Non-Steam=2. `num_per_page` has a documented maximum of 100.
- The documented response has no `success` field; HTTP status is authoritative.
  It contains `query_summary`, `reviews`, `cursor`, `day_range_used`, and
  `total_matching`. Score totals are conditional first-page fields.
- Public news is documented at `ISteamNews/GetNewsForApp/v2/`. The documented
  current-player method reports connected concurrent players, not DAU.
- The Steam Web API Terms currently state a general 100,000-calls/day limit.
  This project will stay far below it and will not probe rate limits.

Official references:

- <https://partner.steamgames.com/doc/webapi/IUserReviewsService?l=english>
- <https://partner.steamgames.com/doc/webapi_overview?l=english>
- <https://partner.steamgames.com/doc/webapi/ISteamUserStats?l=english>
- <https://partner.steamgames.com/doc/webapi/ISteamNews?l=english>
- <https://steamcommunity.com/dev/apiterms>

## One-call review validation

Executed once for app 1599340 on 2026-10-21 with no API key and
`num_per_page=1`. Observed evidence: HTTP 200, one request, 761 response bytes,
JSON content type, and a top-level `response` envelope. That envelope contained
`query_summary`, `reviews`, `cursor`, and `total_matching`; the single review
contained all fields required by the implemented contract. The initial direct
top-level parser assumption failed, was recorded, then corrected and revalidated
against the same saved response without another API request. One normalized row
was written and contained no author or Steam ID field. Raw content and review
values were not printed or committed.

Use HTTPS against the public host, without a key, with a descriptive user agent
and a client timeout. URL-encode one `input_json` object equivalent to:

```json
{
  "appid": 1599340,
  "filter": 1,
  "languages": ["all"],
  "review_type": 0,
  "purchase_type": 1,
  "num_per_page": 1,
  "cursor": "*"
}
```

Do not paste the full response into logs or commit it. Write it under ignored
`raw_data/` with restrictive local permissions and record separately:
endpoint/interface version, sanitized parameters, UTC request/fetch times, HTTP
status, response-byte count, response SHA-256, run ID, cache/retry headers when
present, parser version, and validation outcome. Never log an API key, username,
review text, or full author Steam ID.

Validate:

1. HTTP 200 and JSON object; handle 429 using `Retry-After` when present and a
   bounded backoff. Do not retry other 4xx responses.
2. Exact top-level shape and the absence/presence rules described above.
3. Required review types, unsigned timestamps, numeric
   `weighted_vote_score`, and newly documented `refunded` field.
4. Parser acceptance of the sanitized capture and rejection of a deliberately
   corrupted local copy. Do not adapt the contract by silently coercing values.
5. No full author ID reaches normalized output or logs. Raw capture access is
   restricted and its retention/deletion date is recorded.

## Cursor and 90-day feasibility validation

The first adjacent-page check was executed on 2026-10-21. The second request
used the exact returned cursor read from the local raw response, without placing
the cursor in logs or command-line arguments. It returned HTTP 200 and one
review; the returned cursor differed from page 1, review-ID overlap was zero,
and Recent timestamps were non-increasing across the two observations. The two
responses produced two distinct normalized rows. This is evidence for one
cursor transition only, not terminal-page or completeness behavior.

After the one-call gate passes, make at most two adjacent pages with Recent=1.
Confirm that page 2 uses the exact returned cursor, the cursor advances, review
IDs do not unexpectedly duplicate within the two pages, and an empty review
list is the documented terminal condition. Then run a bounded request using
both `date_range_start` and `date_range_end` for a small UTC interval. Only
after these checks should the 90-day policy be assessed for page count,
coverage, cached responses, late edits, and runtime.

The fixed UTC interval `2026-10-20T00:00:00Z` through
`2026-10-20T23:59:59Z` was validated on 2026-10-21. The successful integrated
run made two requests: the first returned four unique reviews and
`total_matching=4`; the second was the terminal page. All four creation
timestamps were inside the inclusive range, duplicate count was zero, the
cursor advanced, and the page cap was not reached. Observed creation timestamps
ranged from `17:42:43Z` to `21:48:24Z`.

The first attempt exposed another real contract difference: on the terminal
page Steam reported `query_summary.num_reviews=0` and omitted `reviews` instead
of returning an empty array. The parser now treats that omission as empty only
when the summary count is zero. The corrected integrated run was repeated, so
the date-range investigation made four API calls in total rather than hiding
the failed attempt.

## 90-day bounded volume probe

On 2026-10-21, exactly one request was made for the inclusive UTC interval
`2026-07-23T00:00:00Z` through `2026-10-20T23:59:59Z`, using Recent sorting,
all languages, all purchase types, and a 100-row page. This was a count/volume
probe, not a backfill.

- `total_matching`: **450 reviews** at the observation time.
- First-page rows: **100**, all inside the requested interval.
- Estimated data pages at 100 rows/page: **5**. A cursor-until-empty strategy
  may make one additional terminal request.
- First response size: **75,013 bytes**. Multiplying this one page by five gives
  a rough uncompressed response estimate of **375,065 bytes (0.358 MiB)**.
- The job stopped because of its one-page cap; it did not observe the terminal
  page or prove complete 90-day coverage.
- The first page spanned `2026-09-29T19:01:20Z` through
  `2026-10-20T21:48:24Z`, consistent with Recent ordering but showing that older
  matching reviews remain on later pages.

The size estimate is illustrative linear extrapolation from one measured page.
Review text lengths and later-page payload sizes can differ, so it is not a
measured full-backfill byte count.

## Updated-stream bounded probe

On 2026-10-21, two pages of five rows each were requested with filter value 2
(`Updated`). The ten unique rows were ordered by non-increasing
`timestamp_updated`, the cursor advanced on both pages, and duplicate count was
zero. Four rows had `timestamp_updated > timestamp_created`; six had equal
timestamps. This demonstrates that the bounded stream contained both edited and
new/unmodified reviews, so a collector must not equate every Updated row with a
text edit.

The observed update timestamps ranged from `2026-10-20T14:26:47Z` to
`2026-10-21T12:09:54Z`; combined response size was 13,627 bytes. The job stopped
at its two-page cap and did not observe a terminal page. The oldest timestamp is
the observed coverage floor of this bounded prefix; the newest timestamp is the
next high-watermark candidate. Neither was persisted as a production checkpoint
because completeness, cache delay, late visibility, and safe overlap have not
been validated by a later repeated live run.

Separately validate Updated=2 for incremental change capture. A creation-time
backfill cannot prove complete edit history. Record earliest/latest created and
updated timestamps, page counts, duplicate counts, invalid rows, retry counts,
and any incomplete interval. Advance a checkpoint only after raw storage,
normalized validation, and durable output all succeed.

The local checkpoint transaction boundary is now implemented and tested with
synthetic pages. It stores the greatest fully processed update time, replays a
configurable overlap, and commits only after durable deduplicated row output.
This does not yet prove the live repeated-run stop condition.

Bounded HTTP retries are also implemented locally. Deterministic tests cover
429 with delta-seconds and HTTP-date `Retry-After`, 5xx exponential backoff,
transport exhaustion, and immediate failure for non-transient 4xx. An
over-limit `Retry-After` stops the job instead of retrying before Steam's
requested time. No live error was deliberately induced.

## Player and news validation

- Make one current-player request for app 1599340; validate non-negative integer
  count and UTC observation time. Store it only as an observed snapshot.
- Make one public news request with `count=1`; validate the latest supported v2
  shape, publication timestamp, feed, and canonical URL. Do not treat its
  publication time as a patch's effective deployment time.

## Stop conditions and pass evidence

Stop on an unexpected schema, terms ambiguity, repeated 403/429, cursor cycle,
or any risk of exposing identifiers. Passing evidence is a sanitized metadata
record, schema-test output, request count, and documented gaps—not the raw
review response. Recheck official documentation and terms before production.

Verified locally: two complete fixed-range Recent traversals through empty
terminal pages, one bounded Updated prefix, one 180-day news range, and one
anonymous current-player request. Still unverified: all-history Updated
completeness, time-separated watermark recovery, anonymous throughput and real
429 behavior, future schema variability, production overlap tuning and repeated
player/news cadence behavior.
