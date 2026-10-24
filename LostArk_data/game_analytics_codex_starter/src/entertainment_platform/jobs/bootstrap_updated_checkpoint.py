"""Create an Updated-stream checkpoint from a validated local review history."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.connectors.steam.checkpoint import (
    build_updated_checkpoint,
    commit_updated_checkpoint,
    load_updated_checkpoint,
)


def bootstrap(
    *,
    history_path: Path,
    checkpoint_path: Path,
    appid: int,
    overlap_seconds: int,
    run_id: str,
    committed_at: datetime | None = None,
) -> dict[str, object]:
    """Use the greatest observed updated_at without rewriting review data."""

    high_watermarks: list[datetime] = []
    with history_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"history row {line_number} is not an object")
            if row.get("appid") != appid:
                raise ValueError("history contains a different appid")
            try:
                updated = datetime.fromisoformat(str(row["updated_at"]))
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"history row {line_number} has invalid updated_at"
                ) from exc
            if updated.tzinfo is None or updated.utcoffset() is None:
                raise ValueError(f"history row {line_number} has naive updated_at")
            high_watermarks.append(updated.astimezone(UTC))
    if not high_watermarks:
        raise ValueError("history contains no rows")

    candidate = max(high_watermarks)
    existing = load_updated_checkpoint(checkpoint_path)
    if existing is not None:
        if existing.appid != appid or existing.overlap_seconds != overlap_seconds:
            raise ValueError("existing checkpoint does not match bootstrap settings")
        existing_high = datetime.fromisoformat(existing.high_watermark_at).astimezone(
            UTC
        )
        if existing_high < candidate:
            raise ValueError("refusing to replace an older existing checkpoint")
        return {**asdict(existing), "checkpoint_written": False}

    checkpoint = build_updated_checkpoint(
        appid=appid,
        high_watermark=candidate,
        overlap_seconds=overlap_seconds,
        committed_at=committed_at or datetime.now(UTC),
        run_id=run_id,
    )
    commit_updated_checkpoint(checkpoint_path, checkpoint)
    return {**asdict(checkpoint), "checkpoint_written": True}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap Updated checkpoint from normalized history."
    )
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--overlap-seconds", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    args = parser.parse_args(argv)
    result = bootstrap(
        history_path=args.history,
        checkpoint_path=args.checkpoint,
        appid=args.appid,
        overlap_seconds=args.overlap_seconds,
        run_id=args.run_id,
    )
    safe = {
        key: result[key]
        for key in (
            "appid",
            "high_watermark_at",
            "overlap_seconds",
            "checkpoint_written",
        )
    }
    print(json.dumps(safe, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
