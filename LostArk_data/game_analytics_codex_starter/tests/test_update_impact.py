from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from entertainment_platform.analytics.update_impact import build_update_impact_draft


def test_update_impact_uses_publication_only_as_unvalidated_proxy(
    tmp_path: Path,
) -> None:
    reference = datetime(2026, 6, 15, tzinfo=UTC)
    reviews = []
    for index in range(40):
        reviews.append(
            {
                "appid": 1599340,
                "recommendationid": f"pre-{index}",
                "created_at": (
                    reference - timedelta(days=1, minutes=index)
                ).isoformat(),
                "voted_up": index >= 20,
            }
        )
        reviews.append(
            {
                "appid": 1599340,
                "recommendationid": f"post-{index}",
                "created_at": (
                    reference + timedelta(days=1, minutes=index)
                ).isoformat(),
                "voted_up": index >= 10,
            }
        )
    # Extend observed coverage so both 14-day windows are complete.
    reviews.extend(
        [
            {
                "appid": 1599340,
                "recommendationid": "coverage-start",
                "created_at": (reference - timedelta(days=20)).isoformat(),
                "voted_up": True,
            },
            {
                "appid": 1599340,
                "recommendationid": "coverage-end",
                "created_at": (reference + timedelta(days=20)).isoformat(),
                "voted_up": True,
            },
        ]
    )
    news = [
        {
            "appid": 1599340,
            "news_id": "candidate-1",
            "title": "Synthetic Update",
            "published_at": reference.isoformat(),
            "effective_at": None,
            "candidate_event_type": "patch_or_update_candidate",
        }
    ]
    reviews_path = tmp_path / "reviews.jsonl"
    news_path = tmp_path / "news.jsonl"
    reviews_path.write_text(
        "".join(json.dumps(row) + "\n" for row in reviews), encoding="utf-8"
    )
    news_path.write_text(json.dumps(news[0]) + "\n", encoding="utf-8")

    output = build_update_impact_draft(
        current_reviews_path=reviews_path,
        news_path=news_path,
        output_path=tmp_path / "impact.jsonl",
        window_days=14,
    )

    row = output[0]
    assert row["reference_type"] == "publication_proxy_unvalidated"
    assert row["pre_total"] == 40
    assert row["post_total"] == 40
    assert row["negative_rate_delta_pp"] == -25.0
    assert row["pre_window_complete"] is True
    assert row["post_window_complete"] is True
    assert row["quality_flags"] == ["effective_time_unvalidated"]
    assert row["causal_claim_allowed"] is False
