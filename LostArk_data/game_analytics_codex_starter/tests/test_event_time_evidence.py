from entertainment_platform.analytics.event_time_evidence import (
    extract_event_time_evidence,
)


def _news() -> dict[str, object]:
    return {
        "appid": 1599340,
        "news_id": "event-1",
        "title": "Synthetic Release Notes",
        "published_at": "2026-08-11T16:00:00Z",
        "url": "https://example.test/release",
    }


def test_explicit_utc_announcement_is_not_promoted_to_effective_time() -> None:
    result = extract_event_time_evidence(
        _news(),
        {"contents": "Downtime will begin on August 12 at 12 AM PT (7 AM UTC)."},
    )

    assert result["evidence_type"] == "announced_maintenance_start"
    assert result["announced_at_utc"] == "2026-08-12T07:00:00Z"
    assert result["effective_at"] is None
    assert result["promotion_allowed"] is False


def test_pt_uses_calendar_aware_los_angeles_offset() -> None:
    result = extract_event_time_evidence(
        _news(), {"contents": "Downtime will begin on June 10 at 12am PT."}
    )

    assert result["announced_at_utc"] == "2026-06-10T07:00:00Z"
    assert result["timezone_basis"] == "us_pacific_dst_rule_2007_plus"


def test_event_start_remains_distinct_from_maintenance() -> None:
    result = extract_event_time_evidence(
        _news(), {"contents": "Event starts June 12, 12 p.m. PT and runs two days."}
    )

    assert result["evidence_type"] == "announced_event_start"
    assert result["announced_at_utc"] == "2026-06-12T19:00:00Z"
    assert result["effective_at"] is None
