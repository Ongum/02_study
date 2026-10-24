from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.analytics.merge_review_history import merge_histories
from entertainment_platform.common.http import HttpResponse
from entertainment_platform.connectors.steam.players import parse_player_count
from entertainment_platform.jobs.steam_current_players import run


def test_player_parser_and_job_rerun_are_idempotent(tmp_path: Path) -> None:
    body = json.dumps({"response": {"player_count": 12345, "result": 1}}).encode()
    calls = 0

    def fetcher(_: object) -> HttpResponse:
        nonlocal calls
        calls += 1
        return HttpResponse(
            status=200,
            headers={},
            body=body,
            attempts=1,
            retry_delays_seconds=(),
        )

    output = tmp_path / "players.jsonl"
    first = run(
        appid=1599340,
        run_id="synthetic-player",
        raw_directory=tmp_path / "raw",
        output_path=output,
        observed_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        fetcher=fetcher,
    )
    second = run(
        appid=1599340,
        run_id="synthetic-player",
        raw_directory=tmp_path / "raw",
        output_path=output,
        fetcher=fetcher,
    )

    assert first["player_count"] == 12345
    assert first["rows_written"] == 1
    assert second["rows_written"] == 0
    assert second["request_count"] == 0
    assert calls == 1
    assert len(output.read_text().splitlines()) == 1


def test_player_parser_omits_unneeded_user_fields() -> None:
    snapshot = parse_player_count(
        {"response": {"player_count": 5, "result": 1}},
        appid=1599340,
        observed_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        run_id="synthetic",
    )
    assert snapshot.to_dict().keys() == {
        "appid",
        "observed_at",
        "player_count",
        "run_id",
        "source_endpoint",
        "response_sha256",
    }


def test_merge_histories_deduplicates_overlapping_versions(tmp_path: Path) -> None:
    shared = {
        "appid": 1599340,
        "recommendationid": "1",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps(shared) + "\n", encoding="utf-8")
    second.write_text(
        json.dumps(shared)
        + "\n"
        + json.dumps({**shared, "recommendationid": "2"})
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "merged.jsonl"

    result = merge_histories([first, second], output=output)
    rerun = merge_histories([first, second], output=output)

    assert result == {"rows_read": 3, "rows_written": 2}
    assert rerun == {"rows_read": 3, "rows_written": 0}
    assert len(output.read_text().splitlines()) == 2
