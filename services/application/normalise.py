"""Atomic normalization batches over completed, persisted raw evidence."""

from uuid import UUID

from services.application.ports import PipelineCheckpoint, PipelineEvent, UnitOfWork
from services.application.process import (
    PIPELINE_EVENT_NAMESPACE,
    FailureInjector,
    StageResult,
    UnitOfWorkFactory,
)
from services.domain.ids import deterministic_id
from services.domain.raw import RawRecordKind
from services.domain.runs import RunState
from services.pipeline.normalise.candidates import (
    NormaliseContext,
    build_initial_candidate,
)

_COMPLETE_STATES = frozenset(
    (
        RunState.NORMALISED,
        RunState.CLASSIFYING,
        RunState.CLASSIFIED,
        RunState.LOADING,
        RunState.STAGED,
    )
)


def _event(
    uow: UnitOfWork,
    run_id: UUID,
    kind: str,
    facts: dict[str, object],
    context: NormaliseContext,
    attempt: int,
) -> None:
    if kind == "batch_committed":
        coordinates = ("batch", facts["batch_number"], facts["record_ordinal"])
    elif kind == "stage_completed":
        coordinates = ("checkpoint", facts["record_ordinal"])
    else:
        coordinates = ("attempt", attempt)
    uow.events.append(
        PipelineEvent(
            deterministic_id(
                PIPELINE_EVENT_NAMESPACE, run_id, "normalise", kind, *coordinates
            ),
            run_id,
            "normalise",
            kind,
            {**facts, "attempt_number": attempt},
            context.clock.now(),
        )
    )


def normalise_run(
    run_id: UUID,
    batch_size: int,
    uow_factory: UnitOfWorkFactory,
    context: NormaliseContext,
    *,
    failure_injector: FailureInjector | None = None,
) -> StageResult:
    """Resume by logical ordinal; every batch owns one fresh transaction.

    Failure facts are appended after rollback. Counts/classifications belong to
    later stages. Calling a completed stage has no writes or repeated work.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    with uow_factory() as uow:
        run = uow.runs.get(run_id)
        checkpoint = uow.checkpoints.get(run_id, "normalise")
        ordinal = checkpoint.record_ordinal if checkpoint else 0
        batch_number = checkpoint.batch_number if checkpoint else 0
        if run.state in _COMPLETE_STATES:
            return StageResult(run_id, ordinal)
        if run.state not in (
            RunState.PARSED,
            RunState.NORMALISING,
        ) or run.stage_failure not in (None, "normalise_failed"):
            raise ValueError(f"Run cannot enter normalise from {run.state}")
        parse_checkpoint = uow.checkpoints.get(run_id, "parse")
        raw_records = uow.raw_records.for_run(run_id)
        # State is the parse-completion barrier; the checkpoint verifies that
        # the complete logical sequence is actually present, including blanks.
        if len(raw_records) != (
            parse_checkpoint.record_ordinal if parse_checkpoint else 0
        ):
            raise ValueError("Completed parse evidence does not match checkpoint")
        if any(
            raw.parse_metadata.get("logical_ordinal") != i
            for i, raw in enumerate(raw_records, 1)
        ):
            raise ValueError(
                "Persisted raw records must have consecutive logical ordinals"
            )
        if ordinal > len(raw_records) or (
            checkpoint is not None
            and raw_records[ordinal - 1].id != checkpoint.last_record_id
        ):
            raise ValueError("Normalise checkpoint does not match raw evidence")
        attempt = uow.events.next_attempt_number(run_id, "normalise")
        uow.runs.set_state(run_id, RunState.NORMALISING)
        _event(
            uow,
            run_id,
            "stage_retried" if run.state is RunState.NORMALISING else "stage_started",
            {"record_ordinal": ordinal, "batch_number": batch_number},
            context,
            attempt,
        )
        uow.commit()
    try:
        while batch := raw_records[ordinal : ordinal + batch_size]:
            next_ordinal = ordinal + len(batch)
            next_batch = batch_number + 1
            with uow_factory() as uow:
                for raw in batch:
                    result = build_initial_candidate(raw, context)
                    if result.revision is not None:
                        uow.candidates.add(result.revision)
                        for issue in result.issues:
                            uow.candidates.add_issue(issue)
                        for event in result.transformations:
                            uow.candidates.add_transformation(event)
                uow.checkpoints.advance(
                    PipelineCheckpoint(
                        run_id,
                        "normalise",
                        next_batch,
                        next_ordinal,
                        batch[-1].id,
                        context.clock.now(),
                    )
                )
                uow.runs.set_state(run_id, RunState.NORMALISING)
                _event(
                    uow,
                    run_id,
                    "batch_committed",
                    {
                        "batch_number": next_batch,
                        "record_ordinal": next_ordinal,
                        "record_count": len(batch),
                    },
                    context,
                    attempt,
                )
                if failure_injector is not None:
                    failure_injector(next_batch)
                uow.commit()
            ordinal, batch_number = next_ordinal, next_batch
        with uow_factory() as uow:
            initial = [
                r.raw_record_id
                for r in uow.candidates.for_run(run_id)
                if r.revision_number == 1
            ]
            expected = {raw.id for raw in raw_records if raw.kind is RawRecordKind.DATA}
            if len(initial) != len(expected) or set(initial) != expected:
                raise ValueError("Incomplete initial candidate barrier")
            uow.runs.set_state(run_id, RunState.NORMALISED)
            _event(
                uow,
                run_id,
                "stage_completed",
                {"record_ordinal": ordinal, "batch_number": batch_number},
                context,
                attempt,
            )
            uow.commit()
    except Exception as error:
        with uow_factory() as uow:
            checkpoint = uow.checkpoints.get(run_id, "normalise")
            uow.runs.set_state(
                run_id, RunState.NORMALISING, stage_failure="normalise_failed"
            )
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
                context,
                attempt,
            )
            uow.commit()
        raise
    return StageResult(run_id, ordinal)
