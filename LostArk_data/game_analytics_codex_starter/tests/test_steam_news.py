from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.http import HttpResponse
from entertainment_platform.connectors.steam.news import parse_news_page
from entertainment_platform.jobs.steam_news_backfill import run_news_backfill


def _payload(items: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"appnews": {"appid": 1599340, "newsitems": items, "count": len(items)}}
    ).encode()


def _item(gid: str, *, title: str, published: int) -> dict[str, object]:
    return {
        "gid": gid,
        "title": title,
        "url": f"https://example.invalid/{gid}",
        "is_external_url": False,
        "author": "synthetic author must be omitted",
        "contents": "synthetic contents must be omitted",
        "feedlabel": "Synthetic",
        "date": published,
        "feedname": "synthetic",
        "feed_type": 1,
        "appid": 1599340,
    }


def test_news_parser_omits_author_content_and_keeps_effective_time_null() -> None:
    payload = json.loads(
        _payload([_item("1", title="October Update Notes", published=1761000000)])
    )
    parsed = parse_news_page(
        payload,
        appid=1599340,
        fetched_at=datetime(2026, 10, 21, tzinfo=UTC),
        run_id="synthetic-news",
    )
    row = parsed.items[0].to_dict()
    assert row["candidate_event_type"] == "patch_or_update_candidate"
    assert row["effective_at"] is None
    assert "author" not in row
    assert "contents" not in row


def test_news_backfill_stops_after_crossing_range_floor(tmp_path: Path) -> None:
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = datetime(2026, 10, 21, tzinfo=UTC)
    body = _payload(
        [
            _item(
                "in-range",
                title="Patch Notes",
                published=int(datetime(2026, 10, 20, tzinfo=UTC).timestamp()),
            ),
            _item(
                "too-old",
                title="Old News",
                published=int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()),
            ),
        ]
    )
    calls = 0

    def fetcher(_: int, __: int) -> HttpResponse:
        nonlocal calls
        calls += 1
        return HttpResponse(
            status=200,
            headers={},
            body=body,
            attempts=1,
            retry_delays_seconds=(),
        )

    output = tmp_path / "news.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    first = run_news_backfill(
        appid=1599340,
        range_start=start,
        range_end=end,
        run_id="synthetic-news",
        raw_directory=tmp_path / "raw",
        output_path=output,
        checkpoint_path=checkpoint,
        request_interval_seconds=0,
        fetcher=fetcher,
        fetched_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    second = run_news_backfill(
        appid=1599340,
        range_start=start,
        range_end=end,
        run_id="synthetic-news",
        raw_directory=tmp_path / "raw",
        output_path=output,
        checkpoint_path=checkpoint,
        request_interval_seconds=0,
        fetcher=fetcher,
        fetched_at=datetime(2026, 10, 21, tzinfo=UTC),
    )

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert first.completed is True
    assert first.completion_reason == "range_floor"
    assert first.rows_written_this_run == 1
    assert second.completion_reason == "already_complete"
    assert calls == 1
    assert [row["news_id"] for row in rows] == ["in-range"]
