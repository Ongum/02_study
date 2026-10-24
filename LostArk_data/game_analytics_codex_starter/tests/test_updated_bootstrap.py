from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.analytics.version_coverage import compare_versions
from entertainment_platform.jobs.bootstrap_updated_checkpoint import bootstrap


def test_bootstrap_uses_max_updated_at_and_is_idempotent(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    rows = [
        {
            "appid": 1599340,
            "recommendationid": "1",
            "updated_at": "2026-10-20T01:00:00Z",
        },
        {
            "appid": 1599340,
            "recommendationid": "2",
            "updated_at": "2026-10-20T03:00:00Z",
        },
    ]
    history.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    checkpoint = tmp_path / "checkpoint.json"
    first = bootstrap(
        history_path=history,
        checkpoint_path=checkpoint,
        appid=1599340,
        overlap_seconds=86400,
        run_id="synthetic-bootstrap",
        committed_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    second = bootstrap(
        history_path=history,
        checkpoint_path=checkpoint,
        appid=1599340,
        overlap_seconds=86400,
        run_id="synthetic-bootstrap-rerun",
    )
    assert first["high_watermark_at"] == "2026-10-20T03:00:00Z"
    assert first["checkpoint_written"] is True
    assert second["checkpoint_written"] is False


def test_version_coverage_distinguishes_replay_new_version_and_new_review(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.jsonl"
    updated = tmp_path / "updated.jsonl"
    baseline_rows = [
        {"recommendationid": "1", "updated_at": "2026-01-01T00:00:00Z"},
        {"recommendationid": "2", "updated_at": "2026-01-01T00:00:00Z"},
    ]
    updated_rows = [
        {"recommendationid": "1", "updated_at": "2026-01-01T00:00:00Z"},
        {"recommendationid": "2", "updated_at": "2026-01-02T00:00:00Z"},
        {"recommendationid": "3", "updated_at": "2026-01-02T00:00:00Z"},
    ]
    baseline.write_text(
        "".join(json.dumps(row) + "\n" for row in baseline_rows), encoding="utf-8"
    )
    updated.write_text(
        "".join(json.dumps(row) + "\n" for row in updated_rows), encoding="utf-8"
    )
    result = compare_versions(
        baseline_path=baseline,
        updated_path=updated,
        summary_path=tmp_path / "summary.json",
    )
    assert result["overlap_review_ids"] == 2
    assert result["replayed_existing_versions"] == 1
    assert result["new_versions_for_baseline_reviews"] == 1
    assert result["updated_reviews_outside_baseline"] == 1
    assert result["outside_unclassified_missing_created_at"] == 1
