"""Bounded check of Steam's Updated review stream.

The check verifies only the behavior observed during this run: updated-time
ordering, cursor advancement, and duplicates across a few pages. A later repeat
is still required to prove that newly edited reviews can be recovered from a
durable watermark.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from entertainment_platform.jobs.steam_reviews_smoke import run as run_smoke_page

UPDATED_FILTER = 2


@dataclass(frozen=True, slots=True)
class UpdatedCheckResult:
    """Aggregate evidence that is safe to print and commit to documentation."""

    appid: int
    max_pages: int
    page_size: int
    requests_made: int
    pages_fetched: int
    rows_observed: int
    unique_reviews_observed: int
    duplicate_reviews: int
    cursor_advanced_on_every_page: bool
    updated_order_nonincreasing: bool
    terminal_empty_page_observed: bool
    stopped_by_page_cap: bool
    earliest_updated_at: str | None
    latest_updated_at: str | None
    coverage_floor_observed: str | None
    next_high_watermark_candidate: str | None


def run(
    *,
    appid: int,
    max_pages: int,
    page_size: int,
    raw_directory: Path,
    normalized_path: Path,
    metadata_directory: Path,
    summary_path: Path,
    run_id_prefix: str,
) -> UpdatedCheckResult:
    """Read a small prefix of the Updated stream without claiming completeness."""

    _validate_limits(max_pages=max_pages, page_size=page_size)
    cursor = "*"
    seen_cursors: set[str] = set()
    seen_review_ids: set[str] = set()
    updated_timestamps: list[int] = []
    duplicate_reviews = 0
    cursor_advanced = True
    terminal_empty_page = False
    requests_made = 0
    pages_fetched = 0

    for page_number in range(1, max_pages + 1):
        page_run_id = f"{run_id_prefix}-page-{page_number:03d}"
        raw_path = raw_directory / f"{page_run_id}.json"

        # The helper performs exactly one HTTP request. This outer loop is the
        # sole owner of the page cap, which keeps the diagnostic predictable.
        page_metadata = run_smoke_page(
            raw_directory=raw_directory,
            normalized_path=normalized_path,
            metadata_path=metadata_directory / f"{page_run_id}.json",
            run_id=page_run_id,
            appid=appid,
            cursor=cursor,
            num_per_page=page_size,
            filter_value=UPDATED_FILTER,
        )
        pages_fetched += 1
        requests_made += int(page_metadata["request_count"])

        response = _read_response_envelope(raw_path)
        reviews = response["reviews"]
        next_cursor = response["cursor"]
        if not reviews:
            terminal_empty_page = True
            break

        page_updated = [review["timestamp_updated"] for review in reviews]
        updated_timestamps.extend(page_updated)
        for review in reviews:
            recommendationid = str(review["recommendationid"])
            # Review IDs stay in memory and are reduced to a duplicate count;
            # individual IDs never enter the summary or stdout.
            if recommendationid in seen_review_ids:
                duplicate_reviews += 1
            seen_review_ids.add(recommendationid)

        if next_cursor == cursor or next_cursor in seen_cursors:
            cursor_advanced = False
            break
        seen_cursors.add(cursor)
        cursor = next_cursor

    updated_order = all(
        earlier >= later for earlier, later in pairwise(updated_timestamps)
    )
    stopped_by_page_cap = pages_fetched == max_pages and not terminal_empty_page

    result = UpdatedCheckResult(
        appid=appid,
        max_pages=max_pages,
        page_size=page_size,
        requests_made=requests_made,
        pages_fetched=pages_fetched,
        rows_observed=len(updated_timestamps),
        unique_reviews_observed=len(seen_review_ids),
        duplicate_reviews=duplicate_reviews,
        cursor_advanced_on_every_page=cursor_advanced,
        updated_order_nonincreasing=updated_order,
        terminal_empty_page_observed=terminal_empty_page,
        stopped_by_page_cap=stopped_by_page_cap,
        earliest_updated_at=_epoch_iso(min(updated_timestamps))
        if updated_timestamps
        else None,
        latest_updated_at=_epoch_iso(max(updated_timestamps))
        if updated_timestamps
        else None,
        coverage_floor_observed=(
            _epoch_iso(min(updated_timestamps)) if updated_timestamps else None
        ),
        next_high_watermark_candidate=(
            _epoch_iso(max(updated_timestamps)) if updated_timestamps else None
        ),
    )
    _write_summary(summary_path, result)
    return result


def _validate_limits(*, max_pages: int, page_size: int) -> None:
    if not 1 <= max_pages <= 3:
        raise ValueError("max_pages must be in [1, 3]")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be in [1, 100]")


def _read_response_envelope(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
        raise TypeError("saved page is missing the Steam response envelope")
    response = payload["response"]
    reviews = response.get("reviews")
    if reviews is None:
        summary = response.get("query_summary")
        if not isinstance(summary, dict) or summary.get("num_reviews") != 0:
            raise TypeError("saved non-empty page is missing reviews")
        response["reviews"] = []
    elif not isinstance(reviews, list):
        raise TypeError("saved page reviews must be a list")
    if not isinstance(response.get("cursor"), str):
        raise TypeError("saved page cursor must be a string")
    return response


def _write_summary(path: Path, result: UpdatedCheckResult) -> None:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a bounded prefix of Steam's Updated review stream."
    )
    parser.add_argument("--run-id-prefix", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=5)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/updated"))
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=Path("output/updated_normalized_reviews.jsonl"),
    )
    parser.add_argument(
        "--metadata-directory", type=Path, default=Path("output/updated_pages")
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("output/updated_summary.json"),
    )
    args = parser.parse_args(argv)

    result = run(
        appid=args.appid,
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
