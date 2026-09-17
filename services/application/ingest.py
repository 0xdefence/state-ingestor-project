"""Freeze submissions and associate them with the current exact-content run."""

from dataclasses import dataclass
from typing import BinaryIO
from uuid import UUID, uuid4

from services.application.ports import (
    Clock,
    PipelineCheckpoint,
    Run,
    RunSourceOccurrence,
    SourceFile,
    SourceOccurrence,
    SourceStore,
    Stage,
    UnitOfWork,
)
from services.domain.runs import RunState


@dataclass(frozen=True, slots=True)
class IngestFile:
    content: BinaryIO
    filename: str
    original_locator: str
    actor_label: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ReprocessSource:
    source_file_id: UUID
    source_occurrence_id: UUID


@dataclass(frozen=True, slots=True)
class IngestResult:
    source_file_id: UUID
    source_occurrence_id: UUID
    run_id: UUID
    source_reused: bool
    run_reused: bool
    duplicate_upload: bool
    resume_from_checkpoint: PipelineCheckpoint | None


def ingest_file(
    command: IngestFile, uow: UnitOfWork, source_store: SourceStore, clock: Clock
) -> IngestResult:
    frozen = source_store.freeze(command.content)
    with uow:
        if command.idempotency_key is not None:
            replay = uow.sources.get_occurrence_by_key(command.idempotency_key)
            if replay is not None:
                run = uow.runs.get_for_occurrence(replay.id)
                result = IngestResult(
                    replay.source_file_id,
                    replay.id,
                    run.id,
                    True,
                    True,
                    False,
                    _resume_checkpoint(run, uow),
                )
                uow.commit()
                return result

        source, created = uow.sources.get_or_create(
            SourceFile(uuid4(), frozen.sha256, frozen.byte_size, frozen.locator)
        )
        now = clock.now()
        occurrence = SourceOccurrence(
            uuid4(),
            source.id,
            command.filename,
            command.original_locator,
            command.actor_label,
            command.idempotency_key,
            now,
        )
        uow.sources.add_occurrence(occurrence)
        uow.runs.lock_source(source.id)
        run = uow.runs.get_terminal_run(source.id)
        reused = run is not None
        if run is None:
            run = Run(uuid4(), source.id, None, 0, RunState.INGESTED, now)
            uow.runs.add(run)
        uow.runs.link_occurrence(
            RunSourceOccurrence(
                run.id,
                occurrence.id,
                "duplicate_upload" if reused else "initiated",
                now,
            )
        )
        result = IngestResult(
            source.id,
            occurrence.id,
            run.id,
            not created,
            reused,
            reused,
            _resume_checkpoint(run, uow) if reused else None,
        )
        uow.commit()
        return result


def reprocess_source(
    command: ReprocessSource, uow: UnitOfWork, clock: Clock
) -> IngestResult:
    with uow:
        source = uow.sources.get(command.source_file_id)
        uow.runs.lock_source(source.id)
        requesting_run = uow.runs.get_for_occurrence(command.source_occurrence_id)
        if requesting_run.source_file_id != source.id:
            raise ValueError("Requesting occurrence belongs to another source")
        predecessor = uow.runs.get_terminal_run(source.id)
        if predecessor is None:
            raise ValueError("Cannot reprocess a source without an initial run")
        now = clock.now()
        successor = Run(
            uuid4(),
            source.id,
            predecessor.id,
            predecessor.reprocess_sequence + 1,
            RunState.INGESTED,
            now,
        )
        uow.runs.add(successor)
        uow.runs.link_occurrence(
            RunSourceOccurrence(
                successor.id, command.source_occurrence_id, "initiated", now
            )
        )
        result = IngestResult(
            source.id,
            command.source_occurrence_id,
            successor.id,
            True,
            False,
            False,
            None,
        )
        uow.commit()
        return result


def _resume_checkpoint(run: Run, uow: UnitOfWork) -> PipelineCheckpoint | None:
    if run.state == RunState.STAGED:
        return None
    failure_stages: dict[str, Stage] = {
        "parse_failed": "parse",
        "normalise_failed": "normalise",
        "classify_failed": "classify",
        "load_failed": "load",
    }
    state_stages: dict[RunState, Stage] = {
        RunState.CREATED: "parse",
        RunState.INGESTED: "parse",
        RunState.PARSING: "parse",
        RunState.PARSED: "normalise",
        RunState.NORMALISING: "normalise",
        RunState.NORMALISED: "classify",
        RunState.CLASSIFYING: "classify",
        RunState.CLASSIFIED: "load",
        RunState.LOADING: "load",
    }
    stage = (
        failure_stages[run.stage_failure]
        if run.stage_failure
        else state_stages[run.state]
    )
    return uow.checkpoints.get(run.id, stage)
