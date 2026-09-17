"""Immutable decisions reject invalid actor, sequence and supersession evidence."""

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from services.application.decisions import DecideReview
from services.domain.canonical import CanonicalPromotionEvent, PromotionAction
from services.domain.decisions import DecisionOutcome, ReviewDecision
from tests.unit.classify.test_rules import NOW

REVIEW = uuid5(NAMESPACE_URL, "review")
CANDIDATE = uuid5(NAMESPACE_URL, "candidate")


def command(**changes):
    return replace(
        DecideReview(
            REVIEW, CANDIDATE, 0, DecisionOutcome.APPROVE, "Alex", None, "key"
        ),
        **changes,
    )


def test_rejection_requires_reason():
    with pytest.raises(ValueError, match="reason"):
        command(outcome=DecisionOutcome.REJECT)


@pytest.mark.parametrize(
    "changes",
    [{"operator_name": " "}, {"idempotency_key": " "}, {"expected_sequence": -1}],
)
def test_operator_key_and_sequence_are_validated(changes):
    with pytest.raises(ValueError):
        command(**changes)


def test_decision_uuid_sequence_and_same_review_supersession():
    first = ReviewDecision(
        uuid4(),
        REVIEW,
        CANDIDATE,
        1,
        DecisionOutcome.APPROVE,
        "Alex",
        None,
        "key",
        None,
        NOW,
    )
    second = replace(first, id=uuid4(), sequence=2, supersedes_decision_id=first.id)
    second.validate_successor(first)
    for invalid in (
        replace(second, review_item_id=uuid4()),
        replace(second, sequence=3),
        replace(second, supersedes_decision_id=uuid4()),
    ):
        with pytest.raises(ValueError):
            invalid.validate_successor(first)
    for changes in ({"id": REVIEW}, {"sequence": 0}):
        with pytest.raises(ValueError):
            replace(first, **changes)


def test_promotion_identity_requires_uuid4():
    event = CanonicalPromotionEvent(
        uuid4(), uuid4(), uuid4(), PromotionAction.ACTIVATE, None, None, NOW
    )
    with pytest.raises(ValueError, match="UUIDv4"):
        replace(event, id=REVIEW)
