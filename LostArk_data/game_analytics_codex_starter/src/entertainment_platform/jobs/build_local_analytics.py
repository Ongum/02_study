"""Rebuild all local derived analytics from already collected data.

This job performs no network calls.  It records every completed step and stops
on the first failure so a partial rebuild cannot be reported as complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from entertainment_platform.analytics.collection_health import build_collection_health
from entertainment_platform.analytics.event_time_evidence import (
    build_event_time_evidence,
)
from entertainment_platform.analytics.player_analysis import (
    build_player_summary,
    load_snapshots,
    write_summary,
)
from entertainment_platform.analytics.review_analysis import run_analysis
from entertainment_platform.analytics.update_classification import (
    classify_news_files,
    write_jsonl_atomic,
)
from entertainment_platform.analytics.update_decision_mart import (
    build_update_decision_rows,
)
from entertainment_platform.analytics.update_impact import build_update_impact_draft


def run_local_analytics(
    *, config_path: Path, root: Path, run_id: str, as_of: datetime
) -> dict[str, Any]:
    """Run the configured local DAG and always write a success/failure manifest."""

    if not run_id.strip():
        raise ValueError("run_id must not be empty")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    config = _read_json(config_path)
    inputs = _path_map(config.get("inputs"), root, "inputs")
    outputs = _path_map(config.get("outputs"), root, "outputs")
    manifest_path = outputs["run_manifest"]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "data_as_of": _utc_iso(as_of),
        "status": "running",
        "network_calls": 0,
        "steps": [],
    }

    current_step = "validate_inputs"
    started = time.perf_counter()
    try:
        required = (
            "review_history",
            "news",
            "news_raw",
            "player_snapshots",
            "collection_health_config",
        )
        missing = [str(inputs[name]) for name in required if not inputs[name].is_file()]
        if missing:
            raise FileNotFoundError(f"missing pipeline inputs: {', '.join(missing)}")
        _record_step(
            manifest,
            current_step,
            started,
            {"input_count": len(required)},
            [inputs[name] for name in required],
            artifact_role="inputs",
        )

        current_step = "review_analysis"
        started = time.perf_counter()
        review_summary = run_analysis(
            history_path=inputs["review_history"],
            current_path=outputs["reviews_current"],
            daily_path=outputs["review_daily"],
            weekly_path=outputs["review_weekly"],
            language_path=outputs["review_language"],
            issue_path=outputs["review_issue"],
            summary_path=outputs["review_summary"],
            expected_appid=int(config["appid"]),
        )
        review_outputs = [
            outputs[name]
            for name in (
                "reviews_current",
                "review_daily",
                "review_weekly",
                "review_language",
                "review_issue",
                "review_summary",
            )
        ]
        _record_step(manifest, current_step, started, review_summary, review_outputs)

        current_step = "update_classification"
        started = time.perf_counter()
        classifications = classify_news_files(inputs["news"], inputs["news_raw"])
        write_jsonl_atomic(outputs["update_classification"], classifications)
        _record_step(
            manifest,
            current_step,
            started,
            {"row_count": len(classifications)},
            [outputs["update_classification"]],
        )

        current_step = "event_time_evidence"
        started = time.perf_counter()
        time_evidence = build_event_time_evidence(inputs["news"], inputs["news_raw"])
        _write_jsonl_atomic(outputs["event_time_evidence"], time_evidence)
        _record_step(
            manifest,
            current_step,
            started,
            {
                "row_count": len(time_evidence),
                "parsed_time_count": sum(
                    row["announced_at_utc"] is not None for row in time_evidence
                ),
            },
            [outputs["event_time_evidence"]],
        )

        current_step = "update_impact"
        started = time.perf_counter()
        impacts = build_update_impact_draft(
            current_reviews_path=outputs["reviews_current"],
            news_path=inputs["news"],
            output_path=outputs["update_impact"],
            window_days=14,
        )
        _record_step(
            manifest,
            current_step,
            started,
            {"row_count": len(impacts)},
            [outputs["update_impact"]],
        )

        current_step = "update_decision"
        started = time.perf_counter()
        decisions = build_update_decision_rows(classifications, impacts, time_evidence)
        _write_jsonl_atomic(outputs["update_decision"], decisions)
        _record_step(
            manifest,
            current_step,
            started,
            {
                "row_count": len(decisions),
                "evaluated_event_count": sum(
                    row["observed_reaction_score"] is not None for row in decisions
                ),
            },
            [outputs["update_decision"]],
        )

        current_step = "player_summary"
        started = time.perf_counter()
        player_summary = build_player_summary(
            load_snapshots(inputs["player_snapshots"])
        )
        write_summary(outputs["player_summary"], player_summary)
        _record_step(
            manifest,
            current_step,
            started,
            player_summary,
            [outputs["player_summary"]],
        )

        current_step = "collection_health"
        started = time.perf_counter()
        health_rows, health_summary = build_collection_health(
            config_path=inputs["collection_health_config"], root=root, as_of=as_of
        )
        _write_jsonl_atomic(outputs["collection_health"], health_rows)
        _write_json_atomic(outputs["collection_health_summary"], health_summary)
        _record_step(
            manifest,
            current_step,
            started,
            health_summary,
            [outputs["collection_health"], outputs["collection_health_summary"]],
        )
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failed_step"] = current_step
        manifest["error_type"] = type(exc).__name__
        manifest["error_message"] = str(exc)
        _write_json_atomic(manifest_path, manifest)
        raise

    manifest["status"] = "complete"
    manifest["completed_step_count"] = len(manifest["steps"])
    manifest["failed_step"] = None
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _record_step(
    manifest: dict[str, Any],
    name: str,
    started: float,
    metrics: Mapping[str, Any],
    output_paths: Sequence[Path],
    artifact_role: str = "outputs",
) -> None:
    if artifact_role not in {"inputs", "outputs"}:
        raise ValueError("artifact_role must be inputs or outputs")
    missing_artifacts = [str(path) for path in output_paths if not path.is_file()]
    if missing_artifacts:
        raise FileNotFoundError(
            f"step {artifact_role} are missing: {', '.join(missing_artifacts)}"
        )
    step = {
        "name": name,
        "status": "complete",
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "metrics": _json_safe(metrics),
        artifact_role: [
            {
                "path": path.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in output_paths
        ],
    }
    manifest["steps"].append(step)


def _path_map(value: object, root: Path, label: str) -> dict[str, Path]:
    if not isinstance(value, dict):
        raise TypeError(f"pipeline config {label} must be an object")
    return {str(key): root / str(path) for key, path in value.items()}


def _json_safe(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), default=str))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


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


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    manifest = run_local_analytics(
        config_path=args.config,
        root=args.root,
        run_id=args.run_id,
        as_of=_parse_utc(args.as_of),
    )
    print(
        json.dumps(
            {
                "run_id": manifest["run_id"],
                "status": manifest["status"],
                "completed_step_count": manifest["completed_step_count"],
                "network_calls": manifest["network_calls"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
