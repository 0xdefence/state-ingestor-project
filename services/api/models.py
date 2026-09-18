"""HTTP validation models; domain commands validate business semantics."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.application.decisions import DecideReview
from services.domain.decisions import DecisionOutcome


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_revision_id: UUID
    expected_sequence: int = Field(ge=0, strict=True)
    outcome: DecisionOutcome
    operator_name: str = Field(min_length=1)
    reason: str | None = None
    idempotency_key: str = Field(min_length=1)
    supersedes_decision_id: UUID | None = None

    def command(self, review_item_id: UUID) -> DecideReview:
        return DecideReview(
            review_item_id,
            self.candidate_revision_id,
            self.expected_sequence,
            self.outcome,
            self.operator_name,
            self.reason,
            self.idempotency_key,
            self.supersedes_decision_id,
        )
