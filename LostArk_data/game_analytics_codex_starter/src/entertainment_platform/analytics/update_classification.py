"""Derive privacy-minimized, multi-label update classifications from app news.

The raw news excerpt is used only as classifier input.  It is not copied to the
derived event dimension.  Scores describe expected monitoring priority before
observing player/review outcomes; they are not measured update performance.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

RULE_VERSION = "keyword_rules_v1"

LABEL_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "live_service_transition": (
        "end its live service",
        "live service operations",
        "taking over",
        "take over",
    ),
    "content_class": ("new class", "class has arrived", "dimensionalist"),
    "content_raid_dungeon": (
        "raid",
        "abyssal dungeon",
        "new combat trial",
        "new chapter",
    ),
    "progression_economy": (
        "progression",
        "honing",
        "tier 3",
        "tier 4",
        "gear up",
        "reward track",
        "raid gold",
        "growth boost",
    ),
    "balance_combat": ("balance change", "balance adjustment", "difficulty"),
    "monetization_store": (
        "store",
        "shop item",
        "ark pass",
        "cosmetic",
        "avatar collection",
    ),
    "new_returning_experience": (
        "new and returning",
        "jump-up",
        "mokoko bootcamp",
        "catch-up",
    ),
    "technical_operations": (
        "downtime",
        "maintenance",
        "server merge",
        "region merge",
    ),
    "live_event": (" event", "festival", "competition", "twitch drop"),
    "community_communications": (
        "team update",
        "roadmap",
        "community",
        "creator",
    ),
}

PRIMARY_PRIORITY = (
    "live_service_transition",
    "content_class",
    "content_raid_dungeon",
    "progression_economy",
    "balance_combat",
    "technical_operations",
    "monetization_store",
    "new_returning_experience",
    "live_event",
    "community_communications",
)

SCOPE_PRIOR = {
    "live_service_transition": 100,
    "content_class": 85,
    "content_raid_dungeon": 75,
    "progression_economy": 80,
    "balance_combat": 70,
    "technical_operations": 60,
    "monetization_store": 55,
    "new_returning_experience": 65,
    "live_event": 35,
    "community_communications": 20,
}


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", without_tags).strip()


def classify_update_event(
    news_row: Mapping[str, Any], raw_item: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify one news item while retaining no article body in the result."""

    title = str(news_row["title"])
    content = _plain_text(str(raw_item.get("contents", "")))
    searchable = f" {title} {content} ".lower()
    labels = [
        label
        for label, patterns in LABEL_PATTERNS.items()
        if any(pattern in searchable for pattern in patterns)
    ]
    if not labels:
        labels = ["other_announcement"]
    primary_type = next(
        (label for label in PRIMARY_PRIORITY if label in labels), labels[0]
    )
    secondary_types = sorted(label for label in labels if label != primary_type)
    scope_score = min(
        100,
        max(SCOPE_PRIOR.get(label, 10) for label in labels)
        + max(0, len(labels) - 1) * 3,
    )
    technical_risk = max(
        95 if "live_service_transition" in labels else 0,
        75 if "technical_operations" in labels else 0,
        55 if "balance_combat" in labels else 0,
        45 if "content_raid_dungeon" in labels else 0,
    )
    monetization_risk = max(
        80 if "monetization_store" in labels else 0,
        45 if "progression_economy" in labels else 0,
    )
    matched_label_count = len(labels) - ("other_announcement" in labels)
    confidence = "medium_rule_based" if matched_label_count >= 2 else "low_rule_based"

    return {
        "appid": int(news_row["appid"]),
        "event_id": str(news_row["news_id"]),
        "event_title": title,
        "published_at": str(news_row["published_at"]),
        "effective_at": news_row.get("effective_at"),
        "evidence_url": str(news_row["url"]),
        "primary_type": primary_type,
        "secondary_types": secondary_types,
        "expected_scope_score": scope_score,
        "expected_technical_risk_score": technical_risk,
        "expected_monetization_risk_score": monetization_risk,
        "classification_method": RULE_VERSION,
        "classification_confidence": confidence,
        "human_verified": False,
        "score_interpretation": "rule-based monitoring prior; not observed impact",
    }


def classify_news_files(news_path: Path, raw_path: Path) -> list[dict[str, Any]]:
    """Join normalized event identity to local raw excerpts and classify rows."""

    news_rows = _read_jsonl(news_path)
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_items = raw_payload.get("appnews", {}).get("newsitems", [])
    if not isinstance(raw_items, list):
        raise TypeError("raw app news must contain appnews.newsitems")
    raw_by_id = {
        str(item.get("gid")): item for item in raw_items if isinstance(item, dict)
    }
    results: list[dict[str, Any]] = []
    for row in news_rows:
        news_id = str(row.get("news_id", ""))
        raw_item = raw_by_id.get(news_id)
        if raw_item is None:
            raise ValueError(f"raw news item is missing for news_id={news_id}")
        results.append(classify_update_event(row, raw_item))
    return sorted(results, key=lambda row: (row["published_at"], row["event_id"]))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"expected object at {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
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
    rows = classify_news_files(args.news, args.raw)
    write_jsonl_atomic(args.output, rows)
    type_counts: dict[str, int] = {}
    for row in rows:
        value = str(row["primary_type"])
        type_counts[value] = type_counts.get(value, 0) + 1
    print(
        json.dumps(
            {"rows_written": len(rows), "primary_type_counts": type_counts},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
