"""Serialized idempotency claims and append-only ordered decision history."""

from datetime import UTC
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.domain.decisions import DecisionOutcome, ReviewDecision
from services.infrastructure.db.models import ReviewDecisionModel


def _decision(row: ReviewDecisionModel) -> ReviewDecision:
    return ReviewDecision(
        row.id,
        row.review_item_id,
        row.candidate_revision_id,
        row.sequence,
        DecisionOutcome(row.outcome),
        row.operator_name,
        row.reason,
        row.idempotency_key,
        row.supersedes_decision_id,
        row.decided_at.astimezone(UTC),
    )


class SqlAlchemyDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_idempotency(self, key: str) -> None:
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "review-decision:" + key},
        )

    def by_idempotency_key(self, key: str) -> ReviewDecision | None:
        row = self._session.scalar(
            select(ReviewDecisionModel).where(
                ReviewDecisionModel.idempotency_key == key
            )
        )
        return _decision(row) if row else None

    def for_review(self, review_item_id: UUID) -> tuple[ReviewDecision, ...]:
        return tuple(
            _decision(row)
            for row in self._session.scalars(
                select(ReviewDecisionModel)
                .where(ReviewDecisionModel.review_item_id == review_item_id)
                .order_by(ReviewDecisionModel.sequence)
            )
        )

    def append(self, decision: ReviewDecision) -> None:
        history = self.for_review(decision.review_item_id)
        decision.validate_successor(history[-1] if history else None)
        self._session.add(
            ReviewDecisionModel(
                id=decision.id,
                review_item_id=decision.review_item_id,
                candidate_revision_id=decision.candidate_revision_id,
                sequence=decision.sequence,
                outcome=decision.outcome,
                operator_name=decision.operator_name,
                reason=decision.reason,
                idempotency_key=decision.idempotency_key,
                supersedes_decision_id=decision.supersedes_decision_id,
                decided_at=decision.decided_at,
            )
        )
        self._session.flush()
