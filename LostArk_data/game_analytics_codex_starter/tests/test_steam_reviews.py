from __future__ import annotations

import json
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

from entertainment_platform.common.http import (
    HttpFetchError,
    RetryPolicy,
    fetch_bytes_with_retry,
)
from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.checkpoint import load_updated_checkpoint
from entertainment_platform.connectors.steam.reviews import (
    SteamReviewValidationError,
    parse_review_page,
)
from entertainment_platform.jobs.offline_steam_reviews import DEDUPLICATION_KEY, run
from entertainment_platform.jobs.process_updated_batch import process_updated_payloads
from entertainment_platform.jobs.steam_reviews_date_range_check import (
    _validate_arguments,
)
from entertainment_platform.jobs.steam_reviews_smoke import build_review_request_url
from entertainment_platform.jobs.steam_reviews_updated_check import (
    _validate_limits as validate_updated_limits,
)

FIXTURE = Path(__file__).parent / "fixtures" / "steam_reviews_page.synthetic.json"
FETCHED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self.headers = Message()
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def sequence_opener(*results: object):
    pending = iter(results)

    def open_next(_: Request, *, timeout: float) -> FakeResponse:
        assert timeout > 0
        result = next(pending)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, FakeResponse)
        return result

    return open_next


def http_error(status: int, *, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.invalid", status, "synthetic", headers, None)


def test_http_retry_honors_retry_after_without_real_sleep() -> None:
    sleeps: list[float] = []

    response = fetch_bytes_with_retry(
        Request("https://example.invalid"),
        timeout_seconds=5,
        policy=RetryPolicy(max_retries=3, jitter_ratio=0),
        opener=sequence_opener(
            http_error(429, retry_after="3"), FakeResponse(body=b"ok")
        ),
        sleep=sleeps.append,
        random_unit=lambda: 0,
    )

    assert response.body == b"ok"
    assert response.attempts == 2
    assert response.retry_delays_seconds == (3.0,)
    assert sleeps == [3.0]


def test_http_retry_uses_exponential_backoff_for_5xx() -> None:
    sleeps: list[float] = []

    response = fetch_bytes_with_retry(
        Request("https://example.invalid"),
        timeout_seconds=5,
        policy=RetryPolicy(max_retries=3, jitter_ratio=0),
        opener=sequence_opener(
            http_error(500), http_error(503), FakeResponse(body=b"ok")
        ),
        sleep=sleeps.append,
        random_unit=lambda: 0,
    )

    assert response.attempts == 3
    assert response.retry_delays_seconds == (1.0, 2.0)
    assert sleeps == [1.0, 2.0]


def test_http_retry_supports_http_date_retry_after() -> None:
    sleeps: list[float] = []

    response = fetch_bytes_with_retry(
        Request("https://example.invalid"),
        timeout_seconds=5,
        policy=RetryPolicy(max_retries=1, max_delay_seconds=10, jitter_ratio=0),
        opener=sequence_opener(
            http_error(429, retry_after="Wed, 21 Oct 2026 12:00:05 GMT"),
            FakeResponse(),
        ),
        sleep=sleeps.append,
        random_unit=lambda: 0,
        now=lambda: datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
    )

    assert response.attempts == 2
    assert sleeps == [5.0]


def test_retry_after_beyond_wait_cap_stops_without_retrying_early() -> None:
    sleeps: list[float] = []

    with pytest.raises(HttpFetchError) as captured:
        fetch_bytes_with_retry(
            Request("https://example.invalid"),
            timeout_seconds=5,
            policy=RetryPolicy(max_retries=3, max_delay_seconds=30),
            opener=sequence_opener(http_error(429, retry_after="120")),
            sleep=sleeps.append,
        )

    assert captured.value.attempts == 1
    assert captured.value.error_kind == "retry_after_exceeds_limit"
    assert captured.value.recommended_delay_seconds == 120
    assert sleeps == []


def test_http_does_not_retry_non_transient_4xx() -> None:
    sleeps: list[float] = []

    with pytest.raises(HttpFetchError) as captured:
        fetch_bytes_with_retry(
            Request("https://example.invalid"),
            timeout_seconds=5,
            policy=RetryPolicy(max_retries=3),
            opener=sequence_opener(http_error(400)),
            sleep=sleeps.append,
        )

    assert captured.value.status == 400
    assert captured.value.attempts == 1
    assert captured.value.retryable is False
    assert sleeps == []


def test_transport_failure_stops_after_bounded_attempts() -> None:
    sleeps: list[float] = []

    with pytest.raises(HttpFetchError) as captured:
        fetch_bytes_with_retry(
            Request("https://example.invalid"),
            timeout_seconds=5,
            policy=RetryPolicy(max_retries=2, jitter_ratio=0),
            opener=sequence_opener(
                URLError("synthetic"), URLError("synthetic"), URLError("synthetic")
            ),
            sleep=sleeps.append,
            random_unit=lambda: 0,
        )

    assert captured.value.status is None
    assert captured.value.attempts == 3
    assert captured.value.retryable is True
    assert sleeps == [1.0, 2.0]


def test_parse_review_page_normalizes_without_author_identifier() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    page = parse_review_page(
        payload, appid=1599340, fetched_at=FETCHED_AT, run_id="offline-test"
    )

    assert page.total_matching == 2
    assert page.cursor == "synthetic-next-cursor"
    assert page.reviews[0].created_at == "2025-10-20T00:00:00Z"
    assert page.reviews[0].voted_up is False
    assert "author" not in page.reviews[0].to_dict()
    assert "steamid" not in page.reviews[0].to_dict()


def test_parse_review_page_rejects_invalid_boolean() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["response"]["reviews"][0]["voted_up"] = 0

    with pytest.raises(SteamReviewValidationError, match="voted_up"):
        parse_review_page(
            payload, appid=1599340, fetched_at=FETCHED_AT, run_id="offline-test"
        )


def test_parse_review_page_accepts_observed_terminal_page_without_reviews() -> None:
    payload = {
        "response": {
            "query_summary": {"num_reviews": 0},
            "cursor": "terminal-cursor",
            "total_matching": 4,
        }
    }

    page = parse_review_page(
        payload, appid=1599340, fetched_at=FETCHED_AT, run_id="terminal-page-test"
    )

    assert page.reviews == ()
    assert page.total_matching == 4


def test_offline_run_is_idempotent(tmp_path: Path) -> None:
    output = tmp_path / "normalized_reviews.jsonl"

    first_written = run(
        input_path=FIXTURE,
        output_path=output,
        appid=1599340,
        fetched_at=FETCHED_AT,
        run_id="offline-test-first",
    )
    second_written = run(
        input_path=FIXTURE,
        output_path=output,
        appid=1599340,
        fetched_at=FETCHED_AT,
        run_id="offline-test-retry",
    )

    rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert first_written == 2
    assert second_written == 0
    assert len(rows) == 2
    assert {row["recommendationid"] for row in rows} == {
        "90000000000000001",
        "90000000000000002",
    }


def test_smoke_url_uses_input_json_without_api_key() -> None:
    url, parameters = build_review_request_url(appid=1599340)
    query = parse_qs(urlparse(url).query)

    assert "key" not in query
    assert set(query) == {"input_json"}
    assert json.loads(query["input_json"][0]) == parameters
    assert parameters["num_per_page"] == 1
    assert parameters["languages"] == ["all"]


def test_smoke_url_urlencodes_returned_cursor() -> None:
    cursor = "synthetic cursor+/="

    url, parameters = build_review_request_url(appid=1599340, cursor=cursor)
    query = parse_qs(urlparse(url).query)

    assert json.loads(query["input_json"][0])["cursor"] == cursor
    assert parameters["cursor"] == cursor


def test_review_url_includes_both_date_boundaries() -> None:
    url, parameters = build_review_request_url(
        appid=1599340,
        num_per_page=100,
        date_range_start=1760918400,
        date_range_end=1761004799,
    )
    query = parse_qs(urlparse(url).query)

    assert json.loads(query["input_json"][0]) == parameters
    assert parameters["date_range_start"] == 1760918400
    assert parameters["date_range_end"] == 1761004799
    assert parameters["num_per_page"] == 100


def test_date_range_check_rejects_more_than_five_pages() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        _validate_arguments(
            range_start=datetime(2026, 10, 20, tzinfo=UTC),
            range_end=datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC),
            max_pages=6,
            page_size=100,
        )


def test_review_url_supports_documented_updated_filter() -> None:
    url, parameters = build_review_request_url(
        appid=1599340, filter_value=2, num_per_page=5
    )
    query = parse_qs(urlparse(url).query)

    assert json.loads(query["input_json"][0]) == parameters
    assert parameters["filter"] == 2
    assert parameters["num_per_page"] == 5


def test_updated_check_rejects_unbounded_page_count() -> None:
    with pytest.raises(ValueError, match="max_pages"):
        validate_updated_limits(max_pages=4, page_size=5)


def test_invalid_later_page_does_not_write_or_checkpoint(tmp_path: Path) -> None:
    valid = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalid = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del invalid["response"]["reviews"][0]["language"]
    output = tmp_path / "reviews.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    with pytest.raises(SteamReviewValidationError, match="language"):
        process_updated_payloads(
            [valid, invalid],
            output_path=output,
            checkpoint_path=checkpoint,
            appid=1599340,
            fetched_at=FETCHED_AT,
            run_id="invalid-batch",
            overlap_seconds=3600,
        )

    assert not output.exists()
    assert not checkpoint.exists()


def test_partial_write_failure_is_recoverable_without_checkpoint(
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    output = tmp_path / "reviews.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    def partial_writer(path: Path, rows: list[dict[str, object]]) -> int:
        append_unique_jsonl(path, rows[:1], key_fields=DEDUPLICATION_KEY)
        raise OSError("synthetic write failure")

    with pytest.raises(OSError, match="synthetic write failure"):
        process_updated_payloads(
            [payload],
            output_path=output,
            checkpoint_path=checkpoint,
            appid=1599340,
            fetched_at=FETCHED_AT,
            run_id="partial-write",
            overlap_seconds=3600,
            row_writer=partial_writer,
        )

    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
    assert not checkpoint.exists()

    recovered = process_updated_payloads(
        [payload],
        output_path=output,
        checkpoint_path=checkpoint,
        appid=1599340,
        fetched_at=FETCHED_AT,
        run_id="recovery-run",
        overlap_seconds=3600,
    )
    assert recovered.rows_written == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    assert checkpoint.exists()


def test_safe_rerun_deduplicates_and_preserves_high_watermark(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    output = tmp_path / "reviews.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    first = process_updated_payloads(
        [payload],
        output_path=output,
        checkpoint_path=checkpoint,
        appid=1599340,
        fetched_at=FETCHED_AT,
        run_id="first-run",
        overlap_seconds=3600,
    )
    second = process_updated_payloads(
        [payload],
        output_path=output,
        checkpoint_path=checkpoint,
        appid=1599340,
        fetched_at=FETCHED_AT,
        run_id="safe-rerun",
        overlap_seconds=3600,
    )

    assert first.rows_written == 2
    assert first.checkpoint_advanced is True
    assert second.rows_written == 0
    assert second.checkpoint_advanced is False
    assert second.high_watermark_at == first.high_watermark_at
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    saved = load_updated_checkpoint(checkpoint)
    assert saved is not None
    assert saved.scan_floor.isoformat() == "2025-10-20T01:00:00+00:00"
