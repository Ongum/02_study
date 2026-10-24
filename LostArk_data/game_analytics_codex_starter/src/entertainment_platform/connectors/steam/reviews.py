"""Validation and normalization for Steam review response pages.

This module performs no network requests. The response contract is based on
the official Steamworks documentation and must be checked against a small,
recorded smoke response before it is treated as production-verified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

STEAM_REVIEWS_ENDPOINT = (
    "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
)


class SteamReviewValidationError(ValueError):
    """Raised when a response cannot satisfy the normalized review contract."""


@dataclass(frozen=True, slots=True)
class NormalizedSteamReview:
    """Privacy-minimized review row; author identifiers are intentionally absent."""

    appid: int
    recommendationid: str
    language: str
    review_text: str
    created_at: str
    updated_at: str
    voted_up: bool
    steam_purchase: bool
    received_for_free: bool
    written_during_early_access: bool
    refunded: bool
    votes_up: int
    votes_funny: int
    weighted_vote_score: float
    comment_count: int
    fetched_at: str
    run_id: str
    source_endpoint: str
    page_fingerprint: str
    payload_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParsedSteamReviewPage:
    reviews: tuple[NormalizedSteamReview, ...]
    cursor: str
    total_matching: int
    page_fingerprint: str


def parse_review_page(
    payload: object,
    *,
    appid: int,
    fetched_at: datetime,
    run_id: str,
) -> ParsedSteamReviewPage:
    """Validate one API response page and return normalized, identifier-safe rows."""

    if not isinstance(payload, dict):
        raise SteamReviewValidationError("response must be a JSON object")
    if not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0:
        raise SteamReviewValidationError("appid must be a positive integer")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise SteamReviewValidationError("fetched_at must be timezone-aware")
    if not run_id.strip():
        raise SteamReviewValidationError("run_id must not be empty")

    response = _required(payload, "response", dict)
    query_summary = _required(response, "query_summary", dict)
    if "reviews" in response:
        reviews = _required(response, "reviews", list)
    elif _required_uint(query_summary, "num_reviews") == 0:
        # The live API may omit `reviews` on a terminal page instead of
        # returning an empty list. Only accept that omission when Steam's own
        # page count confirms there are zero reviews.
        reviews = []
    else:
        raise SteamReviewValidationError(
            "missing required field: reviews for non-empty page"
        )
    cursor = _required(response, "cursor", str)
    total_matching = _required_uint(response, "total_matching")
    page_fingerprint = _sha256_json(payload)
    fetched_at_iso = _utc_iso(fetched_at)

    normalized = tuple(
        _normalize_review(
            item,
            index=index,
            appid=appid,
            fetched_at=fetched_at_iso,
            run_id=run_id,
            page_fingerprint=page_fingerprint,
        )
        for index, item in enumerate(reviews)
    )
    return ParsedSteamReviewPage(
        reviews=normalized,
        cursor=cursor,
        total_matching=total_matching,
        page_fingerprint=page_fingerprint,
    )


def _normalize_review(
    item: object,
    *,
    index: int,
    appid: int,
    fetched_at: str,
    run_id: str,
    page_fingerprint: str,
) -> NormalizedSteamReview:
    if not isinstance(item, dict):
        raise SteamReviewValidationError(f"reviews[{index}] must be an object")

    recommendationid = _required(item, "recommendationid", (str, int))
    if isinstance(recommendationid, bool) or not str(recommendationid).isdigit():
        raise SteamReviewValidationError(
            f"reviews[{index}].recommendationid must be an unsigned integer string"
        )

    weighted_vote_score = _required(item, "weighted_vote_score", (int, float))
    if isinstance(weighted_vote_score, bool):
        raise SteamReviewValidationError(
            f"reviews[{index}].weighted_vote_score must be numeric"
        )

    return NormalizedSteamReview(
        appid=appid,
        recommendationid=str(recommendationid),
        language=_required(item, "language", str),
        review_text=_required(item, "review", str),
        created_at=_unix_seconds_to_iso(
            _required_uint(item, "timestamp_created"), index, "timestamp_created"
        ),
        updated_at=_unix_seconds_to_iso(
            _required_uint(item, "timestamp_updated"), index, "timestamp_updated"
        ),
        voted_up=_required_bool(item, "voted_up"),
        steam_purchase=_required_bool(item, "steam_purchase"),
        received_for_free=_required_bool(item, "received_for_free"),
        written_during_early_access=_required_bool(item, "written_during_early_access"),
        refunded=_required_bool(item, "refunded"),
        votes_up=_required_uint(item, "votes_up"),
        votes_funny=_required_uint(item, "votes_funny"),
        weighted_vote_score=float(weighted_vote_score),
        comment_count=_required_uint(item, "comment_count"),
        fetched_at=fetched_at,
        run_id=run_id,
        source_endpoint=STEAM_REVIEWS_ENDPOINT,
        page_fingerprint=page_fingerprint,
        payload_hash=_sha256_json(item),
    )


def _required(
    value: dict[str, Any], key: str, expected: type | tuple[type, ...]
) -> Any:
    if key not in value:
        raise SteamReviewValidationError(f"missing required field: {key}")
    result = value[key]
    if not isinstance(result, expected):
        raise SteamReviewValidationError(f"field {key} has invalid type")
    return result


def _required_bool(value: dict[str, Any], key: str) -> bool:
    result = _required(value, key, bool)
    return result


def _required_uint(value: dict[str, Any], key: str) -> int:
    result = _required(value, key, int)
    if isinstance(result, bool) or result < 0:
        raise SteamReviewValidationError(f"field {key} must be an unsigned integer")
    return result


def _unix_seconds_to_iso(value: int, index: int, field: str) -> str:
    try:
        return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise SteamReviewValidationError(
            f"reviews[{index}].{field} is outside the supported timestamp range"
        ) from exc


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
