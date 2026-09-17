"""Append-only whole-record decisions and their effective review state."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DecisionOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ACKNOWLEDGE = "acknowledge"


class EffectiveReviewState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACKNOWLEDGED = "acknowledged"


def validate_actor(
    outcome: DecisionOutcome,
    operator_name: str,
    reason: str | None,
    idempotency_key: str,
) -> None:
    if not operator_name.strip() or not idempotency_key.strip():
        raise ValueError("operator and idempotency key must be nonempty")
    if outcome is DecisionOutcome.REJECT and not (reason and reason.strip()):
        raise ValueError("rejection requires a reason")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    id: UUID
    review_item_id: UUID
    candidate_revision_id: UUID
    sequence: int
    outcome: DecisionOutcome
    operator_name: str
    reason: str | None
    idempotency_key: str
    supersedes_decision_id: UUID | None
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.id.version != 4:
            raise ValueError("decision identity requires UUIDv4")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("decision sequence must be positive")
        validate_actor(
            self.outcome, self.operator_name, self.reason, self.idempotency_key
        )

    def validate_successor(self, previous: "ReviewDecision | None") -> None:
        if previous is None:
            if self.sequence != 1 or self.supersedes_decision_id is not None:
                raise ValueError(
                    "first decision must start sequence without supersession"
                )
        elif (
            self.review_item_id != previous.review_item_id
            or self.sequence != previous.sequence + 1
            or self.supersedes_decision_id != previous.id
        ):
            raise ValueError("supersession requires latest decision in same review")

    @property
    def effective_state(self) -> EffectiveReviewState:
        return {
            DecisionOutcome.APPROVE: EffectiveReviewState.APPROVED,
            DecisionOutcome.REJECT: EffectiveReviewState.REJECTED,
            DecisionOutcome.ACKNOWLEDGE: EffectiveReviewState.ACKNOWLEDGED,
        }[self.outcome]
