"""Run the offline Steam review fixture-to-JSONL vertical slice."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.reviews import parse_review_page

DEDUPLICATION_KEY = ("appid", "recommendationid", "updated_at")


def run(
    *,
    input_path: Path,
    output_path: Path,
    appid: int,
    fetched_at: datetime,
    run_id: str,
) -> int:
    """Parse, validate, normalize and durably append one offline response page."""

    with input_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    page = parse_review_page(payload, appid=appid, fetched_at=fetched_at, run_id=run_id)
    return append_unique_jsonl(
        output_path,
        (review.to_dict() for review in page.reviews),
        key_fields=DEDUPLICATION_KEY,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process an offline synthetic Steam review response fixture."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--fetched-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    written = run(
        input_path=args.input,
        output_path=args.output,
        appid=args.appid,
        fetched_at=args.fetched_at,
        run_id=args.run_id,
    )
    print(json.dumps({"rows_written": written, "status": "local_file_processed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
