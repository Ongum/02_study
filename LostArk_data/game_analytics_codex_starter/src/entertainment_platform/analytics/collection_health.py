"""Build a reproducible health mart for local scheduled-batch candidates."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_collection_health(
    *, config_path: Path, root: Path, as_of: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate configured sources at an explicit timestamp.

    An explicit ``as_of`` makes freshness results reproducible instead of
    changing whenever a historical report is rerun.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    sources = config.get("sources")
    if not isinstance(sources, list):
        raise TypeError("collection health config sources must be a list")
    tolerance = float(config.get("freshness_tolerance_multiplier", 1.5))
    if tolerance < 1:
        raise ValueError("freshness_tolerance_multiplier must be at least 1")

    rows = [
        _source_health(source, root=root, as_of=as_of, tolerance=tolerance)
        for source in sources
    ]
    summary = {
        "as_of": _utc_iso(as_of),
        "source_count": len(rows),
        "fresh_sources": sum(row["freshness_status"] == "fresh" for row in rows),
        "stale_sources": sum(row["freshness_status"] == "stale" for row in rows),
        "not_scheduled_sources": sum(
            row["execution_status"] == "manual_local" for row in rows
        ),
        "analysis_ready_sources": sum(
            row["analysis_readiness"] == "ready" for row in rows
        ),
        "source_statuses": {
            row["source_id"]: {
                "freshness": row["freshness_status"],
                "readiness": row["analysis_readiness"],
                "run_count": row["run_count"],
                "data_row_count": row["data_row_count"],
            }
            for row in rows
        },
    }
    return rows, summary


def _source_health(
    source: Mapping[str, Any], *, root: Path, as_of: datetime, tolerance: float
) -> dict[str, Any]:
    source_id = str(source["source_id"])
    timestamps = _load_timestamps(source["time_input"], root)
    if any(timestamp > as_of for timestamp in timestamps):
        raise ValueError(f"{source_id} has an observation after as_of")
    timestamps = sorted(set(timestamps))
    cadence_value = source.get("target_cadence_hours")
    cadence = float(cadence_value) if cadence_value is not None else None
    intervals = [
        (timestamps[index] - timestamps[index - 1]).total_seconds() / 3600
        for index in range(1, len(timestamps))
    ]
    gap_threshold = cadence * tolerance if cadence is not None else None
    gap_count = (
        sum(interval > gap_threshold for interval in intervals)
        if gap_threshold is not None
        else 0
    )
    latest = timestamps[-1] if timestamps else None
    freshness_age = (
        (as_of - latest).total_seconds() / 3600 if latest is not None else None
    )
    if cadence is None:
        freshness_status = "not_applicable"
    elif freshness_age is None:
        freshness_status = "missing"
    else:
        freshness_status = "fresh" if freshness_age <= gap_threshold else "stale"

    expected_runs = _expected_runs(timestamps, cadence)
    coverage_ratio = (
        min(1.0, len(timestamps) / expected_runs) if expected_runs else None
    )
    data_path = root / str(source["data_path"])
    data_row_count = _count_jsonl(data_path)
    checkpoint_files = _glob(root, source.get("checkpoint_pattern"))
    checkpoint_values = [_read_json(path) for path in checkpoint_files]
    checkpoint_completed = sum(
        bool(value.get("completed")) for value in checkpoint_values
    )
    checkpoint_watermarks = sum(
        value.get("high_watermark_at") is not None for value in checkpoint_values
    )
    raw_observed = _raw_review_observations(
        _glob(root, source.get("raw_observation_pattern"))
    )
    replay_ratio = max(0.0, 1 - data_row_count / raw_observed) if raw_observed else None
    minimum_runs = int(source["minimum_runs_for_analysis"])
    if data_row_count == 0 or not timestamps:
        readiness = "not_ready"
    elif len(timestamps) < minimum_runs or (gap_count > 0 and cadence is not None):
        readiness = "limited"
    else:
        readiness = "ready"

    return {
        "source_id": source_id,
        "collection_mode": str(source["collection_mode"]),
        "execution_status": str(source["execution_status"]),
        "target_cadence_hours": cadence,
        "as_of": _utc_iso(as_of),
        "run_count": len(timestamps),
        "first_run_at": _utc_iso(timestamps[0]) if timestamps else None,
        "last_run_at": _utc_iso(latest) if latest else None,
        "freshness_age_hours": round(freshness_age, 4)
        if freshness_age is not None
        else None,
        "freshness_status": freshness_status,
        "expected_runs_in_observed_span": expected_runs,
        "run_coverage_ratio": round(coverage_ratio, 4)
        if coverage_ratio is not None
        else None,
        "maximum_interval_hours": round(max(intervals), 4) if intervals else None,
        "gap_threshold_hours": round(gap_threshold, 4)
        if gap_threshold is not None
        else None,
        "gap_count": gap_count,
        "data_row_count": data_row_count,
        "checkpoint_file_count": len(checkpoint_files),
        "completed_checkpoint_count": checkpoint_completed,
        "watermark_checkpoint_count": checkpoint_watermarks,
        "raw_observed_row_count": raw_observed,
        "replay_or_duplicate_ratio": round(replay_ratio, 4)
        if replay_ratio is not None
        else None,
        "minimum_runs_for_analysis": minimum_runs,
        "analysis_readiness": readiness,
        "quality_flags": _quality_flags(
            execution_status=str(source["execution_status"]),
            freshness_status=freshness_status,
            gap_count=gap_count,
            readiness=readiness,
        ),
    }


def _load_timestamps(spec: Mapping[str, Any], root: Path) -> list[datetime]:
    kind = str(spec["kind"])
    field = str(spec["field"])
    if kind == "jsonl":
        paths = [root / str(spec["path"])]
        values = _read_jsonl(paths[0]) if paths[0].exists() else []
    elif kind == "json_glob":
        paths = _glob(root, spec["pattern"])
        values = [_read_json(path) for path in paths]
    else:
        raise ValueError(f"unsupported time_input kind: {kind}")
    return [_parse_utc(str(value[field])) for value in values if value.get(field)]


def _expected_runs(timestamps: Sequence[datetime], cadence: float | None) -> int | None:
    if not timestamps or cadence is None:
        return None
    span = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
    return math.floor(span / cadence) + 1


def _quality_flags(
    *, execution_status: str, freshness_status: str, gap_count: int, readiness: str
) -> list[str]:
    flags = []
    if execution_status == "manual_local":
        flags.append("not_operationally_scheduled")
    if freshness_status in {"stale", "missing"}:
        flags.append("freshness_breach")
    if gap_count:
        flags.append("cadence_gap_detected")
    if readiness != "ready":
        flags.append("analysis_history_insufficient")
    return flags


def _raw_review_observations(paths: Sequence[Path]) -> int | None:
    if not paths:
        return None
    total = 0
    for path in paths:
        payload = _read_json(path)
        reviews = payload.get("response", {}).get("reviews", [])
        if not isinstance(reviews, list):
            raise TypeError(f"reviews must be a list in {path}")
        total += len(reviews)
    return total


def _glob(root: Path, pattern: object) -> list[Path]:
    if pattern is None:
        return []
    return sorted(path for path in root.glob(str(pattern)) if path.is_file())


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    output = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"expected object at {path}:{line_number}")
            output.append(value)
    return output


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = build_collection_health(
        config_path=args.config,
        root=args.root,
        as_of=_parse_utc(args.as_of),
    )
    _write_jsonl_atomic(args.output, rows)
    _write_json_atomic(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
