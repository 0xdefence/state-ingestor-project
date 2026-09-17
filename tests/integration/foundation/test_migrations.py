"""Exercise migrations and invariants against isolated Postgres databases."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.application.ports import (
    CheckpointRepository,
    EventRepository,
    RawRecordRepository,
    Repositories,
    RunRepository,
    SourceRepository,
)
from services.domain.runs import RunState
from services.infrastructure.db.canonical_repository import (
    SqlAlchemyCanonicalRepository,
)
from services.infrastructure.db.decision_repository import SqlAlchemyDecisionRepository
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
    SqlAlchemyClassificationRepository,
    SqlAlchemyReviewRepository,
)
from services.infrastructure.db.fx_repository import SqlAlchemyFxRepository
from services.infrastructure.db.models import RunModel
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork

TABLES = {
    "source_file",
    "source_occurrence",
    "run",
    "run_source_occurrence",
    "pipeline_checkpoint",
    "pipeline_event",
    "raw_record",
}
SOURCE = "00000000-0000-0000-0000-000000000001"
RUN = "00000000-0000-0000-0000-000000000002"
OCCURRENCE = "00000000-0000-0000-0000-000000000003"
RAW = "00000000-0000-0000-0000-000000000004"


@pytest.fixture
def postgres_url() -> Iterator[str]:
    base_url = make_url(
        os.environ.get(
            "TEST_POSTGRES_URL",
            "postgresql+psycopg://alexis:alexis@localhost:55432/alexis",
        )
    )
    database = f"foundation_test_{uuid4().hex}"
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        yield base_url.set(database=database).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


def migration_config(postgres_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    return config


def test_upgrade_creates_foundation_tables(postgres_url: str) -> None:
    config = migration_config(postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        assert set(inspect(engine).get_table_names()) >= TABLES
        command.downgrade(config, "base")
        assert not (set(inspect(engine).get_table_names()) & TABLES)
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) >= TABLES
    finally:
        engine.dispose()


@pytest.fixture
def migrated_engine(postgres_url: str) -> Iterator[Engine]:
    command.upgrade(migration_config(postgres_url), "head")
    engine = create_engine(postgres_url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO source_file (id, sha256, byte_size, locator)
            VALUES (:source, :sha, 10, 'aa/object');
        """),
            {"source": SOURCE, "sha": "a" * 64},
        )
        connection.execute(
            text("""
            INSERT INTO source_occurrence
            (id, source_file_id, filename, original_locator, actor_label,
             idempotency_key, ingested_at)
            VALUES (:occurrence, :source, 'sample.csv', '/sample.csv', 'operator',
                    'first', '2026-09-16T12:00:00Z')
        """),
            {"occurrence": OCCURRENCE, "source": SOURCE},
        )
        connection.execute(
            text("""
            INSERT INTO run (id, source_file_id, reprocess_sequence, state, created_at)
            VALUES (:run, :source, 0, 'ingested', '2026-09-16T12:00:00Z')
        """),
            {"run": RUN, "source": SOURCE},
        )
        connection.execute(
            text("""
            INSERT INTO run_source_occurrence
            (run_id, source_occurrence_id, relation, linked_at)
            VALUES (:run, :occurrence, 'initiated', '2026-09-16T12:00:00Z')
        """),
            {"run": RUN, "occurrence": OCCURRENCE},
        )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO source_file SELECT '00000000-0000-0000-0000-000000000099', "
        "sha256, byte_size, locator FROM source_file",
        "UPDATE source_file SET byte_size = -1",
        "UPDATE source_occurrence SET source_file_id = "
        "'00000000-0000-0000-0000-000000000099'",
        "INSERT INTO source_occurrence SELECT '00000000-0000-0000-0000-000000000099', "
        "source_file_id, filename, original_locator, actor_label, idempotency_key, "
        "ingested_at FROM source_occurrence",
        "INSERT INTO run (id, source_file_id, reprocess_sequence, state, created_at) "
        "SELECT '00000000-0000-0000-0000-000000000099', source_file_id, 0, "
        "'created', created_at FROM run",
        "UPDATE run SET state = 'unknown'",
        "UPDATE run SET predecessor_run_id = id",
        "INSERT INTO run_source_occurrence SELECT * FROM run_source_occurrence",
        "UPDATE run_source_occurrence SET relation = 'unknown'",
    ],
)
def test_rejects_invalid_identity_and_links(
    migrated_engine: Engine, statement: str
) -> None:
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(text(statement))


def test_reprocess_and_duplicate_links_remain_possible(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO run (id, source_file_id, predecessor_run_id,
                             reprocess_sequence, state, created_at)
            SELECT '00000000-0000-0000-0000-000000000099', source_file_id, id,
                   1, 'created', created_at FROM run
        """)
        )
        connection.execute(
            text("""
            INSERT INTO run_source_occurrence
            VALUES ('00000000-0000-0000-0000-000000000099', :occurrence,
                    'duplicate_upload', '2026-09-16T12:00:00Z')
        """),
            {"occurrence": OCCURRENCE},
        )
        assert connection.scalar(text("SELECT count(*) FROM run")) == 2


def test_raw_evidence_and_checkpoint_round_trip(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO raw_record
            (id, run_id, source_line_start, source_line_end, kind, fields,
             field_count, parse_metadata)
            VALUES (:raw, :run, 2, 3, 'data', '["customer", "a\\nb", ""]',
                    3, '{"quoted": true}')
        """),
            {"raw": RAW, "run": RUN},
        )
        connection.execute(
            text("""
            INSERT INTO pipeline_checkpoint
            (run_id, stage, batch_number, record_ordinal, last_record_id, updated_at)
            VALUES (:run, 'parse', 1, 1, :raw, '2026-09-16T12:00:00Z')
        """),
            {"raw": RAW, "run": RUN},
        )
        connection.execute(
            text("""
            INSERT INTO pipeline_event
            (id, run_id, stage, event_type, facts, occurred_at)
            VALUES (:raw, :run, 'parse', 'batch_committed', '{"records": 1}',
                    '2026-09-16T12:00:00Z')
        """),
            {"raw": RAW, "run": RUN},
        )
        row = connection.execute(
            text("SELECT fields, parse_metadata FROM raw_record")
        ).one()
        assert row[0] == ["customer", "a\nb", ""]
        assert row[1] == {"quoted": True}
        assert (
            connection.scalar(text("SELECT record_ordinal FROM pipeline_checkpoint"))
            == 1
        )


def test_models_match_migrated_schema(migrated_engine: Engine) -> None:
    from services.infrastructure.db.models import Base

    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def unused_repositories(session: Session) -> Repositories:
    # Concrete repositories are Task 4; this regression exercises the real ORM
    # session owned by the UoW, with stand-ins only for unused repository ports.
    return Repositories(
        sources=cast(SourceRepository, Mock(spec=SourceRepository)),
        runs=cast(RunRepository, Mock(spec=RunRepository)),
        raw_records=cast(RawRecordRepository, Mock(spec=RawRecordRepository)),
        checkpoints=cast(CheckpointRepository, Mock(spec=CheckpointRepository)),
        events=cast(EventRepository, Mock(spec=EventRepository)),
        candidates=SqlAlchemyCandidateRepository(session),
        classifications=SqlAlchemyClassificationRepository(session),
        reviews=SqlAlchemyReviewRepository(session),
        fx=SqlAlchemyFxRepository(session),
        canonicals=SqlAlchemyCanonicalRepository(session),
        decisions=SqlAlchemyDecisionRepository(session),
    )


@pytest.mark.parametrize(
    ("counts", "state", "expected_sql_null"),
    [
        (None, RunState.CREATED, True),
        ({"clean": 2, "needs_review": 1}, RunState.CLASSIFIED, False),
    ],
    ids=["pre_classification_sql_null", "classified_object_snapshot"],
)
def test_run_counts_round_trip_through_orm_unit_of_work(
    migrated_engine: Engine,
    counts: dict[str, int] | None,
    state: RunState,
    expected_sql_null: bool,
) -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000099")
    uow = SqlAlchemyUnitOfWork(sessionmaker(migrated_engine), unused_repositories)
    with uow:
        uow.session.add(
            RunModel(
                id=run_id,
                source_file_id=UUID(SOURCE),
                predecessor_run_id=UUID(RUN),
                reprocess_sequence=1,
                state=state,
                counts=counts,
                created_at=datetime(2026, 9, 16, 12, tzinfo=UTC),
            )
        )
        uow.commit()

    with uow:
        stored_run = uow.session.get(RunModel, run_id)
        assert stored_run is not None
        assert stored_run.counts == counts
        assert (
            uow.session.scalar(
                text("SELECT counts IS NULL FROM run WHERE id = :run_id"),
                {"run_id": run_id},
            )
            is expected_sql_null
        )
