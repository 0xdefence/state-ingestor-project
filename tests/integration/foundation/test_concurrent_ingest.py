"""Real PostgreSQL races and current-run repository invariants."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import (
    IngestFile,
    IngestResult,
    ReprocessSource,
    ingest_file,
    reprocess_source,
)
from services.application.ports import (
    EventRepository,
    PipelineCheckpoint,
    RawRecordRepository,
    Repositories,
    SourceFile,
)
from services.domain.runs import RunChainInvariantError, RunState
from services.infrastructure.db.canonical_repository import (
    SqlAlchemyCanonicalRepository,
)
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
    SqlAlchemyClassificationRepository,
    SqlAlchemyReviewRepository,
)
from services.infrastructure.db.fx_repository import SqlAlchemyFxRepository
from services.infrastructure.db.models import (
    RawRecordModel,
    RunModel,
    RunSourceOccurrenceModel,
    SourceFileModel,
    SourceOccurrenceModel,
)
from services.infrastructure.db.repositories import (
    SqlAlchemyCheckpointRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySourceRepository,
)
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.source_store import FilesystemSourceStore

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture
def engine() -> Iterator[Engine]:
    url = make_url(
        os.environ.get(
            "TEST_POSTGRES_URL",
            "postgresql+psycopg://alexis:alexis@127.0.0.1:55432/alexis",
        )
    )
    database = "ingest_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    isolated_url = url.set(database=database).render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", isolated_url.replace("%", "%%"))
    test_engine = create_engine(
        isolated_url, connect_args={"options": "-c lock_timeout=5000"}
    )
    try:
        command.upgrade(config, "head")
        yield test_engine
    finally:
        test_engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


def repositories(session: Session) -> Repositories:
    return Repositories(
        SqlAlchemySourceRepository(session),
        SqlAlchemyRunRepository(session),
        cast(RawRecordRepository, Mock(spec=RawRecordRepository)),
        SqlAlchemyCheckpointRepository(session),
        cast(EventRepository, Mock(spec=EventRepository)),
        SqlAlchemyCandidateRepository(session),
        SqlAlchemyClassificationRepository(session),
        SqlAlchemyReviewRepository(session),
        SqlAlchemyFxRepository(session),
        SqlAlchemyCanonicalRepository(session),
    )


def uow_for(engine: Engine) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)


def ingest(
    engine: Engine, root: Path, name: str, key: str | None = None
) -> IngestResult:
    return ingest_file(
        IngestFile(
            BytesIO(b"kind,name\r\ncustomer,Ada\r\n"),
            name,
            "/imports/" + name,
            name,
            key,
        ),
        uow_for(engine),
        FilesystemSourceStore(root),
        FixedClock(),
    )


@pytest.mark.parametrize(
    "existing", [False, True], ids=["first_ingests", "known_source"]
)
def test_concurrent_identical_ingests_create_one_run(
    engine: Engine,
    tmp_path: Path,
    existing: bool,
) -> None:
    if existing:
        ingest(engine, tmp_path, "initial.csv")
    barrier = Barrier(2)
    sessions: list[Session] = []

    class RacingSources(SqlAlchemySourceRepository):
        def get_or_create(self, source: SourceFile) -> tuple[SourceFile, bool]:
            barrier.wait(timeout=5)
            return super().get_or_create(source)

    def racing_repositories(session: Session) -> Repositories:
        sessions.append(session)
        bundle = repositories(session)
        return Repositories(
            RacingSources(session),
            bundle.runs,
            bundle.raw_records,
            bundle.checkpoints,
            bundle.events,
            bundle.candidates,
            bundle.classifications,
            bundle.reviews,
            bundle.fx,
            bundle.canonicals,
        )

    def submit(name: str) -> IngestResult:
        return ingest_file(
            IngestFile(
                BytesIO(b"kind,name\r\ncustomer,Ada\r\n"),
                name,
                "/imports/" + name,
                name,
            ),
            SqlAlchemyUnitOfWork(sessionmaker(engine), racing_repositories),
            FilesystemSourceStore(tmp_path),
            FixedClock(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, "first.csv")
        second = pool.submit(submit, "second.csv")
        results = [first.result(timeout=15), second.result(timeout=15)]
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert results[0].source_file_id == results[1].source_file_id
    assert results[0].run_id == results[1].run_id
    assert results[0].source_occurrence_id != results[1].source_occurrence_id
    with Session(engine) as session:
        assert len(session.scalars(select(SourceFileModel)).all()) == 1
        assert len(session.scalars(select(RunModel)).all()) == 1
        occurrences = session.scalars(select(SourceOccurrenceModel)).all()
        links = session.scalars(select(RunSourceOccurrenceModel)).all()
        assert len(occurrences) == len(links) == 2 + int(existing)
        assert {o.filename for o in occurrences} >= {"first.csv", "second.csv"}
        assert sum(link.relation == "initiated" for link in links) == 1


def test_concurrent_idempotency_replay_creates_nothing_new(
    engine: Engine,
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)

    def submit() -> IngestResult:
        barrier.wait(timeout=5)
        return ingest(engine, tmp_path, "same.csv", "same-request")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        first, second = [f.result(timeout=15) for f in futures]
    assert first.source_occurrence_id == second.source_occurrence_id
    assert first.run_id == second.run_id
    with Session(engine) as session:
        assert len(session.scalars(select(SourceOccurrenceModel)).all()) == 1
        assert len(session.scalars(select(RunSourceOccurrenceModel)).all()) == 1


def test_reprocess_chain_and_original_idempotency_replay(
    engine: Engine,
    tmp_path: Path,
) -> None:
    original = ingest(engine, tmp_path, "original.csv", "original")
    request = ReprocessSource(original.source_file_id, original.source_occurrence_id)
    second = reprocess_source(request, uow_for(engine), FixedClock())
    third = reprocess_source(request, uow_for(engine), FixedClock())
    duplicate = ingest(engine, tmp_path, "duplicate.csv")
    replay = ingest(engine, tmp_path, "original.csv", "original")
    assert duplicate.run_id == third.run_id
    assert replay.run_id == original.run_id
    assert replay.source_occurrence_id == original.source_occurrence_id
    with Session(engine) as session:
        rows = session.scalars(
            select(RunModel).order_by(RunModel.reprocess_sequence)
        ).all()
        assert [r.id for r in rows] == [original.run_id, second.run_id, third.run_id]
        assert [r.predecessor_run_id for r in rows] == [
            None,
            original.run_id,
            second.run_id,
        ]
        assert len(session.scalars(select(SourceOccurrenceModel)).all()) == 2
        assert len(session.scalars(select(RunSourceOccurrenceModel)).all()) == 4


def test_source_lock_emits_select_for_update(engine: Engine, tmp_path: Path) -> None:
    result = ingest(engine, tmp_path, "original.csv")
    with Session(engine) as first, Session(engine) as second:
        SqlAlchemyRunRepository(first).lock_source(result.source_file_id)
        # PostgreSQL NOWAIT must conflict with our repository's row lock.
        from sqlalchemy.exc import OperationalError

        with pytest.raises(OperationalError):
            second.execute(
                select(SourceFileModel)
                .where(SourceFileModel.id == result.source_file_id)
                .with_for_update(nowait=True)
            )


def test_source_insert_does_not_swallow_unrelated_integrity_errors(
    engine: Engine,
) -> None:
    with Session(engine) as session, pytest.raises(IntegrityError):
        SqlAlchemySourceRepository(session).get_or_create(
            SourceFile(uuid4(), "a" * 64, -1, "bad-size")
        )


@pytest.mark.parametrize("damage", ["fork", "cycle", "disconnected", "sequence"])
def test_terminal_run_rejects_broken_chain(
    engine: Engine, tmp_path: Path, damage: str
) -> None:
    first = ingest(engine, tmp_path, "first.csv")
    request = ReprocessSource(first.source_file_id, first.source_occurrence_id)
    second = reprocess_source(request, uow_for(engine), FixedClock())
    third = reprocess_source(request, uow_for(engine), FixedClock())
    with engine.begin() as connection:
        if damage == "fork":
            connection.execute(
                text("ALTER TABLE run DROP CONSTRAINT uq_run_predecessor")
            )
            connection.execute(
                text("UPDATE run SET predecessor_run_id=:first WHERE id=:third"),
                {"first": first.run_id, "third": third.run_id},
            )
        elif damage == "cycle":
            connection.execute(
                text("ALTER TABLE run DROP CONSTRAINT ck_run_reprocess_sequence")
            )
            connection.execute(
                text("UPDATE run SET predecessor_run_id=:third WHERE id=:first"),
                {"first": first.run_id, "third": third.run_id},
            )
        elif damage == "disconnected":
            connection.execute(
                text("ALTER TABLE run DROP CONSTRAINT run_predecessor_run_id_fkey")
            )
            connection.execute(
                text("UPDATE run SET predecessor_run_id=:missing WHERE id=:second"),
                {"missing": uuid4(), "second": second.run_id},
            )
        else:
            connection.execute(
                text("UPDATE run SET reprocess_sequence=9 WHERE id=:third"),
                {"third": third.run_id},
            )
    with Session(engine) as session, pytest.raises(RunChainInvariantError):
        SqlAlchemyRunRepository(session).get_terminal_run(first.source_file_id)


def test_checkpoint_resume_round_trip(engine: Engine, tmp_path: Path) -> None:
    from services.domain.raw import RawRecordKind

    first = ingest(engine, tmp_path, "first.csv")
    raw_id = uuid4()
    checkpoint = PipelineCheckpoint(first.run_id, "parse", 1, 1, raw_id, NOW)
    with uow_for(engine) as uow:
        uow.session.add(
            RawRecordModel(
                id=raw_id,
                run_id=first.run_id,
                source_line_start=2,
                source_line_end=2,
                kind=RawRecordKind.DATA,
                fields=["customer", "Ada"],
                field_count=2,
                parse_metadata={},
            )
        )
        uow.session.flush()
        uow.checkpoints.advance(checkpoint)
        uow.runs.set_state(first.run_id, RunState.PARSING, stage_failure="parse_failed")
        uow.commit()
    duplicate = ingest(engine, tmp_path, "retry.csv")
    assert duplicate.resume_from_checkpoint == checkpoint
    with uow_for(engine) as uow:
        uow.runs.set_state(first.run_id, RunState.STAGED)
        uow.commit()
    completed = ingest(engine, tmp_path, "completed.csv")
    assert completed.resume_from_checkpoint is None
    with Session(engine) as session:
        assert len(session.scalars(select(RawRecordModel)).all()) == 1
