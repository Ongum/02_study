"""Join update priors to measured reactions for an auditable decision mart.

Expected risk, observed reaction, and evidence confidence stay separate.  This
prevents a broad update, a negative review change, or missing data from being
collapsed into one number whose meaning cannot be explained.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def build_update_decision_rows(
    classifications: Sequence[Mapping[str, Any]],
    impacts: Sequence[Mapping[str, Any]],
    time_evidence: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build one row per classified event, retaining unevaluated events."""

    impact_by_id = {str(row["event_id"]): row for row in impacts}
    if len(impact_by_id) != len(impacts):
        raise ValueError("impact event_id values must be unique")
    time_by_id = {str(row["event_id"]): row for row in time_evidence}
    if len(time_by_id) != len(time_evidence):
        raise ValueError("time evidence event_id values must be unique")

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for classification in classifications:
        event_id = str(classification["event_id"])
        if not event_id or event_id in seen:
            raise ValueError(
                "classification event_id values must be non-empty and unique"
            )
        seen.add(event_id)
        impact = impact_by_id.get(event_id)
        output.append(_decision_row(classification, impact, time_by_id.get(event_id)))
    return sorted(output, key=lambda row: (row["published_at"], row["event_id"]))


def _decision_row(
    classification: Mapping[str, Any],
    impact: Mapping[str, Any] | None,
    time_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    scope = _score(classification, "expected_scope_score")
    technical = _score(classification, "expected_technical_risk_score")
    monetization = _score(classification, "expected_monetization_risk_score")
    expected_prior = round(scope * 0.5 + technical * 0.3 + monetization * 0.2, 2)

    delta = _optional_float(impact, "negative_rate_delta_pp")
    negative_signal = (
        round(min(100.0, max(0.0, delta) / 20.0 * 100.0), 2)
        if delta is not None
        else None
    )
    observed_components = [negative_signal] if negative_signal is not None else []
    observed_reaction = (
        round(sum(observed_components) / len(observed_components), 2)
        if observed_components
        else None
    )
    confidence = _data_confidence(classification, impact)
    decision_priority = (
        round(expected_prior * 0.35 + observed_reaction * 0.65, 2)
        if observed_reaction is not None
        else None
    )
    recommendation = _recommendation(impact, delta, confidence)
    quality_flags = list(impact.get("quality_flags", [])) if impact else []
    if impact is None:
        quality_flags.append("reaction_not_evaluated")
    if time_evidence and time_evidence.get("announced_at_utc") is not None:
        quality_flags.append("announced_time_not_effective_time")
    quality_flags.extend(["player_signal_unavailable", "issue_signal_unavailable"])

    return {
        "appid": int(classification["appid"]),
        "event_id": str(classification["event_id"]),
        "event_title": str(classification["event_title"]),
        "published_at": str(classification["published_at"]),
        "effective_at": classification.get("effective_at"),
        "announced_at_utc": (
            time_evidence.get("announced_at_utc") if time_evidence else None
        ),
        "event_time_evidence_type": (
            time_evidence.get("evidence_type") if time_evidence else None
        ),
        "event_time_evidence_status": (
            time_evidence.get("evidence_status") if time_evidence else "unavailable"
        ),
        "primary_type": str(classification["primary_type"]),
        "secondary_types": list(classification.get("secondary_types", [])),
        "expected_scope_score": scope,
        "expected_technical_risk_score": technical,
        "expected_monetization_risk_score": monetization,
        "expected_monitoring_prior_score": expected_prior,
        "pre_review_count": int(impact["pre_total"]) if impact else None,
        "post_review_count": int(impact["post_total"]) if impact else None,
        "negative_rate_delta_pp": delta,
        "negative_review_signal_score": negative_signal,
        "player_change_signal_score": None,
        "issue_lift_signal_score": None,
        "observed_reaction_score": observed_reaction,
        "observed_component_count": len(observed_components),
        "data_confidence_score": confidence,
        "decision_priority_score": decision_priority,
        "priority_band": _priority_band(decision_priority, confidence),
        "recommended_action": recommendation,
        "quality_flags": sorted(set(quality_flags)),
        "causal_claim_allowed": False,
        "classification_method": classification["classification_method"],
        "score_policy_version": "update_decision_v1",
    }


def _data_confidence(
    classification: Mapping[str, Any], impact: Mapping[str, Any] | None
) -> int:
    """Score evidence availability, not whether the observed result is large."""

    if impact is None:
        return 0
    points = 0
    if classification.get("human_verified") is True:
        points += 10
    elif classification.get("classification_confidence") == "medium_rule_based":
        points += 5
    elif classification.get("classification_confidence") == "low_rule_based":
        points += 2
    if impact.get("effective_at") is not None:
        points += 20
    if impact.get("pre_window_complete") is True:
        points += 15
    if impact.get("post_window_complete") is True:
        points += 15
    pre_total = int(impact.get("pre_total", 0))
    post_total = int(impact.get("post_total", 0))
    minimum_side = min(pre_total, post_total)
    if minimum_side >= 50:
        points += 20
    elif minimum_side >= 30:
        points += 12
    elif minimum_side > 0:
        points += 5
    lower = _optional_float(impact, "approx_delta_ci95_lower_pp")
    upper = _optional_float(impact, "approx_delta_ci95_upper_pp")
    if lower is not None and upper is not None:
        width = upper - lower
        if width <= 20:
            points += 10
        elif width <= 35:
            points += 5
    # Ten points are reserved for event-aligned player and issue signals. They
    # are unavailable in the current local dataset and are not imputed.
    return min(points, 100)


def _recommendation(
    impact: Mapping[str, Any] | None, delta: float | None, confidence: int
) -> str:
    if impact is None or delta is None:
        return "collect_event_aligned_measurements"
    flags = set(impact.get("quality_flags", []))
    if "low_sample" in flags or not (
        impact.get("pre_window_complete") and impact.get("post_window_complete")
    ):
        return "collect_more_data_and_monitor"
    if impact.get("effective_at") is None:
        return (
            "triage_while_validating_event_time"
            if delta >= 10
            else "validate_event_time_and_monitor"
        )
    lower = _optional_float(impact, "approx_delta_ci95_lower_pp")
    upper = _optional_float(impact, "approx_delta_ci95_upper_pp")
    if lower is not None and lower > 0 and delta >= 10 and confidence >= 60:
        return "prioritize_investigation"
    if upper is not None and upper < 0:
        return "monitor_positive_signal"
    if delta >= 5:
        return "targeted_investigation"
    return "continue_standard_monitoring"


def _priority_band(score: float | None, confidence: int) -> str:
    if score is None or confidence < 40:
        return "evidence_limited"
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _score(row: Mapping[str, Any], field: str) -> float:
    value = float(row[field])
    if not 0 <= value <= 100:
        raise ValueError(f"{field} must be in [0, 100]")
    return value


def _optional_float(row: Mapping[str, Any] | None, field: str) -> float | None:
    if row is None or row.get(field) is None:
        return None
    return float(row[field])


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
    parser.add_argument("--classifications", type=Path, required=True)
    parser.add_argument("--impacts", type=Path, required=True)
    parser.add_argument("--time-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_update_decision_rows(
        _read_jsonl(args.classifications),
        _read_jsonl(args.impacts),
        _read_jsonl(args.time_evidence) if args.time_evidence else (),
    )
    _write_jsonl_atomic(args.output, rows)
    print(
        json.dumps(
            {
                "rows_written": len(rows),
                "evaluated_events": sum(
                    row["observed_reaction_score"] is not None for row in rows
                ),
                "evidence_limited_events": sum(
                    row["priority_band"] == "evidence_limited" for row in rows
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
