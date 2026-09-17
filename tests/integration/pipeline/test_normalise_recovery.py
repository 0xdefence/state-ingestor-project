"""Durable normalization rollback, replay and complete persisted evidence equality."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import IngestFile, ingest_file
from services.application.ports import PipelineEvent, Repositories
from services.application.process import parse_run
from services.domain.runs import RunState
from services.infrastructure.db.derived_codec import evidence_equal
from services.infrastructure.db.models import (
    CandidateRevisionModel,
    DataQualityIssueModel,
    PipelineCheckpointModel,
    PipelineEventModel,
    RawRecordModel,
    RunModel,
    TransformationEventModel,
)
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.source_store import FilesystemSourceStore
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import repositories, uow_for
from tests.unit.pipeline.test_parse import FixedClock


def parsed_run(engine: Engine, tmp_path: Path) -> UUID:
    store = FilesystemSourceStore(tmp_path)
    with Path("data/messy_sample_data.csv").open("rb") as source:
        result = ingest_file(
            IngestFile(source, "fixture", "fixture", "test"),
            uow_for(engine),
            store,
            FixedClock(),
        )
    parse_run(result.run_id, 10, lambda: uow_for(engine), store, clock=FixedClock())
    return result.run_id


def stable_graph(engine: Engine, run_id: UUID) -> dict[str, object]:
    with Session(engine) as session:
        graph: dict[str, object] = {}
        for model in (
            RawRecordModel,
            CandidateRevisionModel,
            DataQualityIssueModel,
            TransformationEventModel,
            PipelineCheckpointModel,
            PipelineEventModel,
        ):
            rows = []
            for row in session.scalars(select(model)).all():
                if (
                    isinstance(row, (PipelineCheckpointModel, PipelineEventModel))
                    and row.stage != "normalise"
                ):
                    continue
                if isinstance(row, PipelineEventModel) and row.event_type not in (
                    "batch_committed",
                    "stage_completed",
                ):
                    continue
                values = {
                    column.name: getattr(row, column.name)
                    for column in model.__table__.columns
                }
                if isinstance(row, PipelineEventModel):
                    values["facts"] = {
                        key: value
                        for key, value in row.facts.items()
                        if key != "attempt_number"
                    }
                rows.append(values)
            graph[model.__tablename__] = sorted(
                rows, key=lambda row: str(row.get("id", row))
            )
        run = session.get(RunModel, run_id)
        graph["run"] = {
            "state": run.state,
            "stage_failure": run.stage_failure,
            "counts": run.counts,
        }
        return graph


def event_history(engine: Engine, run_id: UUID) -> tuple[PipelineEvent, ...]:
    """Read every event field, without stripping execution-history metadata."""
    with Session(engine) as session:
        return tuple(
            PipelineEvent(
                row.id,
                row.run_id,
                row.stage,
                row.event_type,
                row.facts,
                row.occurred_at,
            )
            for row in session.scalars(
                select(PipelineEventModel)
                .where(PipelineEventModel.run_id == run_id)
                .order_by(PipelineEventModel.id)
            )
        )


def assert_retained_history(
    previous: tuple[PipelineEvent, ...], current: tuple[PipelineEvent, ...]
) -> None:
    by_id = {event.id: event for event in current}
    assert all(
        event.id in by_id and evidence_equal(event, by_id[event.id])
        for event in previous
    )


def clear_normalisation(engine: Engine, run_id: UUID) -> None:
    # Test-only reset to the same parsed input, preserving run/raw identities.
    with Session(engine) as session:
        for model in (
            TransformationEventModel,
            DataQualityIssueModel,
            CandidateRevisionModel,
        ):
            session.execute(delete(model))
        for model in (PipelineCheckpointModel, PipelineEventModel):
            session.execute(delete(model).where(model.stage == "normalise"))
        session.execute(
            update(RunModel)
            .where(RunModel.id == run_id)
            .values(state=RunState.PARSED, stage_failure=None)
        )
        session.commit()


@pytest.mark.parametrize("failed_batch", [1, 2, 6])
def test_failed_batch_retry_graph_equals_uninterrupted(
    engine: Engine, tmp_path: Path, failed_batch: int
) -> None:
    from services.application.normalise import normalise_run
    from services.pipeline.normalise.candidates import NormaliseContext

    run_id = parsed_run(engine, tmp_path)
    context = NormaliseContext(FixedClock())
    sessions: list[Session] = []

    def factory() -> SqlAlchemyUnitOfWork:
        def tracked(session: Session) -> Repositories:
            sessions.append(session)
            return repositories(session)

        return SqlAlchemyUnitOfWork(sessionmaker(engine), tracked)

    assert normalise_run(run_id, 10, factory, context).record_count == 52
    expected = stable_graph(engine, run_id)
    clear_normalisation(engine, run_id)

    def fail(batch: int) -> None:
        if batch == failed_batch:
            raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError, match="injected"):
        normalise_run(run_id, 10, factory, context, failure_injector=fail)
    with factory() as uow:
        checkpoint = uow.checkpoints.get(run_id, "normalise")
        assert (checkpoint.record_ordinal if checkpoint else 0) == (
            failed_batch - 1
        ) * 10
        assert uow.runs.get(run_id).stage_failure == "normalise_failed"
        assert uow.runs.get(run_id).state is RunState.NORMALISING
        revisions = uow.candidates.for_run(run_id)
        assert len(revisions) == {1: 0, 2: 9, 6: 46}[failed_batch]
    first_failure_history = event_history(engine, run_id)
    with pytest.raises(RuntimeError, match="injected"):
        normalise_run(run_id, 10, factory, context, failure_injector=fail)
    second_failure_history = event_history(engine, run_id)
    assert_retained_history(first_failure_history, second_failure_history)
    assert normalise_run(run_id, 10, factory, context).record_count == 52
    assert evidence_equal(stable_graph(engine, run_id), expected)
    completed_history = event_history(engine, run_id)
    assert_retained_history(second_failure_history, completed_history)
    normalization_events = [e for e in completed_history if e.stage == "normalise"]
    assert len({e.id for e in normalization_events}) == len(normalization_events)
    assert all(e.id.version == 5 for e in normalization_events)
    assert all(e.occurred_at == FixedClock().now() for e in normalization_events)
    ordinal = (failed_batch - 1) * 10
    lifecycle = [
        e
        for e in normalization_events
        if e.event_type in ("stage_started", "stage_retried", "stage_failed")
    ]
    assert sorted(
        (
            e.facts["attempt_number"],
            e.event_type,
            e.facts["record_ordinal"],
            e.facts["batch_number"],
        )
        for e in lifecycle
    ) == sorted(
        [
            (1, "stage_started", 0, 0),
            (1, "stage_failed", ordinal, failed_batch - 1),
            (2, "stage_retried", ordinal, failed_batch - 1),
            (2, "stage_failed", ordinal, failed_batch - 1),
            (3, "stage_retried", ordinal, failed_batch - 1),
        ]
    )
    for event in lifecycle:
        keys = {"attempt_number", "record_ordinal", "batch_number"}
        if event.event_type == "stage_failed":
            keys |= {"error_type", "message"}
            assert event.facts["error_type"] == "RuntimeError"
            assert event.facts["message"] == "injected before commit"
        assert set(event.facts) == keys
    successful = [
        e
        for e in normalization_events
        if e.event_type in ("batch_committed", "stage_completed")
    ]
    batches = sorted(
        (e for e in successful if e.event_type == "batch_committed"),
        key=lambda e: e.facts["batch_number"],
    )
    assert [e.facts for e in batches] == [
        {
            "batch_number": batch,
            "record_ordinal": min(batch * 10, 52),
            "record_count": 10 if batch < 6 else 2,
            "attempt_number": 1 if batch < failed_batch else 3,
        }
        for batch in range(1, 7)
    ]
    completions = [e for e in successful if e.event_type == "stage_completed"]
    assert len(completions) == 1
    assert completions[0].facts == {
        "attempt_number": 3,
        "record_ordinal": 52,
        "batch_number": 6,
    }
    assert len(normalization_events) == len(lifecycle) + len(successful) == 12
    # Replaying successful work retains its original committing attempt/time.
    with factory() as uow:
        for event in successful:
            uow.events.append(
                replace(
                    event,
                    facts={**event.facts, "attempt_number": 99},
                    occurred_at=event.occurred_at + timedelta(days=1),
                )
            )
        uow.commit()
    assert evidence_equal(event_history(engine, run_id), completed_history)
    assert normalise_run(run_id, 10, factory, context).record_count == 52
    assert evidence_equal(stable_graph(engine, run_id), expected)
    assert evidence_equal(event_history(engine, run_id), completed_history)
    assert len({id(session) for session in sessions}) == len(sessions)
    with factory() as uow:
        revisions = uow.candidates.for_run(run_id)
        assert len(revisions) == len({r.raw_record_id for r in revisions}) == 48
        assert all(r.revision_number == 1 and r.id.version == 5 for r in revisions)
        annotated = next(
            r for r in revisions if getattr(r.payload, "unit_price_annotation", None)
        )
        assert annotated.payload.unit_price_annotation == "10% off"
    with Session(engine) as session:
        failures = session.scalars(
            select(PipelineEventModel).where(
                PipelineEventModel.stage == "normalise",
                PipelineEventModel.event_type == "stage_failed",
            )
        ).all()
        assert len(failures) == 2
        assert all(e.facts["record_ordinal"] == ordinal for e in failures)


def test_normalise_requires_parse_completion(engine: Engine, tmp_path: Path) -> None:
    from services.application.normalise import normalise_run
    from services.pipeline.normalise.candidates import NormaliseContext

    run_id = parsed_run(engine, tmp_path)
    with Session(engine) as session:
        session.execute(
            update(RunModel).where(RunModel.id == run_id).values(state=RunState.PARSING)
        )
        session.commit()
    with pytest.raises(ValueError, match="normalise"):
        normalise_run(
            run_id, 10, lambda: uow_for(engine), NormaliseContext(FixedClock())
        )
    with Session(engine) as session:
        assert not session.scalars(select(CandidateRevisionModel)).all()
        assert not session.scalars(
            select(PipelineEventModel).where(PipelineEventModel.stage == "normalise")
        ).all()


def test_candidate_replay_checks_complete_evidence(
    engine: Engine, tmp_path: Path
) -> None:
    from services.application.normalise import normalise_run
    from services.pipeline.normalise.candidates import NormaliseContext

    run_id = parsed_run(engine, tmp_path)
    normalise_run(run_id, 10, lambda: uow_for(engine), NormaliseContext(FixedClock()))
    before = stable_graph(engine, run_id)
    with uow_for(engine) as uow:
        revisions = uow.candidates.for_run(run_id)
        for revision in revisions:
            uow.candidates.add(revision)
            for event in uow.candidates.transformations(revision.id):
                uow.candidates.add_transformation(event)
            for issue in uow.candidates.issues(revision.id):
                uow.candidates.add_issue(issue)
        uow.commit()
    assert evidence_equal(stable_graph(engine, run_id), before)
    revision = revisions[0]
    with pytest.raises(ValueError, match="identity|immutable"):
        with uow_for(engine) as uow:
            uow.candidates.add(
                replace(revision, created_at=revision.created_at.replace(year=2025))
            )
    with uow_for(engine) as uow:
        event = uow.candidates.transformations(revision.id)[0]
        issue = next(issue for r in revisions for issue in uow.candidates.issues(r.id))
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            uow.candidates.add_transformation(replace(event, sequence=999))
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            uow.candidates.add_issue(replace(issue, summary="changed"))
    assert evidence_equal(stable_graph(engine, run_id), before)


def test_replay_rejects_different_decimal_representation(
    engine: Engine, tmp_path: Path
) -> None:
    from decimal import Decimal

    from services.application.normalise import normalise_run
    from services.domain.candidates import CustomerCandidate, Money
    from services.domain.fields import CandidateField
    from services.pipeline.normalise.candidates import NormaliseContext

    run_id = parsed_run(engine, tmp_path)
    normalise_run(run_id, 10, lambda: uow_for(engine), NormaliseContext(FixedClock()))
    with uow_for(engine) as uow:
        revision = uow.candidates.for_run(run_id)[0]
        assert isinstance(revision.payload, CustomerCandidate)
        amount = revision.payload.lifetime_spend
        assert amount.value == Money(Decimal("1240.50"), "USD")
        changed = replace(amount, value=Money(Decimal("1240.500"), "USD"))
        events = uow.candidates.transformations(revision.id)
        event = next(t for t in events if isinstance(t.after.value, Money))
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            uow.candidates.add(
                replace(
                    revision, payload=replace(revision.payload, lifetime_spend=changed)
                )
            )
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            uow.candidates.add_transformation(
                replace(
                    event,
                    after=CandidateField.known(
                        Money(Decimal("1240.500"), "USD"),
                        source_refs=event.after.source_refs,
                    ),
                )
            )


@pytest.mark.parametrize(
    "changed_evidence",
    [
        "candidate_value",
        "candidate_source",
        "transformation_value",
        "transformation_sequence",
        "issue_source",
        "issue_code",
    ],
)
def test_replay_rejects_equal_but_differently_typed_evidence(
    engine: Engine, tmp_path: Path, changed_evidence: str
) -> None:
    from services.application.normalise import normalise_run
    from services.domain.candidates import CustomerCandidate, OrderCandidate
    from services.domain.issues import IssueCode
    from services.pipeline.normalise.candidates import NormaliseContext

    run_id = parsed_run(engine, tmp_path)
    normalise_run(run_id, 10, lambda: uow_for(engine), NormaliseContext(FixedClock()))
    before = stable_graph(engine, run_id)
    with uow_for(engine) as uow:
        revisions = uow.candidates.for_run(run_id)
        customer = revisions[0]
        assert isinstance(customer.payload, CustomerCandidate)
        order = next(
            r
            for r in revisions
            if isinstance(r.payload, OrderCandidate) and r.payload.quantity.value == 1
        )
        assert isinstance(order.payload, OrderCandidate)
        events = uow.candidates.transformations(order.id)
        integer = next(
            t for t in events if type(t.after.value) is int and t.after.value == 1
        )
        first_event = events[0]
        issue = next(
            i
            for r in revisions
            for i in uow.candidates.issues(r.id)
            if i.code is IssueCode.INVALID_STRUCTURE
        )
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            if changed_evidence == "candidate_value":
                uow.candidates.add(
                    replace(
                        order,
                        payload=replace(
                            order.payload,
                            quantity=replace(order.payload.quantity, value=True),
                        ),
                    )
                )
            elif changed_evidence == "candidate_source":
                identity = customer.payload.customer_id
                ref = identity.source_refs[0]
                assert ref.field_index == 1
                uow.candidates.add(
                    replace(
                        customer,
                        payload=replace(
                            customer.payload,
                            customer_id=replace(
                                identity, source_refs=(replace(ref, field_index=True),)
                            ),
                        ),
                    )
                )
            elif changed_evidence == "transformation_value":
                uow.candidates.add_transformation(
                    replace(integer, after=replace(integer.after, value=True))
                )
            elif changed_evidence == "transformation_sequence":
                assert first_event.sequence == 1
                uow.candidates.add_transformation(replace(first_event, sequence=True))
            elif changed_evidence == "issue_source":
                assert issue.source_refs[0].field_index == 0
                uow.candidates.add_issue(
                    replace(
                        issue,
                        source_refs=(
                            replace(issue.source_refs[0], field_index=False),
                            *issue.source_refs[1:],
                        ),
                    )
                )
            else:
                uow.candidates.add_issue(replace(issue, code=str(issue.code)))
    assert evidence_equal(stable_graph(engine, run_id), before)
