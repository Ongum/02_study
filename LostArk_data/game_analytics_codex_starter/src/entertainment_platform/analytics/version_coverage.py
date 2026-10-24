"""Compare a backfill baseline with versions observed by the Updated stream."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path


def compare_versions(
    *, baseline_path: Path, updated_path: Path, summary_path: Path
) -> dict[str, int]:
    baseline = _read_rows(baseline_path)
    updated = _read_rows(updated_path)
    baseline_ids = {str(row["recommendationid"]) for row in baseline}
    baseline_keys = {
        (str(row["recommendationid"]), str(row["updated_at"])) for row in baseline
    }
    updated_ids = {str(row["recommendationid"]) for row in updated}
    updated_keys = {
        (str(row["recommendationid"]), str(row["updated_at"])) for row in updated
    }
    overlap_ids = baseline_ids & updated_ids
    new_versions_for_baseline_ids = {
        key
        for key in updated_keys
        if key[0] in baseline_ids and key not in baseline_keys
    }
    baseline_created = [
        str(row["created_at"])
        for row in baseline
        if isinstance(row.get("created_at"), str)
    ]
    baseline_created_min = min(baseline_created) if baseline_created else None
    baseline_created_max = max(baseline_created) if baseline_created else None
    outside_rows = [
        row for row in updated if str(row["recommendationid"]) not in baseline_ids
    ]
    outside_before = 0
    outside_after = 0
    outside_within = 0
    outside_unclassified = 0
    for row in outside_rows:
        created_at = row.get("created_at")
        if (
            not isinstance(created_at, str)
            or baseline_created_min is None
            or baseline_created_max is None
        ):
            outside_unclassified += 1
        elif created_at < baseline_created_min:
            outside_before += 1
        elif created_at > baseline_created_max:
            outside_after += 1
        else:
            outside_within += 1
    result = {
        "baseline_rows": len(baseline),
        "baseline_unique_reviews": len(baseline_ids),
        "updated_rows": len(updated),
        "updated_unique_reviews": len(updated_ids),
        "overlap_review_ids": len(overlap_ids),
        "replayed_existing_versions": len(updated_keys & baseline_keys),
        "new_versions_for_baseline_reviews": len(new_versions_for_baseline_ids),
        "updated_reviews_outside_baseline": len(updated_ids - baseline_ids),
        "outside_created_before_baseline": outside_before,
        "outside_created_after_baseline": outside_after,
        "outside_created_within_baseline_but_missing": outside_within,
        "outside_unclassified_missing_created_at": outside_unclassified,
    }
    _write_json(summary_path, result)
    return result


def _read_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"row {line_number} in {path} is not an object")
            rows.append(row)
    return rows


def _write_json(path: Path, value: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare review version coverage.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--updated", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)
    result = compare_versions(
        baseline_path=args.baseline,
        updated_path=args.updated,
        summary_path=args.summary,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
