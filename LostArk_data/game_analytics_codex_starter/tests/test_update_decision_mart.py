from entertainment_platform.analytics.update_decision_mart import (
    build_update_decision_rows,
)


def _classification(event_id: str) -> dict[str, object]:
    return {
        "appid": 1599340,
        "event_id": event_id,
        "event_title": "Synthetic Update",
        "published_at": "2026-09-15T00:00:00Z",
        "effective_at": None,
        "primary_type": "content_class",
        "secondary_types": ["balance_combat"],
        "expected_scope_score": 80,
        "expected_technical_risk_score": 40,
        "expected_monetization_risk_score": 0,
        "classification_method": "test_rules",
        "classification_confidence": "medium_rule_based",
        "human_verified": False,
    }


def test_decision_mart_separates_prior_reaction_and_confidence() -> None:
    impact = {
        "event_id": "event-1",
        "effective_at": None,
        "pre_total": 60,
        "post_total": 70,
        "negative_rate_delta_pp": 12.0,
        "approx_delta_ci95_lower_pp": 2.0,
        "approx_delta_ci95_upper_pp": 22.0,
        "pre_window_complete": True,
        "post_window_complete": True,
        "quality_flags": ["effective_time_unvalidated"],
    }

    row = build_update_decision_rows([_classification("event-1")], [impact])[0]

    assert row["expected_monitoring_prior_score"] == 52.0
    assert row["negative_review_signal_score"] == 60.0
    assert row["observed_reaction_score"] == 60.0
    assert row["data_confidence_score"] == 65
    assert row["player_change_signal_score"] is None
    assert row["recommended_action"] == "triage_while_validating_event_time"
    assert row["causal_claim_allowed"] is False


def test_decision_mart_retains_event_without_reaction_data() -> None:
    row = build_update_decision_rows([_classification("event-2")], [])[0]

    assert row["observed_reaction_score"] is None
    assert row["data_confidence_score"] == 0
    assert row["priority_band"] == "evidence_limited"
    assert row["recommended_action"] == "collect_event_aligned_measurements"
    assert "reaction_not_evaluated" in row["quality_flags"]


def test_announced_time_is_visible_but_does_not_raise_confidence() -> None:
    impact = {
        "event_id": "event-3",
        "effective_at": None,
        "pre_total": 60,
        "post_total": 60,
        "negative_rate_delta_pp": 2.0,
        "approx_delta_ci95_lower_pp": -8.0,
        "approx_delta_ci95_upper_pp": 12.0,
        "pre_window_complete": True,
        "post_window_complete": True,
        "quality_flags": ["effective_time_unvalidated"],
    }
    time_evidence = {
        "event_id": "event-3",
        "announced_at_utc": "2026-09-16T07:00:00Z",
        "evidence_type": "announced_maintenance_start",
        "evidence_status": "announced_not_observed",
    }

    row = build_update_decision_rows(
        [_classification("event-3")], [impact], [time_evidence]
    )[0]

    assert row["announced_at_utc"] == "2026-09-16T07:00:00Z"
    assert row["effective_at"] is None
    assert row["data_confidence_score"] == 65
    assert "announced_time_not_effective_time" in row["quality_flags"]
