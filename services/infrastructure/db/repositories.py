"""Transaction-bound PostgreSQL repositories for source and run commands."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from hashlib import sha256
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.application.ports import (
    PipelineCheckpoint,
    PipelineEvent,
    Run,
    RunSourceOccurrence,
    SourceFile,
    SourceOccurrence,
    Stage,
)
from services.domain.raw import RawRecord
from services.domain.runs import RunChainInvariantError, RunState
from services.infrastructure.db.derived_codec import evidence_equal
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository as SqlAlchemyCandidateRepository,
)
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyClassificationRepository as SqlAlchemyClassificationRepository,
)
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyReviewRepository as SqlAlchemyReviewRepository,
)
from services.infrastructure.db.models import (
    PipelineCheckpointModel,
    PipelineEventModel,
    RawRecordModel,
    RunModel,
    RunSourceOccurrenceModel,
    SourceFileModel,
    SourceOccurrenceModel,
)


def _source(row: SourceFileModel) -> SourceFile:
    return SourceFile(row.id, row.sha256, row.byte_size, row.locator)


def _run(row: RunModel) -> Run:
    return Run(
        row.id,
        row.source_file_id,
        row.predecessor_run_id,
        row.reprocess_sequence,
        row.state,
        row.created_at,
        row.stage_failure,
        row.fx_snapshot_id,
        row.requested_fx_snapshot_date,
        row.rules_version,
        row.build_revision,
        row.counts,
    )


class SqlAlchemySourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, source_file_id: UUID) -> SourceFile:
        row = self._session.get(SourceFileModel, source_file_id)
        if row is None:
            raise LookupError(f"Unknown source: {source_file_id}")
        return _source(row)

    def get_or_create(self, source: SourceFile) -> tuple[SourceFile, bool]:
        # Only the SHA conflict is benign. PostgreSQL waits for a racing insert
        # to commit; a subsequent READ COMMITTED query sees the winner's row.
        inserted_id = self._session.scalar(
            insert(SourceFileModel)
            .values(**asdict(source))
            .on_conflict_do_nothing(index_elements=[SourceFileModel.sha256])
            .returning(SourceFileModel.id)
        )
        row = self._session.scalars(
            select(SourceFileModel).where(SourceFileModel.sha256 == source.sha256)
        ).one()
        return _source(row), inserted_id is not None

    def get_occurrence_by_key(self, key: str) -> SourceOccurrence | None:
        # Serialize same-key requests before any source is inserted, including
        # requests carrying different bytes. Hash collisions only serialize more.
        lock_key = int.from_bytes(sha256(key.encode()).digest()[:8], signed=True)
        self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        row = self._session.scalar(
            select(SourceOccurrenceModel).where(
                SourceOccurrenceModel.idempotency_key == key
            )
        )
        if row is None:
            return None
        return SourceOccurrence(
            row.id,
            row.source_file_id,
            row.filename,
            row.original_locator,
            row.actor_label,
            row.idempotency_key,
            row.ingested_at,
        )

    def add_occurrence(self, occurrence: SourceOccurrence) -> None:
        # Queue now, flush only after the source lock: immediate FK checks take
        # KEY SHARE locks that could deadlock two later FOR UPDATE upgrades.
        self._session.add(SourceOccurrenceModel(**asdict(occurrence)))


class SqlAlchemyRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_source(self, source_file_id: UUID) -> None:
        with self._session.no_autoflush:
            self._session.scalars(
                select(SourceFileModel.id)
                .where(SourceFileModel.id == source_file_id)
                .with_for_update()
            ).one()

    def get(self, run_id: UUID) -> Run:
        row = self._session.get(RunModel, run_id)
        if row is None:
            raise LookupError(f"Unknown run: {run_id}")
        return _run(row)

    def get_terminal_run(self, source_file_id: UUID) -> Run | None:
        source_ids = select(RunModel.id).where(
            RunModel.source_file_id == source_file_id
        )
        rows = self._session.scalars(
            select(RunModel).where(
                or_(
                    RunModel.source_file_id == source_file_id,
                    RunModel.predecessor_run_id.in_(source_ids),
                )
            )
        ).all()
        if not rows:
            return None
        roots: list[RunModel] = []
        successors: dict[UUID, RunModel] = {}
        for row in rows:
            if row.source_file_id != source_file_id:
                raise RunChainInvariantError("Successor belongs to another source")
            if row.predecessor_run_id is None:
                roots.append(row)
            elif row.predecessor_run_id in successors:
                raise RunChainInvariantError("Run chain forks")
            else:
                successors[row.predecessor_run_id] = row
        if len(roots) != 1:
            raise RunChainInvariantError("Run chain must have exactly one initial run")
        current = roots[0]
        visited: set[UUID] = set()
        sequence = 0
        while True:
            if current.id in visited:
                raise RunChainInvariantError("Run chain cycles")
            if current.reprocess_sequence != sequence:
                raise RunChainInvariantError("Run chain sequence is discontinuous")
            visited.add(current.id)
            successor = successors.get(current.id)
            if successor is None:
                break
            current = successor
            sequence += 1
        if len(visited) != len(rows):
            raise RunChainInvariantError("Run chain has disconnected runs or cycles")
        return _run(current)

    def get_for_occurrence(self, occurrence_id: UUID) -> Run:
        # Reprocessing may link the requesting occurrence to later runs. Its
        # original ingest association remains the idempotency replay target.
        row = self._session.scalars(
            select(RunModel)
            .join(
                RunSourceOccurrenceModel, RunSourceOccurrenceModel.run_id == RunModel.id
            )
            .where(RunSourceOccurrenceModel.source_occurrence_id == occurrence_id)
            .order_by(RunModel.reprocess_sequence)
            .limit(1)
        ).one()
        return _run(row)

    def add(self, run: Run) -> None:
        self._session.add(RunModel(**asdict(run)))
        self._session.flush()

    def link_occurrence(self, link: RunSourceOccurrence) -> None:
        self._session.add(RunSourceOccurrenceModel(**asdict(link)))

    def complete_classification(
        self, run_id: UUID, rules_version: str, counts: dict[str, int]
    ) -> None:
        row = self._session.get(RunModel, run_id)
        if row is None:
            raise LookupError(f"Unknown run: {run_id}")
        if row.rules_version is not None and row.rules_version != rules_version:
            raise ValueError(
                "Classification rules identity mismatch; reprocess required"
            )
        if row.counts is not None and not evidence_equal(row.counts, counts):
            raise ValueError("Classification counts identity mismatch")
        row.rules_version = rules_version
        row.counts = dict(counts)
        row.state = RunState.CLASSIFIED
        row.stage_failure = None

    def set_state(
        self, run_id: UUID, state: RunState, *, stage_failure: str | None = None
    ) -> None:
        row = self._session.get(RunModel, run_id)
        if row is None:
            raise LookupError(f"Unknown run: {run_id}")
        row.state = state
        row.stage_failure = stage_failure


class SqlAlchemyCheckpointRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: UUID, stage: Stage) -> PipelineCheckpoint | None:
        row = self._session.get(PipelineCheckpointModel, (run_id, stage))
        if row is None:
            return None
        return PipelineCheckpoint(
            row.run_id,
            stage,
            row.batch_number,
            row.record_ordinal,
            row.last_record_id,
            row.updated_at,
        )

    def advance(self, checkpoint: PipelineCheckpoint) -> None:
        values = asdict(checkpoint)
        self._session.execute(
            insert(PipelineCheckpointModel)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[
                    PipelineCheckpointModel.run_id,
                    PipelineCheckpointModel.stage,
                ],
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in ("run_id", "stage")
                },
            )
        )


def _json_value(value: object) -> object:
    """Thaw immutable domain metadata only at the JSON persistence boundary."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        return {key: _json_value(item) for key, item in mapping.items()}
    if isinstance(value, tuple):
        sequence = cast(tuple[object, ...], value)
        return [_json_value(item) for item in sequence]
    return value


class SqlAlchemyRawRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def for_run(self, run_id: UUID) -> tuple[RawRecord, ...]:
        return tuple(
            RawRecord(
                row.id,
                row.run_id,
                row.source_line_start,
                row.source_line_end,
                row.kind,
                tuple(row.fields),
                row.field_count,
                row.parse_metadata,
            )
            for row in self._session.scalars(
                select(RawRecordModel)
                .where(RawRecordModel.run_id == run_id)
                .order_by(RawRecordModel.parse_metadata["logical_ordinal"].as_integer())
            )
        )

    def add_batch(self, records: Sequence[RawRecord]) -> None:
        for record in records:
            values: dict[str, object] = {
                "id": record.id,
                "run_id": record.run_id,
                "source_line_start": record.source_line_start,
                "source_line_end": record.source_line_end,
                "kind": record.kind,
                "fields": list(record.fields),
                "field_count": record.field_count,
                "parse_metadata": _json_value(record.parse_metadata),
            }
            inserted = self._session.scalar(
                insert(RawRecordModel)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[RawRecordModel.id])
                .returning(RawRecordModel.id)
            )
            if inserted is None:
                row = self._session.scalars(
                    select(RawRecordModel).where(RawRecordModel.id == record.id)
                ).one()
                persisted = RawRecord(
                    row.id,
                    row.run_id,
                    row.source_line_start,
                    row.source_line_end,
                    row.kind,
                    tuple(row.fields),
                    row.field_count,
                    row.parse_metadata,
                )
                if persisted != record:
                    raise ValueError(f"Raw record identity mismatch: {record.id}")


class SqlAlchemyEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def next_attempt_number(self, run_id: UUID, stage: Stage) -> int:
        # Serialize attempt allocation until the caller commits the start event.
        # Keep this separate from the aggregate query: FOR UPDATE cannot lock an
        # aggregate result, and READ COMMITTED must see a waiting worker's commit.
        self._session.scalars(
            select(RunModel.id).where(RunModel.id == run_id).with_for_update()
        ).one()
        latest: int | None = self._session.scalar(
            select(
                func.max(PipelineEventModel.facts["attempt_number"].as_integer())
            ).where(
                PipelineEventModel.run_id == run_id,
                PipelineEventModel.stage == stage,
                PipelineEventModel.event_type.in_(("stage_started", "stage_retried")),
            )
        )
        return (latest or 0) + 1

    def append(self, event: PipelineEvent) -> None:
        inserted = self._session.scalar(
            insert(PipelineEventModel)
            .values(**asdict(event))
            .on_conflict_do_nothing(index_elements=[PipelineEventModel.id])
            .returning(PipelineEventModel.id)
        )
        if inserted is not None:
            return
        row = self._session.scalars(
            select(PipelineEventModel).where(PipelineEventModel.id == event.id)
        ).one()
        stored_facts = dict(row.facts)
        incoming_facts = dict(event.facts)
        if event.event_type in ("batch_committed", "stage_completed"):
            # Replay retains the original committing attempt and timestamp.
            # All facts about the completed work must still agree.
            stored_facts.pop("attempt_number", None)
            incoming_facts.pop("attempt_number", None)
        if (row.run_id, row.stage, row.event_type) != (
            event.run_id,
            event.stage,
            event.event_type,
        ) or stored_facts != incoming_facts:
            raise ValueError(f"Pipeline event identity mismatch: {event.id}")
