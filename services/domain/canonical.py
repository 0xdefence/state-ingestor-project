"""Append-only canonical identity, accepted history, and governed keys."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CanonicalIdentity:
    id: UUID
    entity_type: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.id.version != 4 or self.entity_type not in (
            "customer",
            "product",
            "order",
        ):
            raise ValueError(
                "canonical identity requires UUIDv4 and a supported entity"
            )


@dataclass(frozen=True, slots=True)
class CanonicalRevision:
    id: UUID
    identity_id: UUID
    candidate_revision_id: UUID
    revision_number: int
    staged_at: datetime

    def __post_init__(self) -> None:
        if self.id.version != 4 or self.identity_id.version != 4:
            raise ValueError("canonical revisions and identities require UUIDv4")
        if type(self.revision_number) is not int or self.revision_number < 1:
            raise ValueError("canonical revision number must be positive")


@dataclass(frozen=True, slots=True)
class CanonicalBusinessKey:
    identity_id: UUID
    key_type: str
    value: str
    effective_revision: UUID


class PromotionAction(StrEnum):
    ACTIVATE = "activate"
    WITHDRAW = "withdraw"


@dataclass(frozen=True, slots=True)
class CanonicalPromotionEvent:
    id: UUID
    identity_id: UUID
    canonical_revision_id: UUID
    action: PromotionAction
    decision_id: UUID | None
    prior_current_revision_id: UUID | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if any(
            value.version != 4
            for value in (self.id, self.identity_id, self.canonical_revision_id)
        ):
            raise ValueError("promotion identities require UUIDv4")
