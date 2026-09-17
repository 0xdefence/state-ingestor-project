"""Offline import integrity, immutable replay and PostgreSQL FX schema."""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from tests.integration.foundation.test_concurrent_ingest import repositories
from tests.integration.foundation.test_migrations import (
    migrated_engine as migrated_engine,
)
from tests.integration.foundation.test_migrations import migration_config
from tests.integration.foundation.test_migrations import postgres_url as postgres_url
from tests.unit.normalise.test_fx import FIXTURE, MANIFEST


def uow(engine: Engine) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)


def test_fx_schema_upgrade_downgrade_and_model_parity(postgres_url: str) -> None:
    from services.infrastructure.db.models import Base

    config = migration_config(postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        assert {"fx_snapshot", "fx_rate"} <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert (
                compare_metadata(MigrationContext.configure(connection), Base.metadata)
                == []
            )
        fks = inspect(engine).get_foreign_keys("classification_result")
        assert any(fk["referred_table"] == "fx_snapshot" for fk in fks)
        command.downgrade(config, "0002_candidates_classification")
        assert "fx_snapshot" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_offline_import_is_idempotent_and_lookup_is_date_bound(
    migrated_engine: Engine,
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    first = import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    assert import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine)) == first
    with uow(migrated_engine) as work:
        assert work.fx.get(first.id) == first
        rate = work.fx.rate_on_or_before(first.id, "GBP", date(2023, 4, 2))
        assert rate is not None
        assert rate.publication_date == date(2023, 3, 31)
        assert rate.eur_reference_rate == Decimal("0.8792")
        assert work.fx.rate_on_or_before(first.id, "USD", date(1900, 1, 1)) is None
        with pytest.raises(ValueError, match="immutable"):
            work.fx.add(replace(first, rates=first.rates[:-1]))
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 1
        assert connection.scalar(text("SELECT count(*) FROM fx_rate")) == len(
            first.rates
        )


def test_bad_fixture_hash_has_no_writes(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    bad = tmp_path / "bad.csv"
    bad.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash"):
        import_fx_snapshot(bad, MANIFEST, lambda: uow(migrated_engine))
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 0
        assert connection.scalar(text("SELECT count(*) FROM fx_rate")) == 0


def test_same_manifest_hash_requires_complete_existing_rates(
    migrated_engine: Engine,
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    snapshot = import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    with migrated_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM fx_rate WHERE snapshot_id=:id AND publication_date=:day"),
            {"id": snapshot.id, "day": snapshot.rates[0].publication_date},
        )
    with pytest.raises(ValueError, match="immutable"):
        import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))


def test_fx_writes_rollback_as_one_transaction(migrated_engine: Engine) -> None:
    from services.infrastructure.fx_importer import read_fx_snapshot

    snapshot = read_fx_snapshot(FIXTURE, MANIFEST)
    with uow(migrated_engine) as work:
        work.fx.add(snapshot)
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 0
        assert connection.scalar(text("SELECT count(*) FROM fx_rate")) == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fx_rate SET eur_reference_rate=0",
        "UPDATE fx_rate SET eur_reference_rate=-1",
        "UPDATE fx_rate SET eur_reference_rate='NaN'",
        "UPDATE fx_rate SET currency='gbp'",
        "UPDATE fx_rate SET snapshot_id='00000000-0000-0000-0000-000000000999'",
        "UPDATE fx_snapshot SET manifest_hash='x'",
        "UPDATE fx_snapshot SET source='other'",
        "INSERT INTO fx_rate SELECT * FROM fx_rate LIMIT 1",
    ],
)
def test_fx_database_constraints(migrated_engine: Engine, statement: str) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(text(statement))


def test_classification_fx_fk_rejects_dangling_snapshot(
    migrated_engine: Engine,
) -> None:
    from tests.integration.pipeline.test_derived_migration import (
        test_derived_evidence_round_trip_and_rollback,
    )

    test_derived_evidence_round_trip_and_rollback(migrated_engine)
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE classification_result SET fx_snapshot_id=:id"),
            {"id": UUID(int=999)},
        )


def test_manifest_coverage_is_checked_before_writes(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    data = json.loads(MANIFEST.read_bytes())
    data["coverage"]["end"] = "2099-01-01"
    changed = tmp_path / "manifest.json"
    changed.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="coverage"):
        import_fx_snapshot(FIXTURE, changed, lambda: uow(migrated_engine))
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 0


def test_conversion_evidence_round_trips_with_source_and_snapshot(
    migrated_engine: Engine,
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot
    from services.pipeline.fx import convert_to_gbp
    from tests.integration.pipeline.test_derived_migration import (
        test_derived_evidence_round_trip_and_rollback,
    )
    from tests.unit.normalise.test_candidates import REFS, revision

    test_derived_evidence_round_trip_and_rollback(migrated_engine)
    snapshot = import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    candidate = revision()
    conversion = convert_to_gbp(Decimal("19.99"), "USD", date(2024, 2, 3), snapshot)
    event = conversion.transformation_for_revision(
        candidate.id, "product.unit_price", REFS, sequence=2
    )
    assert event is not None
    missing = convert_to_gbp(Decimal("100"), "JPY", date(2024, 2, 3), snapshot)
    issue = missing.issue_for_revision(candidate.id, "product.unit_price", REFS)
    assert issue is not None
    with uow(migrated_engine) as work:
        work.candidates.add_transformation(event)
        work.candidates.add_issue(issue)
        work.commit()
    with uow(migrated_engine) as work:
        stored = work.candidates.transformations(candidate.id)[1]
        assert stored == event
        assert stored.after.value == conversion.evidence
        assert issue in work.candidates.issues(candidate.id)
        assert conversion.evidence.source_amount == Decimal("19.99")
        assert conversion.evidence.publication_date == date(2024, 2, 2)
        assert conversion.evidence.snapshot_id == snapshot.id
        with pytest.raises(ValueError, match="identity mismatch"):
            work.candidates.add_transformation(replace(event, after=event.before))


@pytest.mark.parametrize("field,value", [("version", True), ("byte_size", True)])
def test_manifest_rejects_bool_as_integer(
    migrated_engine: Engine, tmp_path: Path, field: str, value: bool
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    data = json.loads(MANIFEST.read_bytes())
    data[field] = value
    changed = tmp_path / "manifest.json"
    changed.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        import_fx_snapshot(FIXTURE, changed, lambda: uow(migrated_engine))
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 0


def test_concurrent_imports_share_one_complete_snapshot(
    migrated_engine: Engine,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from services.domain.fx import FxSnapshot
    from services.infrastructure.fx_importer import import_fx_snapshot

    def import_one() -> FxSnapshot:
        return import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(import_one)
        right = executor.submit(import_one)
        assert left.result(timeout=10) == right.result(timeout=10)
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM fx_snapshot")) == 1
        assert connection.scalar(text("SELECT count(*) FROM fx_rate")) == 1768


@pytest.mark.parametrize("changed_side", ["stored", "supplied"])
def test_replay_rejects_changed_decimal_scale(
    migrated_engine: Engine,
    changed_side: str,
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    pinned = import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    assert pinned.rates[0].currency == "GBP"
    assert pinned.rates[0].publication_date == date(2023, 3, 30)
    assert (
        pinned.rates[0].eur_reference_rate.as_tuple() == Decimal("0.88164").as_tuple()
    )
    changed_rate = Decimal("0.881640")
    if changed_side == "stored":
        with migrated_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE fx_rate SET eur_reference_rate=:rate "
                    "WHERE snapshot_id=:id AND currency='GBP' "
                    "AND publication_date='2023-03-30'"
                ),
                {"rate": changed_rate, "id": pinned.id},
            )
        with uow(migrated_engine) as work:
            persisted = work.fx.get(pinned.id).rates[0].eur_reference_rate
            assert persisted.as_tuple() == Decimal("0.881640").as_tuple()
    else:
        pinned = replace(
            pinned,
            rates=(
                replace(pinned.rates[0], eur_reference_rate=changed_rate),
                *pinned.rates[1:],
            ),
        )
    with uow(migrated_engine) as work:
        with pytest.raises(ValueError, match="immutable"):
            work.fx.add(pinned)


def test_replay_canonicalizes_rate_order_without_losing_identity(
    migrated_engine: Engine,
) -> None:
    from services.infrastructure.fx_importer import import_fx_snapshot

    pinned = import_fx_snapshot(FIXTURE, MANIFEST, lambda: uow(migrated_engine))
    with uow(migrated_engine) as work:
        work.fx.add(replace(pinned, rates=tuple(reversed(pinned.rates))))
        work.commit()
    with uow(migrated_engine) as work:
        stored = work.fx.get(pinned.id)
        assert [
            (r.publication_date, r.currency, r.eur_reference_rate.as_tuple())
            for r in stored.rates
        ] == [
            (r.publication_date, r.currency, r.eur_reference_rate.as_tuple())
            for r in pinned.rates
        ]
        # A different publication is different evidence, even with the same value.
        changed_identity = replace(pinned.rates[0], publication_date=date(2023, 3, 29))
        with pytest.raises(ValueError, match="immutable"):
            work.fx.add(replace(pinned, rates=(changed_identity, *pinned.rates[1:])))
        with pytest.raises(ValueError, match="immutable"):
            work.fx.add(replace(pinned, rates=(changed_identity, *pinned.rates)))


def test_snapshot_read_keeps_utc_run_date_across_session_timezones(
    migrated_engine: Engine,
) -> None:
    from datetime import UTC, datetime

    from services.infrastructure.fx_importer import read_fx_snapshot
    from services.pipeline.fx import convert_lifetime_spend_to_gbp

    pinned = replace(
        read_fx_snapshot(FIXTURE, MANIFEST),
        effective_at=datetime(2026, 9, 16, 23, 30, tzinfo=UTC),
    )
    with uow(migrated_engine) as work:
        work.fx.add(pinned)
        work.commit()
    with uow(migrated_engine) as work:
        work.session.execute(text("SET LOCAL TIME ZONE 'Europe/London'"))
        stored = work.fx.get(pinned.id)
        converted = convert_lifetime_spend_to_gbp(Decimal("100"), "USD", stored)
        assert converted.requested_date == date(2026, 9, 16)
        assert stored.effective_at.isoformat() == "2026-09-16T23:30:00+00:00"
        work.fx.add(pinned)
