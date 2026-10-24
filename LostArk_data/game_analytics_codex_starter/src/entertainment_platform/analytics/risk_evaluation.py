"""Evaluate a ranked binary-risk model without external dependencies.

This module is intentionally domain-neutral.  It can evaluate churn only when
the caller supplies a real forward-looking churn label.  Review sentiment or a
drop in aggregate concurrent players must not be substituted for that label.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


class RiskEvaluationError(ValueError):
    """Raised when scores cannot support a valid binary ranking evaluation."""


@dataclass(frozen=True, slots=True)
class ScoredOutcome:
    """One prediction made before its binary outcome observation window."""

    entity_id: str
    risk_score: float
    outcome: bool


@dataclass(frozen=True, slots=True)
class GradeSummary:
    """Observed outcome rate for one equal-count risk grade."""

    grade: str
    risk_order: int
    entity_count: int
    outcome_count: int
    observed_outcome_rate: float
    minimum_score: float
    maximum_score: float


def evaluate_binary_risk(
    rows: Sequence[ScoredOutcome],
    *,
    capture_fraction: float = 0.20,
    grade_count: int = 5,
) -> dict[str, Any]:
    """Return AR, capture at the selected fraction, calibration, and grades.

    Higher scores mean greater risk.  Grade ``R1`` is the highest-risk bucket.
    Capture uses exactly ``ceil(n * capture_fraction)`` rows; ties are resolved
    deterministically by ``entity_id`` so reruns are stable.
    """

    _validate(rows, capture_fraction=capture_fraction, grade_count=grade_count)
    ordered = sorted(rows, key=lambda row: (-row.risk_score, row.entity_id))
    positive_count = sum(row.outcome for row in rows)
    negative_count = len(rows) - positive_count
    auc = _auc_with_average_ties(rows, positive_count, negative_count)
    top_count = max(1, math.ceil(len(rows) * capture_fraction))
    captured = sum(row.outcome for row in ordered[:top_count])
    grade_rows = _grade_summaries(ordered, grade_count)
    brier_score = sum((row.risk_score - float(row.outcome)) ** 2 for row in rows) / len(
        rows
    )

    return {
        "entity_count": len(rows),
        "outcome_count": positive_count,
        "observed_outcome_rate": positive_count / len(rows),
        "auc_roc": auc,
        "accuracy_ratio": 2 * auc - 1,
        "capture_fraction": capture_fraction,
        "capture_entity_count": top_count,
        "captured_outcome_count": captured,
        "capture_rate": captured / positive_count,
        "brier_score": brier_score,
        "grade_count": grade_count,
        "grade_direction": "R1 is highest risk",
        "grades": [asdict(row) for row in grade_rows],
    }


def _validate(
    rows: Sequence[ScoredOutcome], *, capture_fraction: float, grade_count: int
) -> None:
    if not rows:
        raise RiskEvaluationError("at least one scored outcome is required")
    if not 0 < capture_fraction <= 1:
        raise RiskEvaluationError("capture_fraction must be in (0, 1]")
    if grade_count < 2:
        raise RiskEvaluationError("grade_count must be at least 2")
    entity_ids: set[str] = set()
    for row in rows:
        if not row.entity_id or row.entity_id in entity_ids:
            raise RiskEvaluationError("entity_id values must be non-empty and unique")
        entity_ids.add(row.entity_id)
        if not math.isfinite(row.risk_score) or not 0 <= row.risk_score <= 1:
            raise RiskEvaluationError("risk_score values must be finite and in [0, 1]")
    positive_count = sum(row.outcome for row in rows)
    if positive_count == 0 or positive_count == len(rows):
        raise RiskEvaluationError("AR requires both outcome classes")


def _auc_with_average_ties(
    rows: Sequence[ScoredOutcome], positive_count: int, negative_count: int
) -> float:
    """Compute ROC AUC from average ascending score ranks in O(n log n)."""

    ascending = sorted(rows, key=lambda row: row.risk_score)
    positive_rank_sum = 0.0
    start = 0
    while start < len(ascending):
        end = start + 1
        while (
            end < len(ascending)
            and ascending[end].risk_score == ascending[start].risk_score
        ):
            end += 1
        # Statistical ranks are one-based.  Every tied item receives the
        # average rank of the tied block.
        average_rank = ((start + 1) + end) / 2
        positive_rank_sum += average_rank * sum(
            row.outcome for row in ascending[start:end]
        )
        start = end
    return (positive_rank_sum - positive_count * (positive_count + 1) / 2) / (
        positive_count * negative_count
    )


def _grade_summaries(
    ordered: Sequence[ScoredOutcome], grade_count: int
) -> list[GradeSummary]:
    buckets: dict[int, list[ScoredOutcome]] = defaultdict(list)
    for index, row in enumerate(ordered):
        bucket = min(grade_count, math.floor(index * grade_count / len(ordered)) + 1)
        buckets[bucket].append(row)

    summaries: list[GradeSummary] = []
    for risk_order in sorted(buckets):
        members = buckets[risk_order]
        outcome_count = sum(row.outcome for row in members)
        summaries.append(
            GradeSummary(
                grade=f"R{risk_order}",
                risk_order=risk_order,
                entity_count=len(members),
                outcome_count=outcome_count,
                observed_outcome_rate=outcome_count / len(members),
                minimum_score=min(row.risk_score for row in members),
                maximum_score=max(row.risk_score for row in members),
            )
        )
    return summaries
