# Steam Updated checkpoint contract (local v1)

Status: implemented and unit-tested locally; not connected to a scheduled live
collector.

## Meaning

Steam's Updated stream is observed newest first. The checkpoint stores the
greatest `timestamp_updated` that belonged to a fully validated and durably
written batch. It does not store the oldest row seen in a bounded prefix.

On a later run, the scanner starts from the first page and continues through an
overlap floor:

```text
scan_floor = high_watermark_at - overlap_seconds
```

Rows inside the overlap are expected to repeat. The storage key
`(appid, recommendationid, updated_at)` removes those duplicates. The production
overlap duration is still open; 3,600 seconds is used only in synthetic tests
and the local demo.

## Stored fields

| Field | Meaning |
|---|---|
| `schema_version` | Checkpoint contract version, currently 1 |
| `appid` | Positive Steam app ID |
| `filter_value` | Must be 2 (`Updated`) |
| `high_watermark_at` | Greatest fully processed update timestamp in UTC |
| `overlap_seconds` | Configured replay window, 0–86,400 seconds |
| `committed_at` | UTC time of the atomic checkpoint commit |
| `run_id` | Run that most recently committed the checkpoint |

## Commit boundary

1. Validate every response page and normalized row.
2. Durably append normalized rows and call `fsync`.
3. Atomically replace the checkpoint file.

An invalid later page performs no writes. If a row write partially succeeds and
then fails, no checkpoint is created. A retry deduplicates the durable partial
rows, writes the remainder, and only then commits the checkpoint. An empty batch
does not create or advance a checkpoint.

The local file proves transaction ordering and recovery behavior only. Cloud
object/database transaction boundaries and concurrent-job exclusion remain to
be designed and tested.
