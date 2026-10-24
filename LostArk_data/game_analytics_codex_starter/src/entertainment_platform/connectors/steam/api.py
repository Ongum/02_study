"""Small request-building helpers for Steam's official review API."""

from __future__ import annotations

import json
import urllib.parse

from entertainment_platform.connectors.steam.reviews import STEAM_REVIEWS_ENDPOINT


def build_review_request_url(
    *,
    appid: int,
    cursor: str = "*",
    num_per_page: int = 1,
    filter_value: int = 1,
    date_range_start: int | None = None,
    date_range_end: int | None = None,
) -> tuple[str, dict[str, object]]:
    """Build an anonymous ``input_json`` review request.

    Keeping this in the connector layer lets diagnostic and production-shaped
    jobs share the same parameter validation without depending on each other.
    Date boundaries are Unix seconds and must be supplied as a pair.
    """

    if appid <= 0:
        raise ValueError("appid must be positive")
    if not cursor:
        raise ValueError("cursor must not be empty")
    if not 1 <= num_per_page <= 100:
        raise ValueError("num_per_page must be in [1, 100]")
    if filter_value not in {0, 1, 2, 3}:
        raise ValueError("filter_value must be a documented Steam review filter")
    if (date_range_start is None) != (date_range_end is None):
        raise ValueError(
            "date_range_start and date_range_end must be supplied together"
        )
    if date_range_start is not None and date_range_end is not None:
        if date_range_start < 0 or date_range_end < 0:
            raise ValueError("date range Unix timestamps must be non-negative")
        if date_range_start > date_range_end:
            raise ValueError("date_range_start must not exceed date_range_end")

    parameters: dict[str, object] = {
        "appid": appid,
        "filter": filter_value,
        "languages": ["all"],
        "review_type": 0,
        "purchase_type": 1,
        "num_per_page": num_per_page,
        "cursor": cursor,
    }
    if date_range_start is not None and date_range_end is not None:
        parameters["date_range_start"] = date_range_start
        parameters["date_range_end"] = date_range_end
    encoded = urllib.parse.urlencode(
        {
            "input_json": json.dumps(
                parameters, ensure_ascii=False, separators=(",", ":")
            )
        }
    )
    return f"{STEAM_REVIEWS_ENDPOINT}?{encoded}", parameters
