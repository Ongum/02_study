"""Merge disjoint or overlapping normalized review histories idempotently."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.jobs.offline_steam_reviews import DEDUPLICATION_KEY


def merge_histories(inputs: Sequence[Path], *, output: Path) -> dict[str, int]:
    if not inputs:
        raise ValueError("at least one input history is required")
    rows_read = 0
    rows_written = 0
    for path in inputs:
        rows: list[dict[str, object]] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError(f"expected object at {path}:{line_number}")
                rows.append(value)
        rows_read += len(rows)
        rows_written += append_unique_jsonl(output, rows, key_fields=DEDUPLICATION_KEY)
    return {"rows_read": rows_read, "rows_written": rows_written}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge normalized review histories.")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = merge_histories(args.input, output=args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
