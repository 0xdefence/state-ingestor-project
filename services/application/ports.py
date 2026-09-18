"""Dependency-neutral persistence, transaction, source-storage and clock contracts."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import BinaryIO, Literal, Protocol, Self
from uuid import UUID

from services.application.canonical_ports import CanonicalRepository
from services.application.decision_ports import DecisionRepository
from services.application.derived_ports import (
    CandidateRepository,
    ClassificationRepository,
    ReviewRepository,
)
from services.application.fx_ports import FxRepository
from services.domain.raw import RawRecord
from services.domain.runs import RunState

Stage = Literal["parse", "normalise", "classify", "load"]
OccurrenceRelation = Literal["initiated", "duplicate_upload"]


@dataclass(frozen=True, slots=True)
class FrozenSource:
    sha256: str
    byte_size: int
    locator: str
    reused: bool


@dataclass(frozen=True, slots=True)
class SourceFile:
    id: UUID
    sha256: str
    byte_size: int
    locator: str


@dataclass(frozen=True, slots=True)
class SourceOccurrence:
    id: UUID
    source_file_id: UUID
    filename: str
    original_locator: str
    actor_label: str
    idempotency_key: str | None
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    source_file_id: UUID
    predecessor_run_id: UUID | None
    reprocess_sequence: int
    state: RunState
    created_at: datetime
    stage_failure: str | None = None
    fx_snapshot_id: UUID | None = None
    requested_fx_snapshot_date: date | None = None
    rules_version: str | None = None
    build_revision: str | None = None
    counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class RunSourceOccurrence:
    run_id: UUID
    source_occurrence_id: UUID
    relation: OccurrenceRelation
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineCheckpoint:
    run_id: UUID
    stage: Stage
    batch_number: int
    record_ordinal: int
    last_record_id: UUID
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    id: UUID
    run_id: UUID
    stage: Stage
    event_type: str
    facts: dict[str, object]
    occurred_at: datetime


class SourceStore(Protocol):
    def freeze(self, content: BinaryIO) -> FrozenSource: ...
    def open(self, locator: str) -> BinaryIO: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ProcessingOwnership(Protocol):
    def hold(self, run_id: UUID) -> AbstractContextManager[None]:
        """Serialize a whole command across its independent stage transactions.

        Ownership is released on exit or loss of the owning database session.
        Callers must read durable state only after entering this context.
        """
        ...


class SourceRepository(Protocol):
    def get(self, source_file_id: UUID) -> SourceFile: ...
    def get_or_create(self, source: SourceFile) -> tuple[SourceFile, bool]: ...
    def get_occurrence_by_key(self, key: str) -> SourceOccurrence | None: ...
    def add_occurrence(self, occurrence: SourceOccurrence) -> None: ...


class RunRepository(Protocol):
    def lock_source(self, source_file_id: UUID) -> None: ...
    def get(self, run_id: UUID) -> Run: ...
    def get_terminal_run(self, source_file_id: UUID) -> Run | None: ...
    def get_for_occurrence(self, occurrence_id: UUID) -> Run: ...
    def add(self, run: Run) -> None: ...
    def complete_classification(
        self, run_id: UUID, rules_version: str, counts: dict[str, int]
    ) -> None: ...
    def pin_fx_snapshot(self, run_id: UUID, snapshot_id: UUID) -> None: ...
    def promoted_count(self, run_id: UUID) -> int: ...
    def link_occurrence(self, link: RunSourceOccurrence) -> None: ...
    def set_state(
        self, run_id: UUID, state: RunState, *, stage_failure: str | None = None
    ) -> None: ...


class RawRecordRepository(Protocol):
    def for_run(self, run_id: UUID) -> tuple[RawRecord, ...]: ...
    def add_batch(self, records: Sequence[RawRecord]) -> None: ...


class CheckpointRepository(Protocol):
    def get(self, run_id: UUID, stage: Stage) -> PipelineCheckpoint | None: ...
    def advance(self, checkpoint: PipelineCheckpoint) -> None: ...


class EventRepository(Protocol):
    def next_attempt_number(self, run_id: UUID, stage: Stage) -> int:
        """Lock the run and allocate from committed starts in this transaction.

        The caller must append its start event before committing to reserve the
        returned number durably. A rolled-back start consumes no attempt.
        """
        ...

    def append(self, event: PipelineEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class Repositories:
    """The command repositories sharing one transaction."""

    sources: SourceRepository
    runs: RunRepository
    raw_records: RawRecordRepository
    checkpoints: CheckpointRepository
    events: EventRepository
    candidates: CandidateRepository
    classifications: ClassificationRepository
    reviews: ReviewRepository
    fx: FxRepository
    canonicals: CanonicalRepository
    decisions: DecisionRepository


class UnitOfWork(Protocol):
    @property
    def processing(self) -> ProcessingOwnership:
        """Independent command ownership; available outside a stage transaction."""
        ...

    @property
    def decisions(self) -> DecisionRepository: ...
    @property
    def canonicals(self) -> CanonicalRepository: ...
    @property
    def fx(self) -> FxRepository: ...
    @property
    def candidates(self) -> CandidateRepository: ...
    @property
    def classifications(self) -> ClassificationRepository: ...
    @property
    def reviews(self) -> ReviewRepository: ...
    @property
    def sources(self) -> SourceRepository: ...
    @property
    def runs(self) -> RunRepository: ...
    @property
    def raw_records(self) -> RawRecordRepository: ...
    @property
    def checkpoints(self) -> CheckpointRepository: ...
    @property
    def events(self) -> EventRepository: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
