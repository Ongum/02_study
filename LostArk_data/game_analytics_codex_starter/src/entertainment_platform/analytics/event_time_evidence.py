"""Extract announced event-time evidence without inventing effective times.

An announced maintenance start, an event broadcast start, and a confirmed
deployment completion are different facts.  This module preserves that
distinction and always leaves ``effective_at`` null until a stronger source is
provided by a separate verification workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PARSER_VERSION = "event_time_rules_v1"
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
MONTH_PATTERN = "|".join(MONTHS)
MAINTENANCE_PATTERN = re.compile(
    rf"Downtime will begin on (?P<month>{MONTH_PATTERN}) (?P<day>\d{{1,2}}) "
    r"at (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)?(?:\s*(?P<zone>PT|UTC))?"
    r"(?:\s*\((?P<utc_hour>\d{1,2})(?::(?P<utc_minute>\d{2}))?\s*"
    r"(?P<utc_ampm>AM|PM)\s*UTC\))?",
    re.IGNORECASE,
)
EVENT_START_PATTERN = re.compile(
    rf"Event starts (?P<month>{MONTH_PATTERN}) (?P<day>\d{{1,2}}),\s*"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\s*(?P<zone>PT|UTC)",
    re.IGNORECASE,
)


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def extract_event_time_evidence(
    news_row: Mapping[str, Any], raw_item: Mapping[str, Any]
) -> dict[str, Any]:
    """Return one strongest announced-time fact for a normalized news row."""

    content = _plain_text(str(raw_item.get("contents", "")))
    year = datetime.fromisoformat(str(news_row["published_at"])).year
    evidence_type = "none_found"
    announced_at_utc: str | None = None
    timezone_basis: str | None = None
    matched_text: str | None = None

    maintenance = MAINTENANCE_PATTERN.search(content)
    event_start = EVENT_START_PATTERN.search(content)
    if maintenance:
        evidence_type = "announced_maintenance_start"
        announced_at_utc, timezone_basis = _match_to_utc(maintenance, year)
        matched_text = maintenance.group(0)
    elif event_start:
        evidence_type = "announced_event_start"
        announced_at_utc, timezone_basis = _match_to_utc(event_start, year)
        matched_text = event_start.group(0)
    elif "end its live service operations" in content.lower():
        evidence_type = "announced_future_service_transition_period"
        matched_text = "early 2027" if "early 2027" in content.lower() else None
    elif re.search(r"\bmaintenance on \d{1,2}/\d{1,2}\b", content, re.IGNORECASE):
        evidence_type = "announced_maintenance_date_unparsed"
        matched_text = re.search(
            r"maintenance on \d{1,2}/\d{1,2}", content, re.IGNORECASE
        ).group(0)

    evidence_hash = (
        hashlib.sha256(matched_text.encode()).hexdigest() if matched_text else None
    )
    return {
        "appid": int(news_row["appid"]),
        "event_id": str(news_row["news_id"]),
        "event_title": str(news_row["title"]),
        "source_published_at": str(news_row["published_at"]),
        "source_url": str(news_row["url"]),
        "evidence_type": evidence_type,
        "announced_at_utc": announced_at_utc,
        "effective_at": None,
        "timezone_basis": timezone_basis,
        "evidence_text_sha256": evidence_hash,
        "evidence_status": (
            "announced_not_observed" if evidence_type != "none_found" else "missing"
        ),
        "promotion_allowed": False,
        "parser_version": PARSER_VERSION,
    }


def _match_to_utc(match: re.Match[str], year: int) -> tuple[str | None, str | None]:
    groups = match.groupdict()
    month_name = groups["month"].capitalize()
    day = int(groups["day"])
    minute = int(groups.get("minute") or 0)
    if groups.get("utc_hour"):
        hour = _hour24(int(groups["utc_hour"]), str(groups["utc_ampm"]))
        minute = int(groups.get("utc_minute") or 0)
        value = datetime(year, MONTHS[month_name], day, hour, minute, tzinfo=UTC)
        return value.isoformat().replace("+00:00", "Z"), "explicit_utc_parenthetical"

    zone_name = (groups.get("zone") or "").upper()
    ampm = groups.get("ampm")
    if not zone_name or not ampm:
        return None, "insufficient_time_zone_evidence"
    hour = _hour24(int(groups["hour"]), str(ampm))
    local_value = datetime(year, MONTHS[month_name], day, hour, minute)
    if zone_name == "UTC":
        source_zone = UTC
        timezone_basis = "explicit_utc"
    else:
        source_zone = timezone(timedelta(hours=_pacific_utc_offset(local_value)))
        timezone_basis = "us_pacific_dst_rule_2007_plus"
    value = local_value.replace(tzinfo=source_zone)
    return (
        value.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        timezone_basis,
    )


def _hour24(hour: int, ampm: str) -> int:
    normalized = ampm.lower().replace(".", "")
    if not 1 <= hour <= 12 or normalized not in {"am", "pm"}:
        raise ValueError("invalid 12-hour time")
    return hour % 12 + (12 if normalized == "pm" else 0)


def _pacific_utc_offset(local_value: datetime) -> int:
    """Return the US Pacific UTC offset using the 2007+ federal DST rule."""

    if local_value.year < 2007:
        raise ValueError("built-in Pacific DST rule supports year 2007 and later")
    dst_start = _first_weekday_on_or_after(
        datetime(local_value.year, 3, 8, 2), weekday=6
    )
    dst_end = _first_weekday_on_or_after(
        datetime(local_value.year, 11, 1, 2), weekday=6
    )
    return -7 if dst_start <= local_value < dst_end else -8


def _first_weekday_on_or_after(value: datetime, *, weekday: int) -> datetime:
    return value + timedelta(days=(weekday - value.weekday()) % 7)


def build_event_time_evidence(news_path: Path, raw_path: Path) -> list[dict[str, Any]]:
    news_rows = _read_jsonl(news_path)
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_items = raw_payload.get("appnews", {}).get("newsitems", [])
    if not isinstance(raw_items, list):
        raise TypeError("raw app news must contain appnews.newsitems")
    raw_by_id = {
        str(item.get("gid")): item for item in raw_items if isinstance(item, dict)
    }
    output = []
    for row in news_rows:
        news_id = str(row["news_id"])
        raw_item = raw_by_id.get(news_id)
        if raw_item is None:
            raise ValueError(f"raw news item is missing for news_id={news_id}")
        output.append(extract_event_time_evidence(row, raw_item))
    return sorted(output, key=lambda row: (row["source_published_at"], row["event_id"]))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def _write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_event_time_evidence(args.news, args.raw)
    _write_jsonl_atomic(args.output, rows)
    print(
        json.dumps(
            {
                "rows_written": len(rows),
                "announced_times_parsed": sum(
                    row["announced_at_utc"] is not None for row in rows
                ),
                "effective_times_promoted": sum(
                    row["effective_at"] is not None for row in rows
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
