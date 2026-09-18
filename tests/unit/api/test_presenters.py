from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from services.api.presenters import present, present_code, present_instant


def test_plain_labels_and_unknown_fallback():
    assert present_code("NEEDS_REVIEW") == "Needs attention"
    assert present_code("new_internal_value") == "New internal value"


def test_instant_preserves_utc_and_explicit_display_zone():
    assert present_instant(
        datetime(2026, 9, 17, 12, tzinfo=UTC), ZoneInfo("Europe/London")
    ) == {
        "instant": "2026-09-17T12:00:00Z",
        "display": "17 September 2026, 13:00 BST",
        "timezone": "Europe/London",
    }
    assert present(Decimal("12.3400")) == "12.3400"


def test_source_local_datetime_is_not_invented_as_utc_instant():
    assert present(datetime(2025, 3, 9, 10, 15, 30)) == "2025-03-09T10:15:30"
