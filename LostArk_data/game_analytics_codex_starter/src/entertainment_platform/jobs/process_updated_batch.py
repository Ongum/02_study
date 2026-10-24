"""Validate and commit a local batch from Steam's Updated review stream.

Transaction order is deliberately visible in this module:

1. Parse and validate every page.
2. Durably append deduplicated normalized rows.
3. Atomically commit the high-watermark checkpoint.

If step 1 or 2 fails, step 3 is never reached. A partial row write is safe to
retry because the JSONL adapter deduplicates review versions.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.checkpoint import (
    build_updated_checkpoint,
    commit_updated_checkpoint,
    load_updated_checkpoint,
)
from entertainment_platform.connectors.steam.reviews import parse_review_page
from entertainment_platform.jobs.offline_steam_reviews import DEDUPLICATION_KEY

RowWriter = Callable[[Path, list[dict[str, object]]], int]


@dataclass(frozen=True, slots=True)
class UpdatedBatchResult:
    pages_validated: int
    rows_validated: int
    rows_written: int
    checkpoint_advanced: bool
    high_watermark_at: str | None


def process_updated_payloads(
    payloads: Sequence[object],
    *,
    output_path: Path,
    checkpoint_path: Path,
    appid: int,
    fetched_at: datetime,
    run_id: str,
    overlap_seconds: int,
    row_writer: RowWriter | None = None,
) -> UpdatedBatchResult:
    """Process already-fetched pages using a data-before-checkpoint boundary."""

    normalized_rows: list[dict[str, object]] = []

    # Validate the complete input batch before opening the output file. This
    # prevents a bad later page from advancing either data or checkpoint state.
    for page_number, payload in enumerate(payloads, start=1):
        page = parse_review_page(
            payload,
            appid=appid,
            fetched_at=fetched_at,
            run_id=f"{run_id}-page-{page_number:03d}",
        )
        normalized_rows.extend(review.to_dict() for review in page.reviews)

    existing = load_updated_checkpoint(checkpoint_path)
    if existing is not None and existing.appid != appid:
        raise ValueError("existing checkpoint appid does not match this batch")
    if existing is not None and existing.overlap_seconds != overlap_seconds:
        raise ValueError("overlap_seconds must match the existing checkpoint")

    if not normalized_rows:
        return UpdatedBatchResult(
            pages_validated=len(payloads),
            rows_validated=0,
            rows_written=0,
            checkpoint_advanced=False,
            high_watermark_at=(
                existing.high_watermark_at if existing is not None else None
            ),
        )

    writer = row_writer or _default_row_writer
    rows_written = writer(output_path, normalized_rows)

    # This line is intentionally after the durable writer. If the writer raises
    # after a partial append, no checkpoint is created and the next run safely
    # deduplicates the rows that reached disk.
    observed_high_watermark = max(
        _parse_utc(str(row["updated_at"])) for row in normalized_rows
    )
    previous_high_watermark = (
        _parse_utc(existing.high_watermark_at) if existing is not None else None
    )
    next_high_watermark = max(
        value
        for value in (observed_high_watermark, previous_high_watermark)
        if value is not None
    )
    checkpoint_advanced = (
        previous_high_watermark is None or next_high_watermark > previous_high_watermark
    )
    checkpoint = build_updated_checkpoint(
        appid=appid,
        high_watermark=next_high_watermark,
        overlap_seconds=overlap_seconds,
        committed_at=datetime.now(UTC),
        run_id=run_id,
    )
    commit_updated_checkpoint(checkpoint_path, checkpoint)

    return UpdatedBatchResult(
        pages_validated=len(payloads),
        rows_validated=len(normalized_rows),
        rows_written=rows_written,
        checkpoint_advanced=checkpoint_advanced,
        high_watermark_at=checkpoint.high_watermark_at,
    )


def _default_row_writer(path: Path, rows: list[dict[str, object]]) -> int:
    return append_unique_jsonl(path, rows, key_fields=DEDUPLICATION_KEY)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("normalized updated_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _load_payloads(paths: Iterable[Path]) -> list[object]:
    payloads: list[object] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            payloads.append(json.load(handle))
    return payloads


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate local Updated pages, write rows, then commit checkpoint."
    )
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--fetched-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--overlap-seconds", type=int, required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    args = parser.parse_args(argv)

    result = process_updated_payloads(
        _load_payloads(args.input),
        output_path=args.output,
        checkpoint_path=args.checkpoint,
        appid=args.appid,
        fetched_at=args.fetched_at,
        run_id=args.run_id,
        overlap_seconds=args.overlap_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
