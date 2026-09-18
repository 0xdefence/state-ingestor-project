"""Immutable projection contracts shared by CLI, HTTP and desktop consumers."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from services.application.queries import FileScope
from services.domain.decisions import ReviewDecision

type Scalar = str | int | bool | None | UUID | datetime | date | Decimal
type Value = Scalar | ObjectView | tuple[Value, ...]


@dataclass(frozen=True, slots=True)
class ObjectView:
    """Lossless immutable object, including arbitrarily nested evidence values."""

    fields: tuple[tuple[str, Value], ...]


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    kind: str
    id: UUID
    attributes: ObjectView


@dataclass(frozen=True, slots=True)
class EvidenceView:
    nodes: tuple[EvidenceNode, ...]


@dataclass(frozen=True, slots=True)
class RunView:
    id: UUID
    source_file_id: UUID
    state: str
    state_label: str
    stage_failure: str | None
    created_at: datetime
    processed_at: datetime | None
    counts: ObjectView
    filename: str | None
    predecessor_run_id: UUID | None
    reprocess_sequence: int
    source_sha256: str
    source_byte_size: int
    fx_snapshot_id: UUID | None
    requested_fx_snapshot_date: date | None
    rules_version: str | None
    build_revision: str | None


@dataclass(frozen=True, slots=True)
class ReviewRowView:
    id: UUID
    run_id: UUID
    raw_record_id: UUID
    classification_id: UUID
    candidate_revision_id: UUID
    entity_type: str | None
    business_identifier: str | None
    verdict: str
    verdict_label: str
    readiness: str
    readiness_label: str
    effective_state: str
    effective_state_label: str
    decision_sequence: int
    latest_decision_id: UUID | None
    source_line_start: int
    source_line_end: int
    reason_summaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    scope: FileScope
    runs: tuple[RunView, ...]
    review_counts: ObjectView
    review_items: tuple[ReviewRowView, ...]


@dataclass(frozen=True, slots=True)
class RunDetailView:
    run: RunView
    occurrences: tuple[ObjectView, ...]
    checkpoints: tuple[ObjectView, ...]
    events: tuple[ObjectView, ...]


@dataclass(frozen=True, slots=True)
class ReviewQueueView:
    scope: FileScope
    items: tuple[ReviewRowView, ...]


@dataclass(frozen=True, slots=True)
class DecisionView:
    decision: ReviewDecision
    outcome_label: str


@dataclass(frozen=True, slots=True)
class ReviewDetailView:
    item: ReviewRowView
    evidence: EvidenceView
    decisions: tuple[DecisionView, ...]
    allowed_outcomes: tuple[str, ...]


def code_label(code: str) -> str:
    return {
        "NEEDS_REVIEW": "Needs attention",
        "AUTO_REPAIRED": "Automatically repaired",
        "blocked_by_dependency": "Waiting for another record",
        "staged": "Processed",
        "pending": "Awaiting review",
    }.get(code, code.replace("_", " ").capitalize())
