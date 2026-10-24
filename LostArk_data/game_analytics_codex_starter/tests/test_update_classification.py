from entertainment_platform.analytics.update_classification import (
    classify_update_event,
)


def test_multi_label_update_classification_keeps_body_out_of_result() -> None:
    news = {
        "appid": 1599340,
        "news_id": "event-1",
        "title": "September Update",
        "published_at": "2026-09-15T16:14:21Z",
        "effective_at": None,
        "url": "https://example.test/update",
    }
    raw = {
        "contents": (
            "A new class has arrived with gear progression, balance changes "
            "and Growth Boost Shop items for new and returning players."
        )
    }

    result = classify_update_event(news, raw)

    assert result["primary_type"] == "content_class"
    assert "progression_economy" in result["secondary_types"]
    assert "balance_combat" in result["secondary_types"]
    assert "monetization_store" in result["secondary_types"]
    assert result["effective_at"] is None
    assert result["human_verified"] is False
    assert "contents" not in result


def test_service_transition_gets_high_monitoring_prior() -> None:
    news = {
        "appid": 1599340,
        "news_id": "event-2",
        "title": "An Update on the Service",
        "published_at": "2026-08-12T08:10:23Z",
        "effective_at": None,
        "url": "https://example.test/service",
    }
    raw = {"contents": "The publisher will end its live service operations."}

    result = classify_update_event(news, raw)

    assert result["primary_type"] == "live_service_transition"
    assert result["expected_scope_score"] == 100
    assert result["expected_technical_risk_score"] == 95
