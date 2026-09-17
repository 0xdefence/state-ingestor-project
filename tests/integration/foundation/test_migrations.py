"""Exercise migrations and invariants against isolated Postgres databases."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

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
