from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.jobs.steam_reviews_backfill import (
    FetchedBackfillPage,
    run_backfill,
)

FIXTURE = Path(__file__).parent / "fixtures" / "steam_reviews_page.synthetic.json"
APPID = 1599340
START = datetime(2025, 10, 20, 0, 0, tzinfo=UTC)
END = datetime(2025, 10, 21, 23, 59, 59, tzinfo=UTC)
FETCHED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _page(*, ids: list[int], timestamps: list[int], cursor: str, total: int) -> bytes:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    template = fixture["response"]["reviews"][0]
    reviews = []
    for recommendationid, timestamp in zip(ids, timestamps, strict=True):
        review = copy.deepcopy(template)
        review["recommendationid"] = str(recommendationid)
        review["timestamp_created"] = timestamp
        review["timestamp_updated"] = timestamp
        reviews.append(review)
    fixture["response"]["reviews"] = reviews
    fixture["response"]["query_summary"]["num_reviews"] = len(reviews)
    fixture["response"]["cursor"] = cursor
    fixture["response"]["total_matching"] = total
    return json.dumps(fixture).encode()


def _fetcher(pages: list[bytes], cursors: list[str]):
    pending = iter(pages)
    expected_cursors = iter(cursors)

    def fetch(cursor: str, _: int) -> FetchedBackfillPage:
        assert cursor == next(expected_cursors)
        return FetchedBackfillPage(body=next(pending))

    return fetch


def test_backfill_reaches_terminal_and_separates_versions_from_reviews(
    tmp_path: Path,
) -> None:
    history = tmp_path / "history.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    pages = [
        _page(
            ids=[1001, 1002],
            timestamps=[1760918400, 1760922000],
            cursor="cursor-2",
            total=3,
        ),
        _page(ids=[1003], timestamps=[1760925600], cursor="cursor-3", total=3),
        _page(ids=[], timestamps=[], cursor="terminal", total=3),
    ]

    result = run_backfill(
        appid=APPID,
        range_start=START,
        range_end=END,
        raw_directory=tmp_path / "raw",
        history_path=history,
        checkpoint_path=checkpoint,
        run_id="synthetic-full",
        max_pages_per_run=5,
        request_interval_seconds=0,
        page_fetcher=_fetcher(pages, ["*", "cursor-2", "cursor-3"]),
        fetched_at=FETCHED_AT,
    )

    assert result.completed is True
    assert result.completion_reason == "terminal"
    assert result.rows_observed_total == 3
    assert result.unique_versions_stored == 3
    assert result.unique_reviews_stored == 3
    assert result.duplicate_versions_in_storage == 0
    assert result.out_of_range_rows == 0
    assert len(list((tmp_path / "raw/synthetic-full").glob("*.json"))) == 6


def test_backfill_resumes_from_durable_cursor_without_refetching_page_one(
    tmp_path: Path,
) -> None:
    history = tmp_path / "history.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    first_page = _page(
        ids=[1001, 1002],
        timestamps=[1760918400, 1760922000],
        cursor="cursor-2",
        total=3,
    )

    first = run_backfill(
        appid=APPID,
        range_start=START,
        range_end=END,
        raw_directory=tmp_path / "raw",
        history_path=history,
        checkpoint_path=checkpoint,
        run_id="synthetic-resume",
        max_pages_per_run=1,
        request_interval_seconds=0,
        page_fetcher=_fetcher([first_page], ["*"]),
        fetched_at=FETCHED_AT,
    )
    assert first.completed is False
    assert first.rows_written_this_run == 2

    remaining = [
        _page(ids=[1003], timestamps=[1760925600], cursor="cursor-3", total=3),
        _page(ids=[], timestamps=[], cursor="terminal", total=3),
    ]
    second = run_backfill(
        appid=APPID,
        range_start=START,
        range_end=END,
        raw_directory=tmp_path / "raw",
        history_path=history,
        checkpoint_path=checkpoint,
        run_id="synthetic-resume",
        max_pages_per_run=5,
        request_interval_seconds=0,
        page_fetcher=_fetcher(remaining, ["cursor-2", "cursor-3"]),
        fetched_at=FETCHED_AT,
    )

    assert second.completed is True
    assert second.pages_fetched_this_run == 2
    assert second.pages_committed_total == 3
    assert second.rows_written_this_run == 1
    assert len(history.read_text(encoding="utf-8").splitlines()) == 3

    # A completed rerun is a no-op and never calls the supplied fetcher.
    third = run_backfill(
        appid=APPID,
        range_start=START,
        range_end=END,
        raw_directory=tmp_path / "raw",
        history_path=history,
        checkpoint_path=checkpoint,
        run_id="synthetic-resume",
        page_fetcher=_fetcher([], []),
        fetched_at=FETCHED_AT,
    )
    assert third.completion_reason == "already_complete"
    assert third.pages_fetched_this_run == 0
