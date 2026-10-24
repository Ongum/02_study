from __future__ import annotations

import json
from pathlib import Path

from entertainment_platform.analytics.review_analysis import run_analysis


def _row(
    *,
    recommendationid: str,
    created_at: str,
    updated_at: str,
    voted_up: bool,
    text: str,
    payload_hash: str,
) -> dict[str, object]:
    return {
        "appid": 1599340,
        "recommendationid": recommendationid,
        "language": "english",
        "review_text": text,
        "created_at": created_at,
        "updated_at": updated_at,
        "voted_up": voted_up,
        "steam_purchase": True,
        "received_for_free": False,
        "written_during_early_access": False,
        "refunded": False,
        "votes_up": 0,
        "votes_funny": 0,
        "weighted_vote_score": 0.0,
        "comment_count": 0,
        "fetched_at": "2026-01-10T00:00:00Z",
        "run_id": "synthetic-analysis",
        "source_endpoint": "synthetic",
        "page_fingerprint": "synthetic",
        "payload_hash": payload_hash,
    }


def test_analysis_selects_latest_version_and_writes_aggregate_only_marts(
    tmp_path: Path,
) -> None:
    rows = [
        _row(
            recommendationid="1",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            voted_up=True,
            text="old positive version",
            payload_hash="old",
        ),
        _row(
            recommendationid="1",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            voted_up=False,
            text="server lag and bots",
            payload_hash="new",
        ),
        _row(
            recommendationid="2",
            created_at="2026-01-08T00:00:00Z",
            updated_at="2026-01-08T00:00:00Z",
            voted_up=False,
            text="pay to win grind",
            payload_hash="second",
        ),
    ]
    history = tmp_path / "history.jsonl"
    history.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    current = tmp_path / "current.jsonl"
    issues = tmp_path / "issues.jsonl"

    summary = run_analysis(
        history_path=history,
        current_path=current,
        daily_path=tmp_path / "daily.jsonl",
        weekly_path=tmp_path / "weekly.jsonl",
        language_path=tmp_path / "language.jsonl",
        issue_path=issues,
        summary_path=tmp_path / "summary.json",
    )

    current_rows = [json.loads(line) for line in current.read_text().splitlines()]
    issue_rows = [json.loads(line) for line in issues.read_text().splitlines()]
    assert summary["history_version_count"] == 3
    assert summary["current_review_count"] == 2
    assert summary["negative_count"] == 2
    assert summary["modified_review_count"] == 1
    assert len(current_rows) == 2
    assert (
        next(row for row in current_rows if row["recommendationid"] == "1")[
            "payload_hash"
        ]
        == "new"
    )
    assert {row["issue_category"] for row in issue_rows} >= {
        "server_or_performance",
        "bots_or_cheating",
        "monetization_or_grind",
    }
    assert all("review_text" not in row for row in issue_rows)
