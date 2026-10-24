import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.analytics.collection_health import build_collection_health


def test_collection_health_marks_manual_gap_and_limited_history(tmp_path: Path) -> None:
    observations = [
        {"observed_at": "2026-10-21T10:00:00Z", "player_count": 10},
        {"observed_at": "2026-10-21T13:00:00Z", "player_count": 20},
    ]
    data_path = tmp_path / "players.jsonl"
    data_path.write_text(
        "".join(json.dumps(row) + "\n" for row in observations), encoding="utf-8"
    )
    config = {
        "freshness_tolerance_multiplier": 1.5,
        "sources": [
            {
                "source_id": "players",
                "collection_mode": "snapshot_micro_batch",
                "execution_status": "manual_local",
                "target_cadence_hours": 1,
                "minimum_runs_for_analysis": 24,
                "data_path": "players.jsonl",
                "time_input": {
                    "kind": "jsonl",
                    "path": "players.jsonl",
                    "field": "observed_at",
                },
            }
        ],
    }
    config_path = tmp_path / "health.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    rows, summary = build_collection_health(
        config_path=config_path,
        root=tmp_path,
        as_of=datetime(2026, 10, 21, 13, 30, tzinfo=UTC),
    )

    row = rows[0]
    assert row["freshness_status"] == "fresh"
    assert row["expected_runs_in_observed_span"] == 4
    assert row["run_coverage_ratio"] == 0.5
    assert row["gap_count"] == 1
    assert row["analysis_readiness"] == "limited"
    assert "not_operationally_scheduled" in row["quality_flags"]
    assert summary["not_scheduled_sources"] == 1


def test_collection_health_computes_updated_replay_ratio(tmp_path: Path) -> None:
    normalized = tmp_path / "updates.jsonl"
    normalized.write_text(
        "".join(json.dumps({"id": index}) + "\n" for index in range(2)),
        encoding="utf-8",
    )
    metadata = tmp_path / "raw" / "run-1" / "page-0001.metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"fetched_at": "2026-10-21T12:00:00Z"}), encoding="utf-8"
    )
    raw = metadata.with_name("page-0001.json")
    raw.write_text(
        json.dumps({"response": {"reviews": [{}, {}, {}, {}]}}), encoding="utf-8"
    )
    config = {
        "sources": [
            {
                "source_id": "updated",
                "collection_mode": "incremental_micro_batch",
                "execution_status": "manual_local",
                "target_cadence_hours": 6,
                "minimum_runs_for_analysis": 1,
                "data_path": "updates.jsonl",
                "time_input": {
                    "kind": "json_glob",
                    "pattern": "raw/**/*.metadata.json",
                    "field": "fetched_at",
                },
                "raw_observation_pattern": "raw/**/page-*.json",
            }
        ]
    }
    config_path = tmp_path / "health.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    rows, _ = build_collection_health(
        config_path=config_path,
        root=tmp_path,
        as_of=datetime(2026, 10, 21, 13, tzinfo=UTC),
    )

    assert rows[0]["raw_observed_row_count"] == 4
    assert rows[0]["replay_or_duplicate_ratio"] == 0.5
