from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from entertainment_platform.connectors.steam.checkpoint import (
    build_updated_checkpoint,
    commit_updated_checkpoint,
    load_updated_checkpoint,
)
from entertainment_platform.jobs.steam_reviews_incremental import (
    FetchedReviewPage,
    run_incremental,
)

FIXTURE = Path(__file__).parent / "fixtures" / "steam_reviews_page.synthetic.json"
APPID = 1599340
FETCHED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _page(*, timestamps: list[int], cursor: str) -> bytes:
    """Build a valid synthetic page without pretending it came from Steam."""

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    template = fixture["response"]["reviews"][0]
    reviews = []
    for index, timestamp in enumerate(timestamps, start=1):
        review = copy.deepcopy(template)
        review["recommendationid"] = str(91000000000000000 + timestamp + index)
        review["timestamp_created"] = timestamp
        review["timestamp_updated"] = timestamp
        reviews.append(review)
    fixture["response"]["reviews"] = reviews
    fixture["response"]["query_summary"]["num_reviews"] = len(reviews)
    fixture["response"]["cursor"] = cursor
    fixture["response"]["total_matching"] = len(reviews)
    return json.dumps(fixture).encode("utf-8")


def _checkpoint(path: Path) -> bytes:
    checkpoint = build_updated_checkpoint(
        appid=APPID,
        high_watermark=datetime(2025, 10, 20, 2, 0, tzinfo=UTC),
        overlap_seconds=3600,
        committed_at=FETCHED_AT,
        run_id="synthetic-bootstrap",
    )
    commit_updated_checkpoint(path, checkpoint)
    return path.read_bytes()


def _sequence_fetcher(pages: list[bytes]):
    pending = iter(pages)

    def fetch(_: str, __: int) -> FetchedReviewPage:
        return FetchedReviewPage(body=next(pending), attempts=1)

    return fetch


def test_incremental_commits_only_after_crossing_overlap_floor(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    _checkpoint(checkpoint_path)
    # The checkpoint scan floor is 01:00Z. The second page crosses it.
    pages = [
        _page(timestamps=[1760932800, 1760929200], cursor="page-2"),
        _page(timestamps=[1760923800, 1760920200], cursor="page-3"),
    ]
    output = tmp_path / "reviews.jsonl"

    result = run_incremental(
        raw_directory=tmp_path / "raw",
        normalized_path=output,
        checkpoint_path=checkpoint_path,
        run_id="overlap-success",
        max_pages=5,
        page_fetcher=_sequence_fetcher(pages),
        fetched_at=FETCHED_AT,
    )

    assert result.complete is True
    assert result.completion_reason == "overlap_floor"
    assert result.pages_fetched == 2
    assert result.rows_observed == 4
    assert result.rows_written == 4
    assert len(output.read_text(encoding="utf-8").splitlines()) == 4
    assert (tmp_path / "raw/overlap-success/page-0002.json").exists()
    saved = load_updated_checkpoint(checkpoint_path)
    assert saved is not None
    assert saved.high_watermark_at == "2025-10-20T04:00:00Z"


def test_page_cap_keeps_raw_but_does_not_commit_incremental_batch(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    original_checkpoint = _checkpoint(checkpoint_path)
    output = tmp_path / "reviews.jsonl"

    result = run_incremental(
        raw_directory=tmp_path / "raw",
        normalized_path=output,
        checkpoint_path=checkpoint_path,
        run_id="capped-run",
        max_pages=1,
        page_fetcher=_sequence_fetcher(
            [_page(timestamps=[1760932800, 1760929200], cursor="page-2")]
        ),
        fetched_at=FETCHED_AT,
    )

    assert result.complete is False
    assert result.completion_reason == "page_cap"
    assert result.rows_written == 0
    assert not output.exists()
    assert checkpoint_path.read_bytes() == original_checkpoint
    assert (tmp_path / "raw/capped-run/page-0001.json").exists()
    metadata = json.loads(
        (tmp_path / "raw/capped-run/page-0001.metadata.json").read_text()
    )
    assert metadata["cursor_kind"] == "initial"
    assert metadata["cursor_sha256"] is None


def test_later_fetch_failure_leaves_checkpoint_unchanged(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    original_checkpoint = _checkpoint(checkpoint_path)
    output = tmp_path / "reviews.jsonl"
    calls = 0

    def fetch(_: str, __: int) -> FetchedReviewPage:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FetchedReviewPage(
                body=_page(timestamps=[1760932800, 1760929200], cursor="page-2")
            )
        raise OSError("synthetic page-two failure")

    with pytest.raises(OSError, match="page-two failure"):
        run_incremental(
            raw_directory=tmp_path / "raw",
            normalized_path=output,
            checkpoint_path=checkpoint_path,
            run_id="failed-run",
            max_pages=5,
            page_fetcher=fetch,
            fetched_at=FETCHED_AT,
        )

    assert not output.exists()
    assert checkpoint_path.read_bytes() == original_checkpoint
    assert (tmp_path / "raw/failed-run/page-0001.json").exists()
    assert not (tmp_path / "raw/failed-run/page-0002.json").exists()


def test_incremental_requires_explicit_bootstrap_checkpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bootstrap checkpoint"):
        run_incremental(
            raw_directory=tmp_path / "raw",
            normalized_path=tmp_path / "reviews.jsonl",
            checkpoint_path=tmp_path / "missing-checkpoint.json",
            run_id="no-bootstrap",
            page_fetcher=_sequence_fetcher([]),
            fetched_at=FETCHED_AT,
        )
