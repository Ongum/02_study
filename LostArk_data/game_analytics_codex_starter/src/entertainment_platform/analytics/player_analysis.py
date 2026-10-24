"""Build a small, honest Gold-like summary from player-count snapshots.

The Steam endpoint exposes only a point-in-time count.  This module therefore
does not interpolate missing hours or call two observations a trend.  It keeps
the observed interval and emits explicit sufficiency flags for downstream use.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any


def _parse_utc(value: str) -> datetime:
    """Parse the normalized ISO-8601 timestamp stored by the collector."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include an offset: {value}")
    return parsed


def load_snapshots(path: Path) -> list[dict[str, Any]]:
    """Read, validate, deduplicate, and sort snapshot rows.

    ``(appid, observed_at)`` is the physical observation key.  Conflicting
    counts for the same key are rejected rather than silently choosing one.
    """

    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                appid = int(row["appid"])
                observed_at = str(row["observed_at"])
                player_count = int(row["player_count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid snapshot at {path}:{line_number}") from exc
            if player_count < 0:
                raise ValueError(f"negative player_count at {path}:{line_number}")
            _parse_utc(observed_at)
            key = (appid, observed_at)
            normalized = {**row, "appid": appid, "player_count": player_count}
            if key in by_key and by_key[key]["player_count"] != player_count:
                raise ValueError(f"conflicting duplicate snapshot key: {key}")
            by_key[key] = normalized
    return sorted(by_key.values(), key=lambda row: _parse_utc(row["observed_at"]))


def build_player_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Create measured point/interval statistics without filling data gaps."""

    if not rows:
        return {
            "snapshot_count": 0,
            "interval_count": 0,
            "insufficient_for_daily_pattern": True,
            "interpretation": "no observations",
        }

    timestamps = [_parse_utc(str(row["observed_at"])) for row in rows]
    counts = [int(row["player_count"]) for row in rows]
    intervals = [
        (timestamps[index] - timestamps[index - 1]).total_seconds() / 3600
        for index in range(1, len(rows))
    ]
    first_count, last_count = counts[0], counts[-1]
    absolute_change = last_count - first_count if len(rows) >= 2 else None
    percent_change = (
        round(absolute_change / first_count * 100, 4)
        if absolute_change is not None and first_count > 0
        else None
    )
    span_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
    distinct_utc_dates = len({timestamp.date() for timestamp in timestamps})

    return {
        "snapshot_count": len(rows),
        "interval_count": len(intervals),
        "first_observed_at": rows[0]["observed_at"],
        "last_observed_at": rows[-1]["observed_at"],
        "span_hours": round(span_hours, 4),
        "median_interval_hours": round(median(intervals), 4) if intervals else None,
        "first_player_count": first_count,
        "last_player_count": last_count,
        "minimum_player_count": min(counts),
        "maximum_player_count": max(counts),
        "absolute_change_first_to_last": absolute_change,
        "percent_change_first_to_last": percent_change,
        # Two dates alone are not a daily pattern; 48 hourly points is a modest
        # minimum that covers two full days without inventing observations.
        "insufficient_for_daily_pattern": len(rows) < 48 or distinct_utc_dates < 2,
        "interpretation": "descriptive point observations; not an update effect",
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    """Atomically replace the derived summary; source snapshots stay append-only."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    summary = build_player_summary(load_snapshots(args.input))
    write_summary(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
