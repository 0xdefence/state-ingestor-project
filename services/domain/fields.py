"""Typed candidate-field values and source evidence references."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class FieldState(StrEnum):
    """The completeness of a field in a candidate revision."""

    KNOWN = "known"
    ABSENT = "absent"
    DEFERRED = "deferred"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SourceRef:
    """A reference to one raw-record field used as evidence."""

    raw_record_id: UUID
    field_index: int | None = None


@dataclass(frozen=True, slots=True)
class CandidateField[T]:
    """A typed candidate value with its source and derived evidence."""

    state: FieldState
    value: T | None
    source_refs: tuple[SourceRef, ...]
    transformation_refs: tuple[UUID, ...] = ()
    issue_refs: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.state is FieldState.KNOWN and self.value is None:
            raise ValueError("known field requires a value")
        if self.state is not FieldState.KNOWN and self.value is not None:
            raise ValueError(f"{self.state} field cannot carry a value")

    @classmethod
    def known(
        cls,
        value: T,
        *,
        source_refs: tuple[SourceRef, ...],
        transformation_refs: tuple[UUID, ...] = (),
        issue_refs: tuple[UUID, ...] = (),
    ) -> CandidateField[T]:
        """Build a field whose value is known."""
        return cls(
            state=FieldState.KNOWN,
            value=value,
            source_refs=source_refs,
            transformation_refs=transformation_refs,
            issue_refs=issue_refs,
        )
