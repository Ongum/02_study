"""Bounded local Updated-stream ingestion with completeness-aware commits.

This job deliberately requires a bootstrap checkpoint. It starts at Steam's
newest page and commits normalized rows/checkpoint only after reaching either
the checkpoint overlap floor or a validated empty terminal page. Raw successful
responses are retained even if a later page fails, supporting diagnosis while
preventing an incomplete scan from being mistaken for completed coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.http import (
    HttpResponse,
    RetryPolicy,
    fetch_bytes_with_retry,
)
from entertainment_platform.connectors.steam.api import build_review_request_url
from entertainment_platform.connectors.steam.checkpoint import (
    UPDATED_STREAM_FILTER,
    load_updated_checkpoint,
)
from entertainment_platform.connectors.steam.reviews import parse_review_page
from entertainment_platform.jobs.process_updated_batch import process_updated_payloads

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class FetchedReviewPage:
    """One logical API page and its HTTP-attempt evidence."""

    body: bytes
    status: int = 200
    attempts: int = 1
    retry_delays_seconds: tuple[float, ...] = ()
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class IncrementalRunResult:
    pages_fetched: int
    http_attempts: int
    rows_observed: int
    complete: bool
    completion_reason: str
    rows_written: int
    checkpoint_advanced: bool
    high_watermark_at: str


PageFetcher = Callable[[str, int], FetchedReviewPage]


def run_incremental(
    *,
    raw_directory: Path,
    normalized_path: Path,
    checkpoint_path: Path,
    run_id: str,
    appid: int = 1599340,
    max_pages: int = 20,
    page_size: int = 100,
    timeout_seconds: float = 20.0,
    retry_policy: RetryPolicy | None = None,
    page_fetcher: PageFetcher | None = None,
    fetched_at: datetime | None = None,
) -> IncrementalRunResult:
    """Collect a complete incremental interval or leave its checkpoint intact.

    ``max_pages`` is a safety bound, not a success condition. Reaching it before
    a terminal page or the replay floor returns ``complete=False`` and performs
    no normalized/checkpoint commit.
    """

    _validate_arguments(
        run_id=run_id,
        appid=appid,
        max_pages=max_pages,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
    checkpoint = load_updated_checkpoint(checkpoint_path)
    if checkpoint is None:
        raise ValueError(
            "bootstrap checkpoint is required before incremental collection"
        )
    if checkpoint.appid != appid:
        raise ValueError("checkpoint appid does not match this job")

    observed_at = fetched_at or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("fetched_at must be timezone-aware")

    fetch_page = page_fetcher or _network_fetcher(
        appid=appid,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
    )
    payloads: list[object] = []
    seen_cursors = {"*"}
    cursor = "*"
    pages_fetched = 0
    http_attempts = 0
    rows_observed = 0
    completion_reason: str | None = None

    for page_number in range(1, max_pages + 1):
        fetched = fetch_page(cursor, page_number)
        if fetched.status != 200:
            raise ValueError("page fetcher must return only successful HTTP 200 pages")
        if fetched.attempts < 1:
            raise ValueError("page fetcher attempts must be positive")
        pages_fetched += 1
        http_attempts += fetched.attempts

        # Bronze-like local evidence is durable before parsing. It remains on
        # an incomplete run and is never treated as a completed checkpoint.
        raw_path = raw_directory / run_id / f"page-{page_number:04d}.json"
        _write_bytes(raw_path, fetched.body)
        _write_page_metadata(
            raw_path.with_suffix(".metadata.json"),
            fetched=fetched,
            run_id=run_id,
            appid=appid,
            page_number=page_number,
            cursor=cursor,
            fetched_at=observed_at,
        )

        payload = json.loads(fetched.body)
        page = parse_review_page(
            payload,
            appid=appid,
            fetched_at=observed_at,
            run_id=f"{run_id}-page-{page_number:03d}",
        )
        payloads.append(payload)
        rows_observed += len(page.reviews)

        if not page.reviews:
            completion_reason = "terminal"
            break
        if (
            min(_parse_utc(row.updated_at) for row in page.reviews)
            <= checkpoint.scan_floor
        ):
            completion_reason = "overlap_floor"
            break
        if page.cursor in seen_cursors:
            raise RuntimeError("Steam cursor repeated before a completion boundary")
        seen_cursors.add(page.cursor)
        cursor = page.cursor

    if completion_reason is None:
        return IncrementalRunResult(
            pages_fetched=pages_fetched,
            http_attempts=http_attempts,
            rows_observed=rows_observed,
            complete=False,
            completion_reason="page_cap",
            rows_written=0,
            checkpoint_advanced=False,
            high_watermark_at=checkpoint.high_watermark_at,
        )

    committed = process_updated_payloads(
        payloads,
        output_path=normalized_path,
        checkpoint_path=checkpoint_path,
        appid=appid,
        fetched_at=observed_at,
        run_id=run_id,
        overlap_seconds=checkpoint.overlap_seconds,
    )
    return IncrementalRunResult(
        pages_fetched=pages_fetched,
        http_attempts=http_attempts,
        rows_observed=rows_observed,
        complete=True,
        completion_reason=completion_reason,
        rows_written=committed.rows_written,
        checkpoint_advanced=committed.checkpoint_advanced,
        high_watermark_at=committed.high_watermark_at or checkpoint.high_watermark_at,
    )


def _network_fetcher(
    *,
    appid: int,
    page_size: int,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
) -> PageFetcher:
    def fetch(cursor: str, _: int) -> FetchedReviewPage:
        url, _parameters = build_review_request_url(
            appid=appid,
            cursor=cursor,
            num_per_page=page_size,
            filter_value=UPDATED_STREAM_FILTER,
        )
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "entertainment-data-platform-incremental/0.0.1",
            },
            method="GET",
        )
        response: HttpResponse = fetch_bytes_with_retry(
            request,
            timeout_seconds=timeout_seconds,
            policy=retry_policy,
        )
        return FetchedReviewPage(
            body=response.body,
            status=response.status,
            attempts=response.attempts,
            retry_delays_seconds=response.retry_delays_seconds,
            headers=response.headers,
        )

    return fetch


def _write_page_metadata(
    path: Path,
    *,
    fetched: FetchedReviewPage,
    run_id: str,
    appid: int,
    page_number: int,
    cursor: str,
    fetched_at: datetime,
) -> None:
    metadata = {
        "run_id": run_id,
        "appid": appid,
        "stream_filter": UPDATED_STREAM_FILTER,
        "page_number": page_number,
        "cursor_kind": "initial" if cursor == "*" else "returned",
        "cursor_sha256": (
            None if cursor == "*" else hashlib.sha256(cursor.encode()).hexdigest()
        ),
        "fetched_at": _utc_iso(fetched_at),
        "http_status": fetched.status,
        "http_attempts": fetched.attempts,
        "retry_delays_seconds": list(fetched.retry_delays_seconds),
        "response_bytes": len(fetched.body),
        "response_sha256": hashlib.sha256(fetched.body).hexdigest(),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)
    _write_bytes(path, f"{encoded}\n".encode())


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validate_arguments(
    *, run_id: str, appid: int, max_pages: int, page_size: int, timeout_seconds: float
) -> None:
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only safe filename characters")
    if appid <= 0:
        raise ValueError("appid must be positive")
    if not 1 <= max_pages <= 100:
        raise ValueError("max_pages must be in [1, 100]")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be in [1, 100]")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be in (0, 60]")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("updated_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded, completeness-aware local Updated collection."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/updated"))
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=Path("output/updated_reviews.jsonl"),
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("output/updated_checkpoint.json")
    )
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)

    result = run_incremental(
        raw_directory=args.raw_directory,
        normalized_path=args.normalized_output,
        checkpoint_path=args.checkpoint,
        run_id=args.run_id,
        appid=args.appid,
        max_pages=args.max_pages,
        page_size=args.page_size,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
