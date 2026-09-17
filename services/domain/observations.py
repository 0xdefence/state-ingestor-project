"""Governed prior evidence supplied by the canonical reader (introduced in Task 7)."""

from dataclasses import dataclass
from uuid import UUID

from services.domain.candidates import CandidateRevision


@dataclass(frozen=True, slots=True)
class PriorObservation:
    identity_id: UUID
    canonical_revision_id: UUID
    run_id: UUID
    candidate: CandidateRevision


@dataclass(frozen=True, slots=True)
class Reobservation:
    id: UUID
    candidate_revision_id: UUID
    identity_id: UUID
    canonical_revision_id: UUID


@dataclass(frozen=True, slots=True)
class ComparisonEvidence:
    candidate_revision_id: UUID
    issue_id: UUID
    conflict_refs: tuple[UUID, ...]
