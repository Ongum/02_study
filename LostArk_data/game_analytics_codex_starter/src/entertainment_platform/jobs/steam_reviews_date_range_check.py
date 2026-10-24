"""Bounded validation of Steam review date-range and cursor behavior.

This is a diagnostic job, not a production backfill. It deliberately caps the
number of requests so an unexpectedly large interval cannot create an unbounded
API loop. Raw responses remain in ignored local storage; logs contain only
aggregate counts and timestamps.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from entertainment_platform.jobs.steam_reviews_smoke import run as run_smoke_page


@dataclass(frozen=True, slots=True)
class DateRangeCheckResult:
    """Safe-to-log aggregates from a bounded date-range check."""

    appid: int
    range_start: str
    range_end: str
    max_pages: int
    page_size: int
    requests_made: int
    pages_fetched: int
    rows_observed: int
    unique_reviews_observed: int
    duplicate_reviews: int
    out_of_range_reviews: int
    cursor_advanced_on_every_page: bool
    terminal_empty_page_observed: bool
    stopped_by_page_cap: bool
    total_matching_reported: int | None
    estimated_pages_at_page_size: int | None
    earliest_created_at: str | None
    latest_created_at: str | None


def run(
    *,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    max_pages: int,
    page_size: int,
    raw_directory: Path,
    normalized_path: Path,
    metadata_directory: Path,
    summary_path: Path,
    run_id_prefix: str,
) -> DateRangeCheckResult:
    """Fetch a bounded number of pages and validate inclusive UTC boundaries."""

    start_epoch, end_epoch = _validate_arguments(
        range_start=range_start,
        range_end=range_end,
        max_pages=max_pages,
        page_size=page_size,
    )

    cursor = "*"
    seen_cursors: set[str] = set()
    seen_review_ids: set[str] = set()
    created_timestamps: list[int] = []
    duplicate_reviews = 0
    out_of_range_reviews = 0
    cursor_advanced = True
    terminal_empty_page = False
    total_matching: int | None = None
    requests_made = 0
    pages_fetched = 0

    for page_number in range(1, max_pages + 1):
        page_run_id = f"{run_id_prefix}-page-{page_number:03d}"
        raw_path = raw_directory / f"{page_run_id}.json"

        # The page helper makes exactly one request. The outer loop owns the
        # cap, so there is no hidden retry or unbounded pagination here.
        page_metadata = run_smoke_page(
            raw_directory=raw_directory,
            normalized_path=normalized_path,
            metadata_path=metadata_directory / f"{page_run_id}.json",
            run_id=page_run_id,
            appid=appid,
            cursor=cursor,
            num_per_page=page_size,
            date_range_start=start_epoch,
            date_range_end=end_epoch,
        )
        pages_fetched += 1
        requests_made += int(page_metadata["request_count"])
        if total_matching is None:
            reported = page_metadata.get("total_matching")
            total_matching = reported if isinstance(reported, int) else None

        response = _read_response_envelope(raw_path)
        reviews = response["reviews"]
        next_cursor = response["cursor"]

        if not reviews:
            terminal_empty_page = True
            break

        for review in reviews:
            recommendationid = str(review["recommendationid"])
            created_at = review["timestamp_created"]

            # IDs are used only in memory for duplicate counting. They are
            # never written into the aggregate summary or printed.
            if recommendationid in seen_review_ids:
                duplicate_reviews += 1
            seen_review_ids.add(recommendationid)

            if not start_epoch <= created_at <= end_epoch:
                out_of_range_reviews += 1
            created_timestamps.append(created_at)

        if next_cursor == cursor or next_cursor in seen_cursors:
            cursor_advanced = False
            break
        seen_cursors.add(cursor)
        cursor = next_cursor

    stopped_by_page_cap = pages_fetched == max_pages and not terminal_empty_page
    estimated_pages = (
        math.ceil(total_matching / page_size) if total_matching is not None else None
    )
    result = DateRangeCheckResult(
        appid=appid,
        range_start=_utc_iso(range_start),
        range_end=_utc_iso(range_end),
        max_pages=max_pages,
        page_size=page_size,
        requests_made=requests_made,
        pages_fetched=pages_fetched,
        rows_observed=len(created_timestamps),
        unique_reviews_observed=len(seen_review_ids),
        duplicate_reviews=duplicate_reviews,
        out_of_range_reviews=out_of_range_reviews,
        cursor_advanced_on_every_page=cursor_advanced,
        terminal_empty_page_observed=terminal_empty_page,
        stopped_by_page_cap=stopped_by_page_cap,
        total_matching_reported=total_matching,
        estimated_pages_at_page_size=estimated_pages,
        earliest_created_at=(
            _epoch_iso(min(created_timestamps)) if created_timestamps else None
        ),
        latest_created_at=(
            _epoch_iso(max(created_timestamps)) if created_timestamps else None
        ),
    )
    _write_summary(summary_path, result)
    return result


def _validate_arguments(
    *, range_start: datetime, range_end: datetime, max_pages: int, page_size: int
) -> tuple[int, int]:
    if range_start.tzinfo is None or range_start.utcoffset() is None:
        raise ValueError("range_start must be timezone-aware")
    if range_end.tzinfo is None or range_end.utcoffset() is None:
        raise ValueError("range_end must be timezone-aware")
    if range_start > range_end:
        raise ValueError("range_start must not exceed range_end")
    if not 1 <= max_pages <= 5:
        raise ValueError("max_pages must be in [1, 5]")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be in [1, 100]")
    return int(range_start.timestamp()), int(range_end.timestamp())


def _read_response_envelope(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise TypeError("saved page is missing the Steam response envelope")
    response = payload["response"]
    reviews = response.get("reviews")
    if reviews is None:
        query_summary = response.get("query_summary")
        if not isinstance(query_summary, dict) or query_summary.get("num_reviews") != 0:
            raise TypeError("saved non-empty page is missing reviews")
        # Normalize Steam's observed terminal-page omission for the aggregate
        # checks below. This changes only the in-memory object, never raw data.
        response["reviews"] = []
    elif not isinstance(reviews, list):
        raise TypeError("saved page reviews must be a list")
    if not isinstance(response.get("cursor"), str):
        raise TypeError("saved page cursor must be a string")
    return response


def _write_summary(path: Path, result: DateRangeCheckResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(asdict(result), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _epoch_iso(value: int) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a bounded Steam review UTC date range."
    )
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--run-id-prefix", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/range"))
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=Path("output/range_normalized_reviews.jsonl"),
    )
    parser.add_argument(
        "--metadata-directory", type=Path, default=Path("output/range_pages")
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("output/date_range_summary.json"),
    )
    args = parser.parse_args(argv)

    result = run(
        appid=args.appid,
        range_start=args.start,
        range_end=args.end,
        max_pages=args.max_pages,
        page_size=args.page_size,
        raw_directory=args.raw_directory,
        normalized_path=args.normalized_output,
        metadata_directory=args.metadata_directory,
        summary_path=args.summary_output,
        run_id_prefix=args.run_id_prefix,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
