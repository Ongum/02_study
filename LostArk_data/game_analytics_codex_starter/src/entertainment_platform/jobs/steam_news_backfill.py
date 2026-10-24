"""Bounded, resumable local Steam app-news collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.checkpoint import (
    read_json_checkpoint,
    write_json_checkpoint,
)
from entertainment_platform.common.http import (
    HttpResponse,
    RetryPolicy,
    fetch_bytes_with_retry,
)
from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.news import (
    STEAM_NEWS_ENDPOINT,
    parse_news_page,
)

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class NewsCheckpoint:
    schema_version: int
    appid: int
    range_start_epoch: int
    range_end_epoch: int
    next_enddate_epoch: int
    next_page_number: int
    pages_committed: int
    completed: bool
    run_id: str
    committed_at: str


@dataclass(frozen=True, slots=True)
class NewsBackfillResult:
    appid: int
    pages_fetched_this_run: int
    http_attempts_this_run: int
    pages_committed_total: int
    rows_written_this_run: int
    rows_in_range_stored: int
    update_candidates_stored: int
    completed: bool
    completion_reason: str
    earliest_published_at: str | None
    latest_published_at: str | None


Fetcher = Callable[[int, int], HttpResponse]


def run_news_backfill(
    *,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    run_id: str,
    raw_directory: Path,
    output_path: Path,
    checkpoint_path: Path,
    max_pages_per_run: int = 5,
    page_size: int = 100,
    request_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    retry_policy: RetryPolicy | None = None,
    fetcher: Fetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
    fetched_at: datetime | None = None,
) -> NewsBackfillResult:
    start_epoch, end_epoch = _validate(
        appid=appid,
        range_start=range_start,
        range_end=range_end,
        run_id=run_id,
        max_pages=max_pages_per_run,
        page_size=page_size,
        request_interval=request_interval_seconds,
    )
    observed_at = fetched_at or datetime.now(UTC)
    checkpoint = _load_checkpoint(checkpoint_path)
    if checkpoint is None:
        checkpoint = NewsCheckpoint(
            schema_version=1,
            appid=appid,
            range_start_epoch=start_epoch,
            range_end_epoch=end_epoch,
            next_enddate_epoch=end_epoch + 1,
            next_page_number=1,
            pages_committed=0,
            completed=False,
            run_id=run_id,
            committed_at=_utc_iso(observed_at),
        )
    _validate_checkpoint(checkpoint, appid, start_epoch, end_epoch)
    if checkpoint.completed:
        return _result(checkpoint, output_path, 0, 0, 0, "already_complete")

    fetch = fetcher or _network_fetcher(
        appid=appid,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
    )
    pages_fetched = 0
    attempts = 0
    rows_written = 0
    completion_reason = "page_cap"

    for request_index in range(max_pages_per_run):
        response = fetch(checkpoint.next_enddate_epoch, checkpoint.next_page_number)
        pages_fetched += 1
        attempts += response.attempts
        digest = hashlib.sha256(response.body).hexdigest()
        raw_path = (
            raw_directory
            / run_id
            / f"page-{checkpoint.next_page_number:04d}-{digest[:12]}.json"
        )
        _write_once(raw_path, response.body)
        payload = json.loads(response.body)
        parsed = parse_news_page(
            payload,
            appid=appid,
            fetched_at=observed_at,
            run_id=f"{run_id}-page-{checkpoint.next_page_number:04d}",
        )
        in_range = [
            item.to_dict()
            for item in parsed.items
            if start_epoch
            <= int(_parse_utc(item.published_at).timestamp())
            <= end_epoch
        ]
        rows_written += append_unique_jsonl(
            output_path, in_range, key_fields=("appid", "news_id")
        )

        published_epochs = [
            int(_parse_utc(item.published_at).timestamp()) for item in parsed.items
        ]
        completed = not published_epochs or min(published_epochs) < start_epoch
        next_enddate = (
            checkpoint.next_enddate_epoch
            if not published_epochs
            else min(published_epochs) - 1
        )
        checkpoint = NewsCheckpoint(
            schema_version=1,
            appid=appid,
            range_start_epoch=start_epoch,
            range_end_epoch=end_epoch,
            next_enddate_epoch=next_enddate,
            next_page_number=checkpoint.next_page_number + 1,
            pages_committed=checkpoint.pages_committed + 1,
            completed=completed,
            run_id=run_id,
            committed_at=_utc_iso(datetime.now(UTC)),
        )
        write_json_checkpoint(checkpoint_path, asdict(checkpoint))
        if completed:
            completion_reason = "range_floor" if published_epochs else "terminal"
            break
        if request_index + 1 < max_pages_per_run and request_interval_seconds > 0:
            sleep(request_interval_seconds)

    return _result(
        checkpoint,
        output_path,
        pages_fetched,
        attempts,
        rows_written,
        completion_reason,
    )


def _network_fetcher(
    *, appid: int, page_size: int, timeout_seconds: float, retry_policy: RetryPolicy
) -> Fetcher:
    def fetch(enddate: int, _: int) -> HttpResponse:
        query = urllib.parse.urlencode(
            {
                "appid": appid,
                "count": page_size,
                "maxlength": 500,
                "enddate": enddate,
            }
        )
        request = urllib.request.Request(
            f"{STEAM_NEWS_ENDPOINT}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "entertainment-data-platform-news/0.0.1",
            },
            method="GET",
        )
        return fetch_bytes_with_retry(
            request,
            timeout_seconds=timeout_seconds,
            policy=retry_policy,
        )

    return fetch


def _result(
    checkpoint: NewsCheckpoint,
    output_path: Path,
    pages_fetched: int,
    attempts: int,
    rows_written: int,
    completion_reason: str,
) -> NewsBackfillResult:
    rows = _read_rows(output_path)
    published = [_parse_utc(str(row["published_at"])) for row in rows]
    candidates = sum(
        row.get("candidate_event_type") == "patch_or_update_candidate" for row in rows
    )
    return NewsBackfillResult(
        appid=checkpoint.appid,
        pages_fetched_this_run=pages_fetched,
        http_attempts_this_run=attempts,
        pages_committed_total=checkpoint.pages_committed,
        rows_written_this_run=rows_written,
        rows_in_range_stored=len(rows),
        update_candidates_stored=candidates,
        completed=checkpoint.completed,
        completion_reason=completion_reason,
        earliest_published_at=_utc_iso(min(published)) if published else None,
        latest_published_at=_utc_iso(max(published)) if published else None,
    )


def _load_checkpoint(path: Path) -> NewsCheckpoint | None:
    payload = read_json_checkpoint(path)
    if payload is None:
        return None
    try:
        return NewsCheckpoint(**payload)
    except TypeError as exc:
        raise ValueError("news checkpoint fields are invalid") from exc


def _validate_checkpoint(
    checkpoint: NewsCheckpoint, appid: int, start_epoch: int, end_epoch: int
) -> None:
    if checkpoint.schema_version != 1:
        raise ValueError("unsupported news checkpoint version")
    if checkpoint.appid != appid:
        raise ValueError("news checkpoint appid does not match")
    if (
        checkpoint.range_start_epoch != start_epoch
        or checkpoint.range_end_epoch != end_epoch
    ):
        raise ValueError("news checkpoint range does not match")
    if checkpoint.next_page_number < 1 or checkpoint.pages_committed < 0:
        raise ValueError("news checkpoint page counters are invalid")


def _validate(
    *,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    run_id: str,
    max_pages: int,
    page_size: int,
    request_interval: float,
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
    if not 1 <= max_pages <= 5:
        raise ValueError("max_pages must be in [1, 5]")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be in [1, 100]")
    if not 0 <= request_interval <= 10:
        raise ValueError("request interval must be in [0, 10]")
    return int(range_start.timestamp()), int(range_end.timestamp())


def _read_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("news row must be an object")
                rows.append(value)
    return rows


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite raw file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect bounded Steam app news.")
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-interval-seconds", type=float, default=1.0)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/news"))
    parser.add_argument(
        "--output", type=Path, default=Path("output/silver_app_news.jsonl")
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=Path("output/news_checkpoint.json")
    )
    args = parser.parse_args(argv)
    result = run_news_backfill(
        appid=args.appid,
        range_start=args.start,
        range_end=args.end,
        run_id=args.run_id,
        raw_directory=args.raw_directory,
        output_path=args.output,
        checkpoint_path=args.checkpoint,
        max_pages_per_run=args.max_pages,
        page_size=args.page_size,
        request_interval_seconds=args.request_interval_seconds,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
