"""Durable normalization rollback, replay and complete persisted evidence equality."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import IngestFile, ingest_file
from services.application.ports import Repositories
from services.application.process import parse_run
from services.domain.runs import RunState
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


def stable_graph(engine: Engine, run_id: UUID) -> str:
    with Session(engine) as session:
        graph = {}
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
        return json.dumps(graph, default=str, sort_keys=True, separators=(",", ":"))


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
    assert normalise_run(run_id, 10, factory, context).record_count == 52
    assert stable_graph(engine, run_id) == expected
    assert normalise_run(run_id, 10, factory, context).record_count == 52
    assert stable_graph(engine, run_id) == expected
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
        assert len(failures) == 1 and failures[0].id.version == 5
        assert failures[0].facts["record_ordinal"] == (failed_batch - 1) * 10


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
    assert stable_graph(engine, run_id) == before
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
    assert stable_graph(engine, run_id) == before


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
