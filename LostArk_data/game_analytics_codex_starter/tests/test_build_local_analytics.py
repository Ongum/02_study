import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from entertainment_platform.jobs.build_local_analytics import run_local_analytics


def test_missing_input_writes_failed_manifest(tmp_path: Path) -> None:
    config = {
        "appid": 1599340,
        "inputs": {
            "review_history": "missing-reviews.jsonl",
            "news": "missing-news.jsonl",
            "news_raw": "missing-news.json",
            "player_snapshots": "missing-players.jsonl",
            "collection_health_config": "missing-health.json",
        },
        "outputs": {"run_manifest": "manifest.json"},
    }
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="missing pipeline inputs"):
        run_local_analytics(
            config_path=config_path,
            root=tmp_path,
            run_id="failed-test",
            as_of=datetime(2026, 10, 22, tzinfo=UTC),
        )

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["failed_step"] == "validate_inputs"
    assert manifest["steps"] == []
    assert manifest["network_calls"] == 0
