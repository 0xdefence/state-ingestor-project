"""Replay comparison must preserve evidence types, representation and sequence."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from services.domain.fields import CandidateField, FieldState, SourceRef


@pytest.mark.parametrize(
    "left,right",
    [
        (1, True),
        ({"refs": [{"index": 0}]}, {"refs": [{"index": False}]}),
        ({1: "field"}, {True: "field"}),
        (Decimal("1.00"), Decimal("1.0")),
        (Decimal("-0.00"), Decimal("0.00")),
        (Decimal("1"), 1),
        (FieldState.ABSENT, "absent"),
        (UUID(int=1), str(UUID(int=1))),
        (date(2026, 9, 17), datetime(2026, 9, 17, tzinfo=UTC)),
        (("first", "second"), ["first", "second"]),
        (("first", "second"), ("second", "first")),
        (
            CandidateField.known(1, source_refs=(SourceRef(UUID(int=1), 1),)),
            CandidateField.known(True, source_refs=(SourceRef(UUID(int=1), 1),)),
        ),
    ],
)
def test_evidence_comparison_rejects_type_or_representation_changes(
    left: object, right: object
) -> None:
    from services.infrastructure.db.derived_codec import evidence_equal

    assert not evidence_equal(left, right)


def test_evidence_comparison_accepts_identical_nested_values() -> None:
    from services.infrastructure.db.derived_codec import evidence_equal

    left = {
        "field": CandidateField.known(
            Decimal("1.00"), source_refs=(SourceRef(UUID(int=1), 2),)
        ),
        "at": datetime(2026, 9, 17, tzinfo=UTC),
        "state": FieldState.KNOWN,
        "list": [True, 1, date(2026, 9, 17)],
    }
    right = {
        "list": [True, 1, date(2026, 9, 17)],
        "state": FieldState.KNOWN,
        "at": datetime(2026, 9, 17, tzinfo=UTC),
        "field": CandidateField.known(
            Decimal("1.00"), source_refs=(SourceRef(UUID(int=1), 2),)
        ),
    }
    assert evidence_equal(left, right)
