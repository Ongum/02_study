"""Dependency-free HTTP execution with bounded, testable retries."""

from __future__ import annotations

import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry limits for one HTTP page request.

    ``max_retries=3`` means at most four total attempts. Delay calculations are
    capped so a malformed or extreme Retry-After value cannot block a job
    indefinitely.
    """

    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.25

    def validate(self) -> None:
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be in [0, 5]")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")
        if self.max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be positive")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base delay must not exceed max delay")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    attempts: int
    retry_delays_seconds: tuple[float, ...]


class HttpFetchError(RuntimeError):
    """Safe error without response bodies, URLs, credentials, or user data."""

    def __init__(
        self,
        *,
        status: int | None,
        attempts: int,
        retryable: bool,
        error_kind: str,
        recommended_delay_seconds: float | None = None,
    ) -> None:
        super().__init__(
            f"HTTP fetch failed: kind={error_kind}, status={status}, attempts={attempts}"
        )
        self.status = status
        self.attempts = attempts
        self.retryable = retryable
        self.error_kind = error_kind
        self.recommended_delay_seconds = recommended_delay_seconds


class _RetryAfterExceedsLimit(ValueError):
    def __init__(self, delay_seconds: float) -> None:
        super().__init__("Retry-After exceeds this job's bounded wait")
        self.delay_seconds = delay_seconds


def fetch_bytes_with_retry(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
    policy: RetryPolicy,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    random_unit: Callable[[], float] = random.random,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> HttpResponse:
    """Fetch bytes, retrying only 429, 5xx, and transient transport failures."""

    policy.validate()
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be in (0, 60]")

    delays: list[float] = []
    max_attempts = policy.max_retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                    attempts=attempt,
                    retry_delays_seconds=tuple(delays),
                )
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == max_attempts:
                raise HttpFetchError(
                    status=exc.code,
                    attempts=attempt,
                    retryable=retryable,
                    error_kind="http_status",
                ) from exc
            try:
                delay = _retry_delay(
                    retry_number=attempt,
                    retry_after=(
                        exc.headers.get("Retry-After") if exc.headers else None
                    ),
                    policy=policy,
                    random_unit=random_unit,
                    now=now,
                )
            except _RetryAfterExceedsLimit as limit_error:
                # Do not retry earlier than the server requested. Ending this
                # bounded job lets a later scheduler run try again safely.
                raise HttpFetchError(
                    status=exc.code,
                    attempts=attempt,
                    retryable=True,
                    error_kind="retry_after_exceeds_limit",
                    recommended_delay_seconds=limit_error.delay_seconds,
                ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt == max_attempts:
                raise HttpFetchError(
                    status=None,
                    attempts=attempt,
                    retryable=True,
                    error_kind="transport",
                ) from exc
            delay = _retry_delay(
                retry_number=attempt,
                retry_after=None,
                policy=policy,
                random_unit=random_unit,
                now=now,
            )

        delays.append(delay)
        sleep(delay)

    raise AssertionError("retry loop exhausted without returning or raising")


def _retry_delay(
    *,
    retry_number: int,
    retry_after: str | None,
    policy: RetryPolicy,
    random_unit: Callable[[], float],
    now: Callable[[], datetime],
) -> float:
    parsed_retry_after = _parse_retry_after(retry_after, now=now)
    if parsed_retry_after is not None:
        if parsed_retry_after > policy.max_delay_seconds:
            raise _RetryAfterExceedsLimit(parsed_retry_after)
        return parsed_retry_after

    exponential = min(
        policy.base_delay_seconds * (2 ** (retry_number - 1)),
        policy.max_delay_seconds,
    )
    unit = random_unit()
    if not 0 <= unit <= 1:
        raise ValueError("random_unit must return a value in [0, 1]")
    jitter = exponential * policy.jitter_ratio * unit
    return min(exponential + jitter, policy.max_delay_seconds)


def _parse_retry_after(
    value: str | None, *, now: Callable[[], datetime]
) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now() must return a timezone-aware datetime")
    return max(
        0.0, (retry_at.astimezone(UTC) - current.astimezone(UTC)).total_seconds()
    )
