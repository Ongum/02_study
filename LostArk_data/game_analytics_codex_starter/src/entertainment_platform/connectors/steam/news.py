"""Validation and privacy-minimized normalization for Steam app news."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

STEAM_NEWS_ENDPOINT = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"


class SteamNewsValidationError(ValueError):
    """Raised when the anonymous app-news response violates its contract."""


@dataclass(frozen=True, slots=True)
class NormalizedSteamNews:
    appid: int
    news_id: str
    published_at: str
    title: str
    url: str
    feed_name: str
    feed_label: str
    is_external_url: bool
    candidate_event_type: str
    effective_at: None
    event_confidence: str
    fetched_at: str
    run_id: str
    source_endpoint: str
    payload_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParsedSteamNewsPage:
    items: tuple[NormalizedSteamNews, ...]


def parse_news_page(
    payload: object,
    *,
    appid: int,
    fetched_at: datetime,
    run_id: str,
) -> ParsedSteamNewsPage:
    if not isinstance(payload, dict):
        raise SteamNewsValidationError("news response must be an object")
    appnews = payload.get("appnews")
    if not isinstance(appnews, dict):
        raise SteamNewsValidationError("missing appnews envelope")
    response_appid = appnews.get("appid")
    if response_appid != appid:
        raise SteamNewsValidationError("news response appid does not match")
    newsitems = appnews.get("newsitems")
    if not isinstance(newsitems, list):
        raise SteamNewsValidationError("newsitems must be a list")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise SteamNewsValidationError("fetched_at must be timezone-aware")
    if not run_id.strip():
        raise SteamNewsValidationError("run_id must not be empty")

    items = tuple(
        _normalize_item(
            item,
            index=index,
            appid=appid,
            fetched_at=fetched_at,
            run_id=run_id,
        )
        for index, item in enumerate(newsitems)
    )
    return ParsedSteamNewsPage(items=items)


def _normalize_item(
    item: object,
    *,
    index: int,
    appid: int,
    fetched_at: datetime,
    run_id: str,
) -> NormalizedSteamNews:
    if not isinstance(item, dict):
        raise SteamNewsValidationError(f"newsitems[{index}] must be an object")
    news_id = item.get("gid")
    if not isinstance(news_id, str) or not news_id:
        raise SteamNewsValidationError(f"newsitems[{index}].gid must be a string")
    title = item.get("title")
    url = item.get("url")
    feed_name = item.get("feedname")
    feed_label = item.get("feedlabel")
    is_external = item.get("is_external_url")
    published_epoch = item.get("date")
    if not isinstance(title, str) or not isinstance(url, str):
        raise SteamNewsValidationError(f"newsitems[{index}] title/url is invalid")
    if not isinstance(feed_name, str) or not isinstance(feed_label, str):
        raise SteamNewsValidationError(f"newsitems[{index}] feed fields are invalid")
    if not isinstance(is_external, bool):
        raise SteamNewsValidationError(
            f"newsitems[{index}].is_external_url must be boolean"
        )
    if (
        not isinstance(published_epoch, int)
        or isinstance(published_epoch, bool)
        or published_epoch < 0
    ):
        raise SteamNewsValidationError(f"newsitems[{index}].date is invalid")
    try:
        published_at = datetime.fromtimestamp(published_epoch, tz=UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise SteamNewsValidationError(
            f"newsitems[{index}].date is out of range"
        ) from exc

    canonical = json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return NormalizedSteamNews(
        appid=appid,
        news_id=news_id,
        published_at=_utc_iso(published_at),
        title=title,
        url=url,
        feed_name=feed_name,
        feed_label=feed_label,
        is_external_url=is_external,
        candidate_event_type=_candidate_type(title),
        effective_at=None,
        event_confidence="candidate_title_only",
        fetched_at=_utc_iso(fetched_at),
        run_id=run_id,
        source_endpoint=STEAM_NEWS_ENDPOINT,
        payload_hash=hashlib.sha256(canonical).hexdigest(),
    )


def _candidate_type(title: str) -> str:
    lowered = title.lower()
    if re.search(r"\b(patch|update|release notes|hotfix)\b", lowered):
        return "patch_or_update_candidate"
    if re.search(r"\bmaintenance\b", lowered):
        return "maintenance_candidate"
    if re.search(r"\bevent\b", lowered):
        return "event_announcement"
    return "other_news"


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
