"""Build local review quality metrics and Gold-like aggregate prototypes.

The module never calls Steam. It selects the latest observed version of each
review, creates aggregate-only marts, and reports uncertainty/coverage limits.
Raw review text remains only in the ignored local Silver history/current files;
aggregate outputs contain no review text or author identifiers.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "appid",
    "recommendationid",
    "language",
    "review_text",
    "created_at",
    "updated_at",
    "voted_up",
    "fetched_at",
    "run_id",
    "payload_hash",
)

# Transparent baseline taxonomy. A match means a keyword was observed, not that
# a human-validated complaint label is correct. English is the first supported
# analysis language per the confirmed project policy.
ENGLISH_ISSUE_PATTERNS: dict[str, tuple[str, ...]] = {
    "server_or_performance": (
        r"\bserver(?:s)?\b",
        r"\blag(?:gy)?\b",
        r"\bdisconnect(?:ed|ing|s)?\b",
        r"\bcrash(?:ed|es|ing)?\b",
        r"\bperformance\b",
        r"\bping\b",
    ),
    "monetization_or_grind": (
        r"pay[ -]?to[ -]?win",
        r"\bp2w\b",
        r"\bmonetization\b",
        r"\bgrind(?:y|ing)?\b",
        r"\bhomework\b",
        r"\bcasino\b",
    ),
    "bots_or_cheating": (
        r"\bbot(?:s|ting)?\b",
        r"\bcheat(?:er|ers|ing)?\b",
        r"\brmt\b",
        r"real money trad",
    ),
    "balance_or_gameplay": (
        r"\bbalance(?:d|ing)?\b",
        r"\bnerf(?:ed|s|ing)?\b",
        r"\bbuff(?:ed|s|ing)?\b",
        r"\bcombat\b",
        r"\bgameplay\b",
        r"\bclass(?:es)?\b",
    ),
    "support_or_account": (
        r"customer support",
        r"\bsupport\b",
        r"\bban(?:ned|s)?\b",
        r"\baccount\b",
        r"\bregion(?:al)?\b",
    ),
    "content_or_progression": (
        r"\bcontent\b",
        r"\bprogress(?:ion|ing)?\b",
        r"\bgear(?:ing)?\b",
        r"\braid(?:s|ing)?\b",
        r"\bhoning\b",
        r"\bitem level\b",
    ),
}


def run_analysis(
    *,
    history_path: Path,
    current_path: Path,
    daily_path: Path,
    weekly_path: Path,
    language_path: Path,
    issue_path: Path,
    summary_path: Path,
    expected_appid: int = 1599340,
) -> dict[str, object]:
    """Validate history and materialize current plus aggregate-only marts."""

    history = _read_jsonl(history_path)
    if not history:
        raise ValueError("review history is empty")

    missing_by_field = Counter[str]()
    duplicate_version_count = 0
    version_keys: set[tuple[object, object, object]] = set()
    invalid_timestamp_count = 0
    updated_before_created_count = 0
    wrong_appid_count = 0
    valid_rows: list[dict[str, object]] = []

    for row in history:
        for field in REQUIRED_FIELDS:
            if field not in row or row[field] is None:
                missing_by_field[field] += 1
        if row.get("appid") != expected_appid:
            wrong_appid_count += 1
        key = (row.get("appid"), row.get("recommendationid"), row.get("updated_at"))
        if key in version_keys:
            duplicate_version_count += 1
        version_keys.add(key)
        try:
            created = _parse_utc(str(row["created_at"]))
            updated = _parse_utc(str(row["updated_at"]))
        except (KeyError, TypeError, ValueError):
            invalid_timestamp_count += 1
            continue
        if updated < created:
            updated_before_created_count += 1
            continue
        if any(field not in row or row[field] is None for field in REQUIRED_FIELDS):
            continue
        valid_rows.append(row)

    if missing_by_field or invalid_timestamp_count or updated_before_created_count:
        raise ValueError("history failed required-field or timestamp validation")
    if wrong_appid_count:
        raise ValueError("history contains a different appid")

    current_by_id: dict[str, dict[str, object]] = {}
    for row in valid_rows:
        recommendationid = str(row["recommendationid"])
        existing = current_by_id.get(recommendationid)
        if existing is None or _parse_utc(str(row["updated_at"])) > _parse_utc(
            str(existing["updated_at"])
        ):
            current_by_id[recommendationid] = row
    current = sorted(
        current_by_id.values(),
        key=lambda row: (str(row["created_at"]), str(row["recommendationid"])),
    )
    _write_jsonl_atomic(current_path, current)

    daily = _aggregate_time(current, period="day")
    weekly = _aggregate_time(current, period="week")
    languages = _aggregate_languages(current)
    issues = _aggregate_english_issues(current)
    _write_jsonl_atomic(daily_path, daily)
    _write_jsonl_atomic(weekly_path, weekly)
    _write_jsonl_atomic(language_path, languages)
    _write_jsonl_atomic(issue_path, issues)

    negative_count = sum(not bool(row["voted_up"]) for row in current)
    positive_count = len(current) - negative_count
    edit_lags = [
        (
            _parse_utc(str(row["updated_at"])) - _parse_utc(str(row["created_at"]))
        ).total_seconds()
        for row in current
    ]
    modified_count = sum(lag > 0 for lag in edit_lags)
    created_dates = [_parse_utc(str(row["created_at"])).date() for row in current]
    first_created = min(created_dates)
    last_created = max(created_dates)
    calendar_days = (last_created - first_created).days + 1
    first_half_end = first_created + timedelta(days=(calendar_days // 2) - 1)
    first_half = [
        row
        for row in current
        if _parse_utc(str(row["created_at"])).date() <= first_half_end
    ]
    second_half = [row for row in current if row not in first_half]
    period_comparison = _compare_periods(
        first_half,
        second_half,
        first_label=f"{first_created.isoformat()}..{first_half_end.isoformat()}",
        second_label=f"{(first_half_end + timedelta(days=1)).isoformat()}..{last_created.isoformat()}",
    )

    weekly_counts = [int(row["total_count"]) for row in weekly]
    average_daily = len(current) / calendar_days
    per_side_7d = average_daily * 7
    per_side_14d = average_daily * 14
    summary: dict[str, object] = {
        "measurement_type": "measured_from_local_steam_backfill",
        "appid": expected_appid,
        "history_version_count": len(valid_rows),
        "unique_review_count": len(current_by_id),
        "current_review_count": len(current),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "negative_rate": negative_count / len(current),
        "negative_rate_wilson_95": list(_wilson_interval(negative_count, len(current))),
        "first_created_date": first_created.isoformat(),
        "last_created_date": last_created.isoformat(),
        "calendar_days_covered": calendar_days,
        "days_with_reviews": len(daily),
        "weeks_with_reviews": len(weekly),
        "average_reviews_per_calendar_day": average_daily,
        "weekly_count_min": min(weekly_counts),
        "weekly_count_median": statistics.median(weekly_counts),
        "weekly_count_max": max(weekly_counts),
        "days_below_30_reviews": sum(int(row["total_count"]) < 30 for row in daily),
        "weeks_below_30_reviews": sum(int(row["total_count"]) < 30 for row in weekly),
        "language_count": len(languages),
        "modified_review_count": modified_count,
        "unmodified_review_count": len(current) - modified_count,
        "edit_lag_seconds_median": statistics.median(edit_lags),
        "edit_lag_seconds_p95": _percentile(edit_lags, 0.95),
        "edit_lag_seconds_max": max(edit_lags),
        "duplicate_version_count": duplicate_version_count,
        "missing_required_fields": dict(sorted(missing_by_field.items())),
        "invalid_timestamp_count": invalid_timestamp_count,
        "updated_before_created_count": updated_before_created_count,
        "wrong_appid_count": wrong_appid_count,
        "period_comparison": period_comparison,
        "update_window_readiness": {
            "estimated_reviews_per_7d_side": per_side_7d,
            "estimated_reviews_per_14d_side": per_side_14d,
            "worst_case_95pp_half_width_7d_side": _difference_half_width(
                per_side_7d, per_side_7d
            ),
            "worst_case_95pp_half_width_14d_side": _difference_half_width(
                per_side_14d, per_side_14d
            ),
            "interpretation": (
                "Descriptive weekly trends are available. Event-level pre/post "
                "changes smaller than the reported half-widths are not reliably "
                "distinguishable without more observations or wider windows."
            ),
        },
        "bounded_expansion_projection": {
            "basis": "illustrative_linear_projection_from_observed_calendar_daily_rate",
            "days_180_expected_reviews": round(average_daily * 180),
            "days_365_expected_reviews": round(average_daily * 365),
            "days_180_expected_data_pages_at_100": math.ceil(average_daily * 180 / 100),
            "days_365_expected_data_pages_at_100": math.ceil(average_daily * 365 / 100),
        },
        "issue_method": {
            "name": "english_negative_review_keyword_baseline_v1",
            "validated_classifier": False,
            "multi_label": True,
            "supported_languages": ["english"],
            "warning": "Keyword matches are exploratory signals, not verified complaint labels.",
        },
        "known_biases": [
            "Steam reviewers are a self-selected subset of players.",
            "The app represents the Steam/global service and not every Lost Ark region or platform.",
            "Date-range backfill selects by creation time; later edits are only the latest state observed at collection.",
            "Anonymous Steam API responses may be cached for up to ten minutes.",
            "English-only issue keywords create analysis-method coverage bias distinct from source selection bias.",
        ],
    }
    _write_json_atomic(summary_path, summary)
    return summary


def _aggregate_time(
    rows: Sequence[Mapping[str, object]], *, period: str
) -> list[dict[str, object]]:
    groups: dict[date, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        created_date = _parse_utc(str(row["created_at"])).date()
        if period == "day":
            period_start = created_date
        elif period == "week":
            period_start = created_date - timedelta(days=created_date.weekday())
        else:
            raise ValueError("period must be day or week")
        groups[period_start].append(row)

    output: list[dict[str, object]] = []
    for period_start, members in sorted(groups.items()):
        negative = sum(not bool(row["voted_up"]) for row in members)
        lower, upper = _wilson_interval(negative, len(members))
        output.append(
            {
                "appid": members[0]["appid"],
                "period": period,
                "period_start": period_start.isoformat(),
                "total_count": len(members),
                "positive_count": len(members) - negative,
                "negative_count": negative,
                "negative_rate": negative / len(members),
                "negative_rate_ci95_lower": lower,
                "negative_rate_ci95_upper": upper,
                "low_sample_flag": len(members) < 30,
            }
        )
    return output


def _aggregate_languages(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row["language"])].append(row)
    output: list[dict[str, object]] = []
    for language, members in sorted(
        groups.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        negative = sum(not bool(row["voted_up"]) for row in members)
        lower, upper = _wilson_interval(negative, len(members))
        output.append(
            {
                "appid": members[0]["appid"],
                "language": language,
                "total_count": len(members),
                "negative_count": negative,
                "negative_rate": negative / len(members),
                "negative_rate_ci95_lower": lower,
                "negative_rate_ci95_upper": upper,
                "low_sample_flag": len(members) < 30,
                "issue_classifier_supported": language == "english",
            }
        )
    return output


def _aggregate_english_issues(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    english_negative = [
        row
        for row in rows
        if row["language"] == "english" and not bool(row["voted_up"])
    ]
    counts = Counter[str]()
    matched_reviews = 0
    for row in english_negative:
        text = str(row["review_text"]).lower()
        matched = {
            category
            for category, patterns in ENGLISH_ISSUE_PATTERNS.items()
            if any(re.search(pattern, text) for pattern in patterns)
        }
        if matched:
            matched_reviews += 1
            counts.update(matched)
        else:
            counts["unclassified"] += 1
    denominator = len(english_negative)
    return [
        {
            "appid": rows[0]["appid"],
            "language": "english",
            "issue_category": category,
            "matched_negative_review_count": count,
            "english_negative_review_count": denominator,
            "share_of_english_negative_reviews": count / denominator
            if denominator
            else 0.0,
            "method": "keyword_baseline_v1",
            "multi_label": True,
            "matched_any_category_review_count": matched_reviews,
        }
        for category, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _compare_periods(
    first: Sequence[Mapping[str, object]],
    second: Sequence[Mapping[str, object]],
    *,
    first_label: str,
    second_label: str,
) -> dict[str, object]:
    first_negative = sum(not bool(row["voted_up"]) for row in first)
    second_negative = sum(not bool(row["voted_up"]) for row in second)
    first_rate = first_negative / len(first) if first else 0.0
    second_rate = second_negative / len(second) if second else 0.0
    half_width = _difference_half_width(
        len(first), len(second), first_rate, second_rate
    )
    delta_pp = (second_rate - first_rate) * 100
    return {
        "first_period": first_label,
        "second_period": second_label,
        "first_total": len(first),
        "second_total": len(second),
        "first_negative_rate": first_rate,
        "second_negative_rate": second_rate,
        "negative_rate_delta_pp": delta_pp,
        "approx_delta_ci95_lower_pp": delta_pp - (half_width * 100),
        "approx_delta_ci95_upper_pp": delta_pp + (half_width * 100),
        "causal_claim_allowed": False,
    }


def _difference_half_width(
    first_n: float,
    second_n: float,
    first_rate: float = 0.5,
    second_rate: float = 0.5,
) -> float:
    if first_n <= 0 or second_n <= 0:
        return 1.0
    variance = first_rate * (1 - first_rate) / first_n
    variance += second_rate * (1 - second_rate) / second_n
    return 1.96 * math.sqrt(variance)


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 1.0)
    z = 1.96
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate percentile of empty values")
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


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


def _write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    encoded = b"".join(
        (
            json.dumps(
                dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    _write_atomic(path, encoded)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_atomic(path, encoded)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build local current-review and aggregate analysis outputs."
    )
    parser.add_argument(
        "--history", type=Path, default=Path("output/silver_review_history_90d.jsonl")
    )
    parser.add_argument(
        "--current", type=Path, default=Path("output/silver_reviews_current_90d.jsonl")
    )
    parser.add_argument(
        "--daily", type=Path, default=Path("output/mart_review_daily_90d.jsonl")
    )
    parser.add_argument(
        "--weekly", type=Path, default=Path("output/mart_review_weekly_90d.jsonl")
    )
    parser.add_argument(
        "--language", type=Path, default=Path("output/mart_review_language_90d.jsonl")
    )
    parser.add_argument(
        "--issues", type=Path, default=Path("output/mart_issue_language_90d.jsonl")
    )
    parser.add_argument(
        "--summary", type=Path, default=Path("output/review_analysis_summary_90d.json")
    )
    parser.add_argument("--appid", type=int, default=1599340)
    args = parser.parse_args(argv)
    summary = run_analysis(
        history_path=args.history,
        current_path=args.current,
        daily_path=args.daily,
        weekly_path=args.weekly,
        language_path=args.language,
        issue_path=args.issues,
        summary_path=args.summary,
        expected_appid=args.appid,
    )
    safe_summary = {
        key: summary[key]
        for key in (
            "measurement_type",
            "current_review_count",
            "negative_count",
            "negative_rate",
            "language_count",
            "modified_review_count",
            "duplicate_version_count",
            "invalid_timestamp_count",
            "period_comparison",
            "update_window_readiness",
        )
    }
    print(json.dumps(safe_summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
