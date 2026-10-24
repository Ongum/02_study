"""Draft review-impact mart around unvalidated Steam news candidates.

Publication time is used only as an explicit proxy while ``effective_at`` is
unknown. Rows are descriptive readiness evidence, never a claim that a patch
was deployed at publication time or caused the observed review change.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def build_update_impact_draft(
    *,
    current_reviews_path: Path,
    news_path: Path,
    output_path: Path,
    window_days: int = 14,
) -> list[dict[str, object]]:
    if not 1 <= window_days <= 30:
        raise ValueError("window_days must be in [1, 30]")
    reviews = _read_jsonl(current_reviews_path)
    news = _read_jsonl(news_path)
    if not reviews:
        raise ValueError("current reviews are empty")
    review_start = min(_parse_utc(str(row["created_at"])) for row in reviews)
    review_end = max(_parse_utc(str(row["created_at"])) for row in reviews)
    window = timedelta(days=window_days)
    output: list[dict[str, object]] = []

    for event in news:
        if event.get("candidate_event_type") != "patch_or_update_candidate":
            continue
        effective_at = event.get("effective_at")
        reference = _parse_utc(
            str(effective_at if effective_at is not None else event["published_at"])
        )
        reference_type = (
            "validated_effective_at"
            if effective_at is not None
            else "publication_proxy_unvalidated"
        )
        pre = [
            row
            for row in reviews
            if reference - window <= _parse_utc(str(row["created_at"])) < reference
        ]
        post = [
            row
            for row in reviews
            if reference <= _parse_utc(str(row["created_at"])) < reference + window
        ]
        pre_negative = sum(not bool(row["voted_up"]) for row in pre)
        post_negative = sum(not bool(row["voted_up"]) for row in post)
        pre_rate = pre_negative / len(pre) if pre else None
        post_rate = post_negative / len(post) if post else None
        delta_pp = (
            (post_rate - pre_rate) * 100
            if pre_rate is not None and post_rate is not None
            else None
        )
        half_width = (
            _difference_half_width(len(pre), len(post), pre_rate, post_rate)
            if pre_rate is not None and post_rate is not None
            else None
        )
        pre_complete = reference - window >= review_start
        post_complete = reference + window <= review_end + timedelta(seconds=1)
        low_sample = len(pre) < 30 or len(post) < 30
        quality_flags = []
        if not pre_complete:
            quality_flags.append("pre_window_censored")
        if not post_complete:
            quality_flags.append("post_window_censored")
        if low_sample:
            quality_flags.append("low_sample")
        if effective_at is None:
            quality_flags.append("effective_time_unvalidated")
        output.append(
            {
                "appid": event["appid"],
                "event_id": event["news_id"],
                "event_title": event["title"],
                "published_at": event["published_at"],
                "effective_at": effective_at,
                "reference_at": reference.isoformat().replace("+00:00", "Z"),
                "reference_type": reference_type,
                "window_days_each_side": window_days,
                "pre_total": len(pre),
                "pre_negative_count": pre_negative,
                "pre_negative_rate": pre_rate,
                "post_total": len(post),
                "post_negative_count": post_negative,
                "post_negative_rate": post_rate,
                "negative_rate_delta_pp": delta_pp,
                "approx_delta_ci95_lower_pp": (
                    delta_pp - half_width * 100
                    if delta_pp is not None and half_width is not None
                    else None
                ),
                "approx_delta_ci95_upper_pp": (
                    delta_pp + half_width * 100
                    if delta_pp is not None and half_width is not None
                    else None
                ),
                "pre_window_complete": pre_complete,
                "post_window_complete": post_complete,
                "quality_flags": quality_flags,
                "causal_claim_allowed": False,
                "interpretation_status": (
                    "descriptive_only"
                    if not quality_flags
                    else "limited_by_quality_flags"
                ),
            }
        )
    output.sort(key=lambda row: str(row["reference_at"]))
    _write_jsonl_atomic(output_path, output)
    return output


def _difference_half_width(
    first_n: int, second_n: int, first_rate: float, second_rate: float
) -> float:
    if first_n <= 0 or second_n <= 0:
        return 1.0
    variance = first_rate * (1 - first_rate) / first_n
    variance += second_rate * (1 - second_rate) / second_n
    return 1.96 * math.sqrt(variance)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
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
    encoded = b"".join(
        (
            json.dumps(
                dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode()
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a draft update-impact mart.")
    parser.add_argument(
        "--reviews",
        type=Path,
        default=Path("output/silver_reviews_current_180d.jsonl"),
    )
    parser.add_argument(
        "--news", type=Path, default=Path("output/silver_app_news_180d.jsonl")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output/mart_update_impact_draft.jsonl")
    )
    parser.add_argument("--window-days", type=int, default=14)
    args = parser.parse_args(argv)
    rows = build_update_impact_draft(
        current_reviews_path=args.reviews,
        news_path=args.news,
        output_path=args.output,
        window_days=args.window_days,
    )
    safe = {
        "candidate_events": len(rows),
        "complete_windows": sum(
            bool(row["pre_window_complete"]) and bool(row["post_window_complete"])
            for row in rows
        ),
        "low_sample_events": sum("low_sample" in row["quality_flags"] for row in rows),
        "validated_effective_times": sum(
            row["effective_at"] is not None for row in rows
        ),
    }
    print(json.dumps(safe, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
