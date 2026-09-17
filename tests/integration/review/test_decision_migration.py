"""Decision constraints, model parity and preservation of existing canonical rows."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from services.application.decisions import decide_review
from services.application.load import stage_run
from services.infrastructure.db.models import Base
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_migrations import migration_config
from tests.integration.pipeline.test_classification_core import prepared
from tests.integration.review.test_decisions import setup_review, uow_for
from tests.unit.classify.test_rules import CUSTOMER, PRODUCT, FixedClock


def test_migration_backfills_existing_canonicals_and_roundtrips(engine, tmp_path):
    run = prepared(engine, tmp_path, CUSTOMER + PRODUCT)
    classify_run(run, default_registry(), uow_for(engine), FixedClock())
    stage_run(run, lambda: uow_for(engine), FixedClock())
    decide_review(
        setup_review(engine, tmp_path / "conflict"), uow_for(engine), FixedClock()
    )
    config = migration_config(engine.url.render_as_string(hide_password=False))
    command.downgrade(config, "0004_canonical_staging")
    assert "canonical_current" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        revisions = conn.execute(
            text(
                "SELECT DISTINCT ON (identity_id) identity_id,id "
                "FROM canonical_revision ORDER BY identity_id,revision_number DESC"
            )
        ).all()
        assert (
            conn.execute(
                text(
                    "SELECT identity_id,canonical_revision_id "
                    "FROM canonical_current ORDER BY identity_id"
                )
            ).all()
            == revisions
        )
        events = conn.execute(
            text(
                "SELECT id,canonical_revision_id,action,decision_id "
                "FROM canonical_promotion_event"
            )
        ).all()
        assert conn.scalar(text("SELECT count(*) FROM canonical_revision")) == 3
        assert len(events) == 2
        assert all(
            id == revision
            and id.version == 4
            and action == "activate"
            and decision is None
            for id, revision, action, decision in events
        )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        assert conn.scalar(text("SELECT count(*) FROM canonical_current")) == 0


@pytest.mark.parametrize(
    "assignment",
    [
        "sequence=0",
        "outcome='bogus'",
        "operator_name=' '",
        "idempotency_key=' '",
        "outcome='reject',reason=NULL",
        "outcome='reject',reason=' '",
        "id='aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa'",
    ],
)
def test_database_rejects_invalid_decisions(engine, tmp_path, assignment):
    decide_review(setup_review(engine, tmp_path), uow_for(engine), FixedClock())
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE review_decision SET " + assignment))


def test_supersession_cannot_reference_another_review(engine, tmp_path):
    first = decide_review(
        setup_review(engine, tmp_path / "first"), uow_for(engine), FixedClock()
    )
    second = decide_review(
        setup_review(
            engine,
            tmp_path / "second",
            PRODUCT.replace("SKU-2004", "SKU-2005").replace("in_stock", "out_of_stock"),
        ),
        uow_for(engine),
        FixedClock(),
    )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE review_decision SET supersedes_decision_id=:first "
                "WHERE id=:second"
            ),
            {"first": first.decision.id, "second": second.decision.id},
        )
