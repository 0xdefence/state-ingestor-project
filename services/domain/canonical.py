"""Append-only canonical identity, accepted history, and governed keys."""

from dataclasses import dataclass
from datetime import datetime
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
