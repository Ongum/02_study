import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from entertainment_platform.jobs.collect_local_incremental import (
    CollectionLockError,
    run_local_incremental_collection,
)


def _config(tmp_path: Path) -> Path:
    value = {
        "appid": 1599340,
        "lock_path": "locks/collection.lock",
        "manifest_directory": "manifests",
        "reviews": {
            "raw_directory": "raw/reviews",
            "normalized_output": "output/reviews.jsonl",
            "checkpoint": "output/reviews-checkpoint.json",
            "max_pages": 2,
            "page_size": 100,
            "timeout_seconds": 5,
        },
        "players": {
            "raw_directory": "raw/players",
            "output": "output/players.jsonl",
            "timeout_seconds": 5,
        },
        "news": {
            "raw_directory": "raw/news",
            "output": "output/news.jsonl",
            "checkpoint_directory": "output/news-checkpoints",
            "lookback_hours": 48,
            "max_pages": 2,
            "page_size": 100,
            "request_interval_seconds": 0,
            "timeout_seconds": 5,
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_complete_run_writes_manifest_and_releases_lock(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def success(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"complete": True, "rows_written": 1, "request_count": 1}

    result = run_local_incremental_collection(
        config_path=_config(tmp_path),
        root=tmp_path,
        run_id="test-run-01",
        as_of=datetime(2026, 10, 22, tzinfo=UTC),
        reviews_runner=success,
        players_runner=success,
        news_runner=success,
    )

    assert result["status"] == "complete"
    assert [step["name"] for step in result["steps"]] == [
        "reviews_updated",
        "current_players",
        "app_news",
    ]
    assert len(calls) == 3
    assert not (tmp_path / "locks/collection.lock").exists()
    saved = json.loads((tmp_path / "manifests/test-run-01.json").read_text())
    assert saved["cloud_resources_created"] is False


def test_source_failure_does_not_stop_other_sources(tmp_path: Path) -> None:
    calls: list[str] = []

    def failed(**_: object) -> dict[str, object]:
        calls.append("reviews")
        raise TimeoutError("bounded test failure")

    def players(**_: object) -> dict[str, object]:
        calls.append("players")
        return {"rows_written": 1}

    def incomplete_news(**_: object) -> dict[str, object]:
        calls.append("news")
        return {"complete": False, "completion_reason": "page_cap"}

    result = run_local_incremental_collection(
        config_path=_config(tmp_path),
        root=tmp_path,
        run_id="test-run-02",
        as_of=datetime(2026, 10, 22, tzinfo=UTC),
        reviews_runner=failed,
        players_runner=players,
        news_runner=incomplete_news,
    )

    assert calls == ["reviews", "players", "news"]
    assert result["status"] == "partial_failure"
    assert [step["status"] for step in result["steps"]] == [
        "failed",
        "complete",
        "incomplete",
    ]


def test_existing_lock_blocks_all_collectors(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    lock = tmp_path / "locks/collection.lock"
    lock.parent.mkdir()
    lock.write_text('{"run_id":"other-run"}', encoding="utf-8")
    called = False

    def should_not_run(**_: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(CollectionLockError, match="already exists"):
        run_local_incremental_collection(
            config_path=config_path,
            root=tmp_path,
            run_id="test-run-03",
            reviews_runner=should_not_run,
            players_runner=should_not_run,
            news_runner=should_not_run,
        )

    assert called is False
    assert lock.exists()


def test_configured_path_cannot_escape_project_root(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    config = json.loads(config_path.read_text())
    config["lock_path"] = "../outside.lock"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes project root"):
        run_local_incremental_collection(
            config_path=config_path,
            root=tmp_path,
            run_id="test-run-04",
        )


def test_run_id_cannot_escape_manifest_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="safe filename"):
        run_local_incremental_collection(
            config_path=_config(tmp_path),
            root=tmp_path,
            run_id="../unsafe",
        )
