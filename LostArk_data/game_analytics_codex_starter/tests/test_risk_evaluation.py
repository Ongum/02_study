import pytest

from entertainment_platform.analytics.risk_evaluation import (
    RiskEvaluationError,
    ScoredOutcome,
    evaluate_binary_risk,
)


def test_perfect_ranking_has_full_ar_and_expected_cp20() -> None:
    rows = [
        ScoredOutcome(f"entity-{index}", (10 - index) / 10, index < 4)
        for index in range(10)
    ]

    result = evaluate_binary_risk(rows)

    assert result["auc_roc"] == pytest.approx(1.0)
    assert result["accuracy_ratio"] == pytest.approx(1.0)
    assert result["capture_entity_count"] == 2
    assert result["capture_rate"] == pytest.approx(0.5)
    assert result["grades"][0]["grade"] == "R1"
    assert result["grades"][0]["observed_outcome_rate"] == pytest.approx(1.0)


def test_tied_scores_have_neutral_ar() -> None:
    rows = [
        ScoredOutcome("a", 0.5, True),
        ScoredOutcome("b", 0.5, False),
        ScoredOutcome("c", 0.5, True),
        ScoredOutcome("d", 0.5, False),
    ]

    result = evaluate_binary_risk(rows, grade_count=2)

    assert result["auc_roc"] == pytest.approx(0.5)
    assert result["accuracy_ratio"] == pytest.approx(0.0)


def test_ar_rejects_single_class_outcomes() -> None:
    rows = [
        ScoredOutcome("a", 0.8, True),
        ScoredOutcome("b", 0.2, True),
    ]

    with pytest.raises(RiskEvaluationError, match="both outcome classes"):
        evaluate_binary_risk(rows)
