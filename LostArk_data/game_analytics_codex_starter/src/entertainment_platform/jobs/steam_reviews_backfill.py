"""Resumable, bounded local backfill for a fixed Steam review date range.

Unlike the small diagnostic job, this job may complete a reviewed historical
range. Every successful response is stored under a content-addressed filename,
then validated and appended to the version-history JSONL before the cursor
checkpoint is advanced. A rerun resumes from that cursor and JSONL dedupe makes
replayed pages safe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.checkpoint import (
    read_json_checkpoint,
    write_json_checkpoint,
)
from entertainment_platform.common.http import RetryPolicy, fetch_bytes_with_retry
from entertainment_platform.common.jsonl import append_unique_jsonl, read_jsonl_keys
from entertainment_platform.connectors.steam.api import build_review_request_url
from entertainment_platform.connectors.steam.reviews import parse_review_page
from entertainment_platform.jobs.offline_steam_reviews import DEDUPLICATION_KEY

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
CHECKPOINT_SCHEMA_VERSION = 1
RECENT_FILTER = 1


@dataclass(frozen=True, slots=True)
class FetchedBackfillPage:
    body: bytes
    status: int = 200
    attempts: int = 1
    retry_delays_seconds: tuple[float, ...] = ()
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    schema_version: int
    appid: int
    range_start_epoch: int
    range_end_epoch: int
    next_cursor: str
    next_page_number: int
    pages_committed: int
    rows_observed: int
    total_matching_reported: int | None
    completed: bool
    committed_at: str
    run_id: str


@dataclass(frozen=True, slots=True)
class BackfillResult:
    appid: int
    range_start: str
    range_end: str
    pages_fetched_this_run: int
    http_attempts_this_run: int
    pages_committed_total: int
    rows_observed_total: int
    rows_written_this_run: int
    unique_versions_stored: int
    unique_reviews_stored: int
    total_matching_reported: int | None
    duplicate_versions_in_storage: int
    out_of_range_rows: int
    invalid_timestamp_rows: int
    completed: bool
    completion_reason: str
    earliest_created_at: str | None
    latest_created_at: str | None
    earliest_updated_at: str | None
    latest_updated_at: str | None


PageFetcher = Callable[[str, int], FetchedBackfillPage]
Sleeper = Callable[[float], None]


def run_backfill(
    *,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    raw_directory: Path,
    history_path: Path,
    checkpoint_path: Path,
    run_id: str,
    max_pages_per_run: int = 10,
    page_size: int = 100,
    request_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    retry_policy: RetryPolicy | None = None,
    page_fetcher: PageFetcher | None = None,
    sleep: Sleeper = time.sleep,
    fetched_at: datetime | None = None,
) -> BackfillResult:
    """Collect or resume a fixed range, stopping only at a bounded page limit."""

    start_epoch, end_epoch = _validate_arguments(
        appid=appid,
        range_start=range_start,
        range_end=range_end,
        run_id=run_id,
        max_pages_per_run=max_pages_per_run,
        page_size=page_size,
        request_interval_seconds=request_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    observed_at = fetched_at or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")

    checkpoint = _load_checkpoint(checkpoint_path)
    if checkpoint is None:
        checkpoint = BackfillCheckpoint(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            appid=appid,
            range_start_epoch=start_epoch,
            range_end_epoch=end_epoch,
            next_cursor="*",
            next_page_number=1,
            pages_committed=0,
            rows_observed=0,
            total_matching_reported=None,
            completed=False,
            committed_at=_utc_iso(observed_at),
            run_id=run_id,
        )
    _validate_checkpoint(
        checkpoint,
        appid=appid,
        range_start_epoch=start_epoch,
        range_end_epoch=end_epoch,
    )
    if checkpoint.completed:
        return _result_from_storage(
            checkpoint=checkpoint,
            history_path=history_path,
            range_start=range_start,
            range_end=range_end,
            pages_fetched=0,
            http_attempts=0,
            rows_written=0,
            completion_reason="already_complete",
        )

    fetch_page = page_fetcher or _network_fetcher(
        appid=appid,
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
    )
    cursor = checkpoint.next_cursor
    page_number = checkpoint.next_page_number
    pages_fetched = 0
    http_attempts = 0
    rows_written = 0
    history_keys = read_jsonl_keys(history_path, DEDUPLICATION_KEY)
    seen_cursor_hashes: set[str] = set()
    completion_reason = "page_cap"

    for request_index in range(max_pages_per_run):
        fetched = fetch_page(cursor, page_number)
        if fetched.status != 200:
            raise ValueError("page fetcher must return only successful HTTP 200 pages")
        if fetched.attempts < 1:
            raise ValueError("page fetcher attempts must be positive")
        pages_fetched += 1
        http_attempts += fetched.attempts

        body_sha256 = hashlib.sha256(fetched.body).hexdigest()
        page_stem = f"page-{page_number:04d}-{body_sha256[:12]}"
        raw_path = raw_directory / run_id / f"{page_stem}.json"
        _write_once(raw_path, fetched.body)
        _write_once(
            raw_path.with_suffix(".metadata.json"),
            _metadata_bytes(
                fetched=fetched,
                appid=appid,
                run_id=run_id,
                page_number=page_number,
                cursor=cursor,
                fetched_at=observed_at,
                body_sha256=body_sha256,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
            ),
        )

        payload = json.loads(fetched.body)
        parsed = parse_review_page(
            payload,
            appid=appid,
            fetched_at=observed_at,
            run_id=f"{run_id}-page-{page_number:04d}",
        )
        if parsed.total_matching < len(parsed.reviews):
            raise ValueError("total_matching is smaller than the current page")
        if (
            checkpoint.total_matching_reported is not None
            and parsed.total_matching != checkpoint.total_matching_reported
        ):
            raise RuntimeError("total_matching changed while resuming fixed backfill")

        # The append call fsyncs before returning. Only after that durable
        # boundary may the next cursor be committed.
        rows_written += append_unique_jsonl(
            history_path,
            (review.to_dict() for review in parsed.reviews),
            key_fields=DEDUPLICATION_KEY,
            known_keys=history_keys,
        )

        completed = not parsed.reviews
        if not completed:
            cursor_hash = hashlib.sha256(parsed.cursor.encode()).hexdigest()
            if parsed.cursor == cursor or cursor_hash in seen_cursor_hashes:
                raise RuntimeError("Steam cursor repeated before terminal page")
            seen_cursor_hashes.add(cursor_hash)

        checkpoint = BackfillCheckpoint(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            appid=appid,
            range_start_epoch=start_epoch,
            range_end_epoch=end_epoch,
            next_cursor=parsed.cursor,
            next_page_number=page_number + 1,
            pages_committed=checkpoint.pages_committed + 1,
            rows_observed=checkpoint.rows_observed + len(parsed.reviews),
            total_matching_reported=(
                checkpoint.total_matching_reported
                if checkpoint.total_matching_reported is not None
                else parsed.total_matching
            ),
            completed=completed,
            committed_at=_utc_iso(datetime.now(UTC)),
            run_id=run_id,
        )
        write_json_checkpoint(checkpoint_path, asdict(checkpoint))
        cursor = parsed.cursor
        page_number += 1

        if completed:
            completion_reason = "terminal"
            break
        if request_index + 1 < max_pages_per_run and request_interval_seconds > 0:
            sleep(request_interval_seconds)

    return _result_from_storage(
        checkpoint=checkpoint,
        history_path=history_path,
        range_start=range_start,
        range_end=range_end,
        pages_fetched=pages_fetched,
        http_attempts=http_attempts,
        rows_written=rows_written,
        completion_reason=completion_reason,
    )


def _network_fetcher(
    *,
    appid: int,
    start_epoch: int,
    end_epoch: int,
    page_size: int,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
) -> PageFetcher:
    def fetch(cursor: str, _: int) -> FetchedBackfillPage:
        url, _parameters = build_review_request_url(
            appid=appid,
            cursor=cursor,
            num_per_page=page_size,
            filter_value=RECENT_FILTER,
            date_range_start=start_epoch,
            date_range_end=end_epoch,
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "entertainment-data-platform-backfill/0.0.1",
            },
            method="GET",
        )
        response = fetch_bytes_with_retry(
            request,
            timeout_seconds=timeout_seconds,
            policy=retry_policy,
        )
        return FetchedBackfillPage(
            body=response.body,
            status=response.status,
            attempts=response.attempts,
            retry_delays_seconds=response.retry_delays_seconds,
            headers=response.headers,
        )

    return fetch


def _result_from_storage(
    *,
    checkpoint: BackfillCheckpoint,
    history_path: Path,
    range_start: datetime,
    range_end: datetime,
    pages_fetched: int,
    http_attempts: int,
    rows_written: int,
    completion_reason: str,
) -> BackfillResult:
    keys: set[tuple[object, object, object]] = set()
    review_ids: set[str] = set()
    created_values: list[datetime] = []
    updated_values: list[datetime] = []
    duplicate_versions = 0
    out_of_range = 0
    invalid_timestamps = 0
    start_utc = range_start.astimezone(UTC)
    end_utc = range_end.astimezone(UTC)

    for row in _iter_history(history_path):
        key = (row.get("appid"), row.get("recommendationid"), row.get("updated_at"))
        if key in keys:
            duplicate_versions += 1
        keys.add(key)
        review_ids.add(str(row.get("recommendationid")))
        try:
            created = _parse_utc(str(row["created_at"]))
            updated = _parse_utc(str(row["updated_at"]))
        except (KeyError, TypeError, ValueError):
            invalid_timestamps += 1
            continue
        created_values.append(created)
        updated_values.append(updated)
        if not start_utc <= created <= end_utc:
            out_of_range += 1
        if updated < created:
            invalid_timestamps += 1

    return BackfillResult(
        appid=checkpoint.appid,
        range_start=_utc_iso(range_start),
        range_end=_utc_iso(range_end),
        pages_fetched_this_run=pages_fetched,
        http_attempts_this_run=http_attempts,
        pages_committed_total=checkpoint.pages_committed,
        rows_observed_total=checkpoint.rows_observed,
        rows_written_this_run=rows_written,
        unique_versions_stored=len(keys),
        unique_reviews_stored=len(review_ids),
        total_matching_reported=checkpoint.total_matching_reported,
        duplicate_versions_in_storage=duplicate_versions,
        out_of_range_rows=out_of_range,
        invalid_timestamp_rows=invalid_timestamps,
        completed=checkpoint.completed,
        completion_reason=completion_reason,
        earliest_created_at=_optional_iso(
            min(created_values) if created_values else None
        ),
        latest_created_at=_optional_iso(
            max(created_values) if created_values else None
        ),
        earliest_updated_at=_optional_iso(
            min(updated_values) if updated_values else None
        ),
        latest_updated_at=_optional_iso(
            max(updated_values) if updated_values else None
        ),
    )


def _load_checkpoint(path: Path) -> BackfillCheckpoint | None:
    payload = read_json_checkpoint(path)
    if payload is None:
        return None
    try:
        return BackfillCheckpoint(**payload)
    except TypeError as exc:
        raise ValueError("backfill checkpoint fields are invalid") from exc


def _validate_checkpoint(
    checkpoint: BackfillCheckpoint,
    *,
    appid: int,
    range_start_epoch: int,
    range_end_epoch: int,
) -> None:
    if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported backfill checkpoint schema version")
    if checkpoint.appid != appid:
        raise ValueError("checkpoint appid does not match")
    if (
        checkpoint.range_start_epoch != range_start_epoch
        or checkpoint.range_end_epoch != range_end_epoch
    ):
        raise ValueError("checkpoint date range does not match")
    if not checkpoint.next_cursor:
        raise ValueError("checkpoint cursor must not be empty")
    if checkpoint.next_page_number < 1 or checkpoint.pages_committed < 0:
        raise ValueError("checkpoint page counters are invalid")
    if checkpoint.rows_observed < 0:
        raise ValueError("checkpoint row count is invalid")
    _parse_utc(checkpoint.committed_at)


def _validate_arguments(
    *,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    run_id: str,
    max_pages_per_run: int,
    page_size: int,
    request_interval_seconds: float,
    timeout_seconds: float,
) -> tuple[int, int]:
    if appid <= 0:
        raise ValueError("appid must be positive")
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only safe filename characters")
    if range_start.tzinfo is None or range_start.utcoffset() is None:
        raise ValueError("range_start must be timezone-aware")
    if range_end.tzinfo is None or range_end.utcoffset() is None:
        raise ValueError("range_end must be timezone-aware")
    if range_start > range_end:
        raise ValueError("range_start must not exceed range_end")
    if not 1 <= max_pages_per_run <= 100:
        raise ValueError("max_pages_per_run must be in [1, 100]")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be in [1, 100]")
    if not 0 <= request_interval_seconds <= 10:
        raise ValueError("request_interval_seconds must be in [0, 10]")
    if not 0 < timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be in (0, 60]")
    return int(range_start.timestamp()), int(range_end.timestamp())


def _metadata_bytes(
    *,
    fetched: FetchedBackfillPage,
    appid: int,
    run_id: str,
    page_number: int,
    cursor: str,
    fetched_at: datetime,
    body_sha256: str,
    start_epoch: int,
    end_epoch: int,
) -> bytes:
    metadata = {
        "appid": appid,
        "run_id": run_id,
        "page_number": page_number,
        "filter": RECENT_FILTER,
        "range_start_epoch": start_epoch,
        "range_end_epoch": end_epoch,
        "cursor_kind": "initial" if cursor == "*" else "returned",
        "cursor_sha256": (
            None if cursor == "*" else hashlib.sha256(cursor.encode()).hexdigest()
        ),
        "fetched_at": _utc_iso(fetched_at),
        "http_status": fetched.status,
        "http_attempts": fetched.attempts,
        "retry_delays_seconds": list(fetched.retry_delays_seconds),
        "response_bytes": len(fetched.body),
        "response_sha256": body_sha256,
    }
    return (
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_once(path: Path, data: bytes) -> None:
    """Create immutable local raw evidence, accepting only byte-identical replay."""

    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite different raw file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _iter_history(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"history row {line_number} is not an object")
            yield value


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_iso(value: datetime | None) -> str | None:
    return _utc_iso(value) if value is not None else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect or resume one bounded Steam review date-range backfill."
    )
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-interval-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/backfill"))
    parser.add_argument(
        "--history-output",
        type=Path,
        default=Path("output/silver_review_history.jsonl"),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("output/backfill_checkpoint.json"),
    )
    args = parser.parse_args(argv)

    result = run_backfill(
        appid=args.appid,
        range_start=args.start,
        range_end=args.end,
        raw_directory=args.raw_directory,
        history_path=args.history_output,
        checkpoint_path=args.checkpoint,
        run_id=args.run_id,
        max_pages_per_run=args.max_pages,
        page_size=args.page_size,
        request_interval_seconds=args.request_interval_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
