# Steam review fixed-range backfill contract

Status: **implemented and measured locally** for Lost Ark app `1599340`.

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

## Privacy and known limits

Raw API responses can contain public Steam author identifiers and therefore
remain ignored and access-restricted locally. Normalized rows omit author IDs.
A historical date-range query returns the latest review state visible at fetch
time; it cannot reconstruct earlier text/recommendation versions. The history
grain supports future versions captured by the Updated stream.

Official contract: <https://partner.steamgames.com/doc/webapi/IUserReviewsService>
