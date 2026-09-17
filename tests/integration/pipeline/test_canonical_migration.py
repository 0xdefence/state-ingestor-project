"""Canonical schema migration, model parity and validated lineage constraints."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from tests.integration.foundation.test_migrations import (
    migrated_engine as migrated_engine,
)
from tests.integration.foundation.test_migrations import migration_config
from tests.integration.foundation.test_migrations import postgres_url as postgres_url


def test_canonical_migration_roundtrip_and_parity(postgres_url):
    from services.infrastructure.db.models import Base

    config = migration_config(postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    tables = {
        "canonical_identity",
        "canonical_revision",
        "canonical_business_key",
        "reobservation_link",
    }
    try:
        assert tables <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            assert (
                compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
            )
        fks = inspect(engine).get_foreign_keys("dependency_record")
        assert any(fk["referred_table"] == "canonical_identity" for fk in fks)
        command.downgrade(config, "0003_fx_snapshots")
        assert not tables.intersection(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert tables <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_dependency_fk_validates_existing_evidence(migrated_engine):
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from tests.integration.pipeline.test_derived_migration import (
        test_derived_evidence_round_trip_and_rollback,
    )

    test_derived_evidence_round_trip_and_rollback(migrated_engine)
    config = migration_config(migrated_engine.url.render_as_string(hide_password=False))
    command.downgrade(config, "0003_fx_snapshots")
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dependency_record SET state='resolved', "
                "resolved_entity_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'"
            )
        )
    with pytest.raises(IntegrityError):
        command.upgrade(config, "head")
    with migrated_engine.connect() as conn:
        assert (
            str(conn.scalar(text("SELECT resolved_entity_id FROM dependency_record")))
            == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        )
