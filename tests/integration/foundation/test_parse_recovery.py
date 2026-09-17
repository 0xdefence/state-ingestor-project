"""Real PostgreSQL batch atomicity, durable recovery and append-only evidence."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import IngestFile, ingest_file
from services.application.ports import Repositories
from services.domain.raw import RawRecord
from services.domain.runs import RunState
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
    SqlAlchemyClassificationRepository,
    SqlAlchemyReviewRepository,
)
from services.infrastructure.db.models import (
    PipelineEventModel,
    RawRecordModel,
    RunModel,
)
from services.infrastructure.db.repositories import (
    SqlAlchemyCheckpointRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySourceRepository,
)
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.source_store import FilesystemSourceStore
from services.pipeline.parse import parse_records
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.unit.pipeline.test_parse import RECOVERY_BYTES, FixedClock, fail_second_batch


def repositories(session: Session) -> Repositories:
    from services.infrastructure.db.repositories import (
        SqlAlchemyEventRepository,
        SqlAlchemyRawRecordRepository,
    )

    return Repositories(
        SqlAlchemySourceRepository(session),
        SqlAlchemyRunRepository(session),
        SqlAlchemyRawRecordRepository(session),
        SqlAlchemyCheckpointRepository(session),
        SqlAlchemyEventRepository(session),
        SqlAlchemyCandidateRepository(session),
        SqlAlchemyClassificationRepository(session),
        SqlAlchemyReviewRepository(session),
    )


def uow_for(engine: Engine) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)


def records_for(engine: Engine, run_id: UUID) -> list[RawRecord]:
    with Session(engine) as session:
        rows = session.scalars(
            select(RawRecordModel)
            .where(RawRecordModel.run_id == run_id)
            .order_by(RawRecordModel.source_line_start)
        ).all()
        return [
            RawRecord(
                r.id,
                r.run_id,
                r.source_line_start,
                r.source_line_end,
                r.kind,
                tuple(r.fields),
                r.field_count,
                r.parse_metadata,
            )
            for r in rows
        ]


def test_failed_batch_and_retry_match_uninterrupted_parse(
    engine: Engine, tmp_path: Path
) -> None:
    from services.application.process import RetryRun, parse_run, retry_run

    store = FilesystemSourceStore(tmp_path)
    result = ingest_file(
        IngestFile(BytesIO(RECOVERY_BYTES), "input", "/missing", "operator"),
        uow_for(engine),
        store,
        FixedClock(),
    )
    sessions: list[Session] = []

    def factory() -> SqlAlchemyUnitOfWork:
        def tracked_repositories(session: Session) -> Repositories:
            sessions.append(session)
            return repositories(session)

        return SqlAlchemyUnitOfWork(sessionmaker(engine), tracked_repositories)

    with pytest.raises(RuntimeError, match="injected"):
        parse_run(
            result.run_id, 2, factory, store, fail_second_batch, clock=FixedClock()
        )
    expected = list(parse_records(BytesIO(RECOVERY_BYTES), result.run_id))
    assert records_for(engine, result.run_id) == expected[:2]
    with uow_for(engine) as uow:
        checkpoint = uow.checkpoints.get(result.run_id, "parse")
        assert checkpoint is not None
        assert (
            checkpoint.batch_number,
            checkpoint.record_ordinal,
            checkpoint.last_record_id,
        ) == (1, 2, expected[1].id)
        assert uow.runs.get(result.run_id).stage_failure == "parse_failed"
    with Session(engine) as session:
        events = session.scalars(select(PipelineEventModel)).all()
        assert sorted(e.event_type for e in events) == [
            "batch_committed",
            "stage_failed",
            "stage_started",
        ]
        old_events = {e.id: (e.event_type, e.facts) for e in events}

    outcome = retry_run(
        RetryRun(result.run_id, batch_size=2), factory, store, FixedClock()
    )
    assert outcome.state == RunState.PARSED
    assert outcome.parse.record_count == 5
    assert records_for(engine, result.run_id) == expected
    assert len({id(session) for session in sessions}) == len(sessions)
    with Session(engine) as session:
        run = session.get(RunModel, result.run_id)
        assert run is not None and run.state == RunState.PARSED
        assert run.stage_failure is None and run.counts is None
        events = session.scalars(select(PipelineEventModel)).all()
        assert all(
            (e.event_type, e.facts) == old_events[e.id]
            for e in events
            if e.id in old_events
        )
        assert sorted(
            e.facts["record_ordinal"]
            for e in events
            if e.event_type == "batch_committed"
        ) == [2, 4, 5]
    before = len(events)
    assert retry_run(RetryRun(result.run_id), factory, store, FixedClock()) == outcome
    with Session(engine) as session:
        assert len(session.scalars(select(PipelineEventModel)).all()) == before


def test_raw_replay_requires_identical_evidence(engine: Engine, tmp_path: Path) -> None:
    store = FilesystemSourceStore(tmp_path)
    result = ingest_file(
        IngestFile(BytesIO(RECOVERY_BYTES), "input", "/missing", "operator"),
        uow_for(engine),
        store,
        FixedClock(),
    )
    records = list(parse_records(BytesIO(RECOVERY_BYTES), result.run_id))
    with uow_for(engine) as uow:
        uow.raw_records.add_batch(records[:2])
        uow.commit()
    with uow_for(engine) as uow:
        uow.raw_records.add_batch(records[:2])
        uow.commit()
    assert records_for(engine, result.run_id) == records[:2]
    with pytest.raises(ValueError, match="identity"):
        with uow_for(engine) as uow:
            # Earlier pending inserts must also roll back if one replay differs.
            uow.raw_records.add_batch(
                [records[2], replace(records[0], fields=("changed",), field_count=1)]
            )
            uow.commit()
    assert records_for(engine, result.run_id) == records[:2]


def test_event_attempt_numbers_survive_independent_transactions(
    engine: Engine, tmp_path: Path
) -> None:
    from services.application.process import RetryRun, parse_run, retry_run

    store = FilesystemSourceStore(tmp_path)
    result = ingest_file(
        IngestFile(BytesIO(RECOVERY_BYTES), "input", "/missing", "operator"),
        uow_for(engine),
        store,
        FixedClock(),
    )

    def factory() -> SqlAlchemyUnitOfWork:
        return uow_for(engine)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            parse_run(
                result.run_id, 2, factory, store, fail_second_batch, clock=FixedClock()
            )
    with Session(engine) as session:
        events = session.scalars(select(PipelineEventModel)).all()
        assert all(event.id.version == 5 for event in events)
        failures = [event for event in events if event.event_type == "stage_failed"]
        assert sorted(event.facts["attempt_number"] for event in failures) == [1, 2, 3]
        assert [event.facts["record_ordinal"] for event in failures] == [2, 2, 2]
        old_ids = {event.id for event in events}
    retry_run(RetryRun(result.run_id, 2), factory, store, FixedClock())
    with Session(engine) as session:
        events = session.scalars(select(PipelineEventModel)).all()
        assert old_ids <= {event.id for event in events}
        assert (
            len([event for event in events if event.event_type == "batch_committed"])
            == 3
        )
        completions = [
            event for event in events if event.event_type == "stage_completed"
        ]
        assert len(completions) == 1
        assert completions[0].facts["attempt_number"] == 4


def test_success_event_replay_preserves_original_evidence(
    engine: Engine, tmp_path: Path
) -> None:
    from datetime import timedelta

    from services.application.ports import PipelineEvent
    from services.application.process import parse_run

    store = FilesystemSourceStore(tmp_path)
    result = ingest_file(
        IngestFile(BytesIO(RECOVERY_BYTES), "input", "/missing", "operator"),
        uow_for(engine),
        store,
        FixedClock(),
    )

    def factory() -> SqlAlchemyUnitOfWork:
        return uow_for(engine)

    parse_run(result.run_id, 2, factory, store, clock=FixedClock())
    with Session(engine) as session:
        rows = session.scalars(
            select(PipelineEventModel).where(
                PipelineEventModel.event_type.in_(
                    ("batch_committed", "stage_completed")
                )
            )
        ).all()
        originals = [
            PipelineEvent(
                row.id, row.run_id, "parse", row.event_type, row.facts, row.occurred_at
            )
            for row in rows
        ]
    with factory() as uow:
        for original in originals:
            uow.events.append(
                replace(
                    original,
                    facts={**original.facts, "attempt_number": 2},
                    occurred_at=original.occurred_at + timedelta(seconds=1),
                )
            )
        uow.commit()
    with Session(engine) as session:
        for original in originals:
            row = session.get(PipelineEventModel, original.id)
            assert row is not None
            assert row.facts == original.facts
            assert row.occurred_at == original.occurred_at
        assert len(session.scalars(select(PipelineEventModel)).all()) == 5
    with pytest.raises(ValueError, match="identity"):
        with factory() as uow:
            uow.events.append(
                replace(
                    originals[0], facts={**originals[0].facts, "record_ordinal": 999}
                )
            )
            uow.commit()
