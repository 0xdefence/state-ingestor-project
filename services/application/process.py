"""Checkpointed parsing and application-level ingest/process composition."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from typing import TYPE_CHECKING, cast
from uuid import UUID

from services.application.errors import ApplicationValidationError
from services.application.ingest import (
    DEFAULT_BUILD_REVISION,
    IngestFile,
    IngestResult,
    ingest_file,
)
from services.application.ports import (
    Clock,
    PipelineCheckpoint,
    PipelineEvent,
    SourceStore,
    UnitOfWork,
)
from services.domain.ids import deterministic_id
from services.domain.raw import RawRecord
from services.domain.runs import RunState
from services.pipeline.parse import parse_records

if TYPE_CHECKING:
    from services.application.load import LoadResult
    from services.pipeline.classify import ClassificationSummary
    from services.pipeline.normalise.candidates import NormaliseContext
    from services.pipeline.rules.registry import RuleRegistry

PIPELINE_EVENT_NAMESPACE = UUID("d6203a35-305c-5df9-9e67-47e08b084d2b")

UnitOfWorkFactory = Callable[[], UnitOfWork]
FailureInjector = Callable[[int], None]
_PARSE_STATES = frozenset((RunState.INGESTED, RunState.PARSING))
_PARSE_COMPLETE_STATES = frozenset(
    (
        RunState.PARSED,
        RunState.NORMALISING,
        RunState.NORMALISED,
        RunState.CLASSIFYING,
        RunState.CLASSIFIED,
        RunState.LOADING,
        RunState.STAGED,
    )
)


@dataclass(frozen=True, slots=True)
class ProcessRun:
    run_id: UUID
    batch_size: int = 1000


@dataclass(frozen=True, slots=True)
class RetryRun:
    run_id: UUID
    batch_size: int = 1000


@dataclass(frozen=True, slots=True)
class StageResult:
    run_id: UUID
    record_count: int


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: UUID
    state: RunState
    parse: StageResult
    normalise: StageResult
    classification_counts: dict[str, int]
    promoted_count: int


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _event(
    uow: UnitOfWork,
    run_id: UUID,
    event_type: str,
    facts: dict[str, object],
    clock: Clock,
    attempt_number: int,
) -> None:
    # Successful work has an identity independent of which attempt committed it.
    # Attempt lifecycle events distinguish repeated failures at one checkpoint.
    if event_type == "batch_committed":
        coordinates = ("batch", facts["batch_number"], facts["record_ordinal"])
    elif event_type == "stage_completed":
        coordinates = ("checkpoint", facts["record_ordinal"])
    else:
        coordinates = ("attempt", attempt_number)
    event_id = deterministic_id(
        PIPELINE_EVENT_NAMESPACE, run_id, "parse", event_type, *coordinates
    )
    uow.events.append(
        PipelineEvent(
            event_id,
            run_id,
            "parse",
            event_type,
            {**facts, "attempt_number": attempt_number},
            clock.now(),
        )
    )


def parse_run(
    run_id: UUID,
    batch_size: int,
    uow_factory: UnitOfWorkFactory,
    source_store: SourceStore,
    failure_injector: FailureInjector | None = None,
    *,
    clock: Clock | None = None,
) -> StageResult:
    """Commit logical-record batches, reopening immutable bytes on every attempt.

    Errors propagate after a separate transaction records the stage failure.
    The injector runs after batch writes and immediately before their commit.
    Completion is its own transaction, so an interruption after the final batch
    is recoverable without replaying evidence. Run counts belong to classification
    and are deliberately not populated by this stage.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    stage_clock = clock if clock is not None else _SystemClock()
    with uow_factory() as uow:
        run = uow.runs.get(run_id)
        checkpoint = uow.checkpoints.get(run_id, "parse")
        ordinal = checkpoint.record_ordinal if checkpoint else 0
        batch_number = checkpoint.batch_number if checkpoint else 0
        if run.state in _PARSE_COMPLETE_STATES:
            return StageResult(run_id, ordinal)
        if run.state not in _PARSE_STATES or run.stage_failure not in (
            None,
            "parse_failed",
        ):
            raise ValueError(f"Run cannot enter parse from {run.state}")
        source = uow.sources.get(run.source_file_id)
        attempt_number = uow.events.next_attempt_number(run_id, "parse")
        uow.runs.set_state(run_id, RunState.PARSING)
        _event(
            uow,
            run_id,
            "stage_retried" if run.state == RunState.PARSING else "stage_started",
            {"record_ordinal": ordinal, "batch_number": batch_number},
            stage_clock,
            attempt_number,
        )
        uow.commit()

    try:
        with source_store.open(source.locator) as binary:
            # Explicitly close the generator before the borrowed stream closes,
            # including failure paths where csv.reader still owns a text wrapper.
            with closing(
                cast(Generator[RawRecord, None, None], parse_records(binary, run_id))
            ) as records:
                for _ in range(ordinal):
                    if next(records, None) is None:
                        raise ValueError("Parse checkpoint exceeds frozen source")
                while batch := list(islice(records, batch_size)):
                    next_ordinal = ordinal + len(batch)
                    next_batch = batch_number + 1
                    with uow_factory() as uow:
                        uow.raw_records.add_batch(batch)
                        uow.checkpoints.advance(
                            PipelineCheckpoint(
                                run_id,
                                "parse",
                                next_batch,
                                next_ordinal,
                                batch[-1].id,
                                stage_clock.now(),
                            )
                        )
                        _event(
                            uow,
                            run_id,
                            "batch_committed",
                            {
                                "batch_number": next_batch,
                                "record_ordinal": next_ordinal,
                                "record_count": len(batch),
                            },
                            stage_clock,
                            attempt_number,
                        )
                        if failure_injector is not None:
                            failure_injector(next_batch)
                        uow.commit()
                    ordinal, batch_number = next_ordinal, next_batch
        with uow_factory() as uow:
            uow.runs.set_state(run_id, RunState.PARSED)
            _event(
                uow,
                run_id,
                "stage_completed",
                {"record_ordinal": ordinal, "batch_number": batch_number},
                stage_clock,
                attempt_number,
            )
            uow.commit()
    except Exception as error:
        with uow_factory() as uow:
            # Read durable state rather than relying on in-memory progress after
            # a failed commit. Earlier batches are never part of this rollback.
            checkpoint = uow.checkpoints.get(run_id, "parse")
            uow.runs.set_state(run_id, RunState.PARSING, stage_failure="parse_failed")
            _event(
                uow,
                run_id,
                "stage_failed",
                {
                    "record_ordinal": checkpoint.record_ordinal if checkpoint else 0,
                    "batch_number": checkpoint.batch_number if checkpoint else 0,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
                stage_clock,
                attempt_number,
            )
            uow.commit()
        raise
    return StageResult(run_id, ordinal)


def normalise_run(
    run_id: UUID,
    batch_size: int,
    uow_factory: UnitOfWorkFactory,
    context: NormaliseContext,
    *,
    failure_injector: FailureInjector | None = None,
) -> StageResult:
    from services.application.normalise import normalise_run as run_stage

    return run_stage(
        run_id,
        batch_size,
        uow_factory,
        context,
        failure_injector=failure_injector,
    )


def classify_run(
    run_id: UUID,
    registry: RuleRegistry,
    uow: UnitOfWork,
    clock: Clock,
) -> ClassificationSummary:
    from services.pipeline.classify import classify_run as run_stage

    return run_stage(run_id, registry, uow, clock)


def stage_run(
    run_id: UUID,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    failure_injector: FailureInjector | None = None,
) -> LoadResult:
    from services.application.load import stage_run as run_stage

    return run_stage(run_id, uow_factory, clock, failure_injector)


def _persisted_result(
    run_id: UUID,
    uow_factory: UnitOfWorkFactory,
    *,
    parsed: StageResult | None,
    normalised: StageResult | None,
    classified: ClassificationSummary | None,
    loaded: LoadResult | None,
) -> RunResult:
    with uow_factory() as uow:
        run = uow.runs.get(run_id)
        parse_checkpoint = uow.checkpoints.get(run_id, "parse")
        normalise_checkpoint = uow.checkpoints.get(run_id, "normalise")
        promoted_count = (
            uow.runs.promoted_count(run_id) if loaded is None else loaded.staged_count
        )
    parsed = parsed or StageResult(
        run_id, parse_checkpoint.record_ordinal if parse_checkpoint else 0
    )
    normalised = normalised or StageResult(
        run_id, normalise_checkpoint.record_ordinal if normalise_checkpoint else 0
    )
    return RunResult(
        run.id,
        run.state,
        parsed,
        normalised,
        dict(classified.counts if classified is not None else run.counts or {}),
        promoted_count,
    )


def process_run(
    command: ProcessRun,
    uow_factory: UnitOfWorkFactory,
    source_store: SourceStore,
    clock: Clock,
) -> RunResult:
    """Own the complete command before reading any durable dispatch state."""
    with uow_factory().processing.hold(command.run_id):
        return _process_owned(command, uow_factory, source_store, clock)


def _process_owned(
    command: ProcessRun,
    uow_factory: UnitOfWorkFactory,
    source_store: SourceStore,
    clock: Clock,
) -> RunResult:
    """Dispatch stages from fresh state while holding processing ownership."""
    from services.pipeline.normalise.candidates import NormaliseContext
    from services.pipeline.rules.registry import default_registry

    parsed: StageResult | None = None
    normalised: StageResult | None = None
    classified: ClassificationSummary | None = None
    loaded: LoadResult | None = None
    while True:
        with uow_factory() as uow:
            run = uow.runs.get(command.run_id)
        if run.state in (RunState.INGESTED, RunState.PARSING):
            parsed = parse_run(
                command.run_id,
                command.batch_size,
                uow_factory,
                source_store,
                clock=clock,
            )
        elif run.state in (RunState.PARSED, RunState.NORMALISING):
            if run.fx_snapshot_id is None:
                with uow_factory() as uow:
                    current = uow.runs.get(command.run_id)
                    if current.fx_snapshot_id is None:
                        uow.runs.pin_fx_snapshot(command.run_id, uow.fx.latest().id)
                        uow.commit()
            normalised = normalise_run(
                command.run_id,
                command.batch_size,
                uow_factory,
                NormaliseContext(clock),
            )
        elif run.state is RunState.NORMALISED:
            classified = classify_run(
                command.run_id, default_registry(), uow_factory(), clock
            )
        elif run.state is RunState.CLASSIFIED:
            loaded = stage_run(command.run_id, uow_factory, clock)
        elif run.state is RunState.STAGED:
            return _persisted_result(
                command.run_id,
                uow_factory,
                parsed=parsed,
                normalised=normalised,
                classified=classified,
                loaded=loaded,
            )
        elif run.state in (RunState.CLASSIFYING, RunState.LOADING):
            raise ApplicationValidationError(
                f"Run cannot be processed from {run.state}"
            )
        else:
            raise ApplicationValidationError(
                f"Run cannot be processed from {run.state}"
            )


def retry_run(
    command: RetryRun,
    uow_factory: UnitOfWorkFactory,
    source_store: SourceStore,
    clock: Clock,
) -> RunResult:
    with uow_factory() as uow:
        run = uow.runs.get(command.run_id)
        if run.stage_failure not in (
            None,
            "parse_failed",
            "normalise_failed",
            "classify_failed",
            "load_failed",
        ):
            raise ValueError(f"Unsupported pipeline failure: {run.stage_failure}")
    return process_run(
        ProcessRun(command.run_id, command.batch_size), uow_factory, source_store, clock
    )


def ingest_and_process(
    command: IngestFile,
    uow_factory: UnitOfWorkFactory,
    source_store: SourceStore,
    clock: Clock,
    *,
    process: bool = False,
    batch_size: int = 1000,
    build_revision: str = DEFAULT_BUILD_REVISION,
) -> IngestResult:
    """Compose ingest with explicit processing, reusing the existing run on retry."""
    result = ingest_file(
        command, uow_factory(), source_store, clock, build_revision=build_revision
    )
    if process:
        with uow_factory() as uow:
            run = uow.runs.get(result.run_id)
        if run.state != RunState.STAGED:
            if result.run_reused:
                retry_run(
                    RetryRun(result.run_id, batch_size),
                    uow_factory,
                    source_store,
                    clock,
                )
            else:
                process_run(
                    ProcessRun(result.run_id, batch_size),
                    uow_factory,
                    source_store,
                    clock,
                )
    return result
