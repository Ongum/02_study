import json
from pathlib import Path

import pytest

from entertainment_platform.analytics.player_analysis import (
    build_player_summary,
    load_snapshots,
)


def test_player_summary_keeps_small_sample_warning(tmp_path: Path) -> None:
    source = tmp_path / "players.jsonl"
    rows = [
        {"appid": 1599340, "observed_at": "2026-10-21T13:00:00Z", "player_count": 5000},
        {"appid": 1599340, "observed_at": "2026-10-21T22:00:00Z", "player_count": 7000},
    ]
    source.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    summary = build_player_summary(load_snapshots(source))

    assert summary["snapshot_count"] == 2
    assert summary["span_hours"] == 9.0
    assert summary["absolute_change_first_to_last"] == 2000
    assert summary["percent_change_first_to_last"] == 40.0
    assert summary["insufficient_for_daily_pattern"] is True


def test_player_loader_rejects_conflicting_observation_key(tmp_path: Path) -> None:
    source = tmp_path / "players.jsonl"
    rows = [
        {"appid": 1599340, "observed_at": "2026-10-21T13:00:00Z", "player_count": 5000},
        {"appid": 1599340, "observed_at": "2026-10-21T13:00:00Z", "player_count": 5001},
    ]
    source.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="conflicting duplicate"):
        load_snapshots(source)
