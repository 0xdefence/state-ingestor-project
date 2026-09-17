"""Canonical append history, evidence replay and governed relational constraints."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.application.load import stage_run
from services.domain.canonical import (
    CanonicalBusinessKey,
    CanonicalIdentity,
    CanonicalPromotionEvent,
    CanonicalRevision,
    PromotionAction,
)
from services.infrastructure.db.models import (
    CanonicalRevisionModel,
)
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.integration.pipeline.test_classification_core import prepared
from tests.unit.classify.test_rules import NOW, PRODUCT, FixedClock


def staged(engine, tmp_path):
    run_id = prepared(engine, tmp_path, PRODUCT)
    summary = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    stage_run(run_id, lambda: uow_for(engine), FixedClock())
    return run_id, summary.graph.terminal[0]


def test_canonical_replay_compares_every_evidence_field(engine, tmp_path):
    _, candidate = staged(engine, tmp_path)
    with uow_for(engine) as work:
        revision = work.canonicals.for_candidate(candidate.id)
        identity = CanonicalIdentity(revision.identity_id, "product", NOW)
        key = CanonicalBusinessKey(identity.id, "product", "SKU-2004", revision.id)
        work.canonicals.add_identity(identity)
        work.canonicals.add_revision(revision)
        work.canonicals.add_key(key)
        for changed in (
            replace(identity, entity_type="customer"),
            replace(identity, created_at=NOW + timedelta(seconds=1)),
        ):
            with pytest.raises(ValueError, match="identity mismatch"):
                work.canonicals.add_identity(changed)
        for changed in (
            replace(revision, identity_id=uuid4()),
            replace(revision, candidate_revision_id=uuid4()),
            replace(revision, revision_number=2),
            replace(revision, staged_at=NOW + timedelta(seconds=1)),
        ):
            with pytest.raises(ValueError, match="identity mismatch"):
                work.canonicals.add_revision(changed)
        for changed in (
            replace(key, identity_id=uuid4()),
            replace(key, effective_revision=uuid4()),
        ):
            with pytest.raises(ValueError, match="identity mismatch"):
                work.canonicals.add_key(changed)


def test_canonical_history_reader_returns_current_typed_revision(engine, tmp_path):
    first, original = staged(engine, tmp_path / "first")
    other = prepared(
        engine, tmp_path / "other", PRODUCT.replace("Widget", "Updated Widget")
    )
    summary = classify_run(other, default_registry(), uow_for(engine), FixedClock())
    current = summary.graph.terminal[0]
    with uow_for(engine) as work:
        old = work.canonicals.for_candidate(original.id)
        successor = CanonicalRevision(uuid4(), old.identity_id, current.id, 2, NOW)
        with pytest.raises(ValueError, match="consecutive"):
            work.canonicals.add_revision(replace(successor, revision_number=3))
        work.canonicals.add_revision(successor)
        # Appending history alone no longer replaces the current projection.
        assert work.canonicals.current(old.identity_id) == old
        work.canonicals.activate(
            CanonicalPromotionEvent(
                uuid4(),
                old.identity_id,
                successor.id,
                PromotionAction.ACTIVATE,
                None,
                old.id,
                NOW,
            )
        )
        work.commit()
    with uow_for(engine) as work:
        (observation,) = work.canonicals.prior_observations()
        assert observation.canonical_revision_id == successor.id
        assert observation.run_id == other
        assert observation.candidate == current
        assert work.canonicals.for_candidate(original.id) == old
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM canonical_identity")) == 1
        assert conn.scalar(text("SELECT count(*) FROM canonical_revision")) == 2
        assert (
            conn.scalar(text("SELECT effective_revision FROM canonical_business_key"))
            == old.id
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE canonical_identity SET entity_type='unknown'",
        "UPDATE canonical_identity SET id='aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa'",
        "UPDATE canonical_revision SET id='aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa'",
        "UPDATE canonical_revision SET revision_number=0",
        "UPDATE canonical_revision SET "
        "candidate_revision_id='aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa'",
        "UPDATE canonical_revision SET "
        "identity_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'",
        "UPDATE canonical_business_key SET value=''",
        "UPDATE canonical_business_key SET key_type='unknown'",
        "UPDATE canonical_business_key SET "
        "effective_revision='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'",
        "UPDATE canonical_business_key SET "
        "identity_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'",
        "INSERT INTO canonical_revision SELECT 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',"
        "identity_id,candidate_revision_id,revision_number,staged_at "
        "FROM canonical_revision",
    ],
)
def test_canonical_schema_constraints(engine, tmp_path, statement):
    staged(engine, tmp_path)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text(statement))


def test_composite_key_lineage_cannot_point_at_other_identity_revision(
    engine, tmp_path
):
    staged(engine, tmp_path / "first")
    run_id = prepared(
        engine, tmp_path / "other", PRODUCT.replace("SKU-2004", "SKU-2005")
    )
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    stage_run(run_id, lambda: uow_for(engine), FixedClock())
    with Session(engine) as session:
        revisions = list(session.scalars(select(CanonicalRevisionModel)))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE canonical_business_key SET effective_revision=:other "
                "WHERE identity_id=:identity"
            ),
            {"other": revisions[1].id, "identity": revisions[0].identity_id},
        )
