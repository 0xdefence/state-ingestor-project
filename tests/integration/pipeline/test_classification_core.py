"""Task 5 transaction and complete-evidence replay contracts on PostgreSQL."""

from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from services.application.ingest import IngestFile, ingest_file
from services.application.normalise import normalise_run
from services.application.process import parse_run
from services.domain.fields import SourceRef
from services.domain.runs import RunState
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyClassificationRepository,
    SqlAlchemyReviewRepository,
)
from services.infrastructure.db.models import (
    CandidateRevisionModel,
    ClassificationResultModel,
    DataQualityIssueModel,
    DependencyRecordModel,
    ReviewItemModel,
    RunModel,
    TransformationEventModel,
)
from services.infrastructure.source_store import FilesystemSourceStore
from services.pipeline.normalise.candidates import NormaliseContext
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.unit.classify.test_rules import (
    CUSTOMER,
    NOW,
    ORDER,
    PRODUCT,
    SNAPSHOT,
    FixedClock,
)


def prepared(engine, tmp_path, csv=CUSTOMER + PRODUCT.replace(",5,", ",0,") + ORDER):
    store = FilesystemSourceStore(tmp_path)
    run = ingest_file(
        IngestFile(BytesIO(csv.encode()), "rules.csv", "fixture", "test"),
        uow_for(engine),
        store,
        FixedClock(),
    )
    parse_run(run.run_id, 10, lambda: uow_for(engine), store, clock=FixedClock())
    normalise_run(
        run.run_id, 10, lambda: uow_for(engine), NormaliseContext(FixedClock())
    )
    with uow_for(engine) as uow:
        uow.fx.add(SNAPSHOT)
        uow.commit()
    with Session(engine) as session:
        stored = session.get(RunModel, run.run_id)
        stored.fx_snapshot_id = SNAPSHOT.id
        stored.build_revision = "separate-build-revision"
        session.commit()
    return run.run_id


def evidence(engine):
    with Session(engine) as session:
        return {
            model.__tablename__: sorted(
                (
                    {
                        column.name: getattr(row, column.name)
                        for column in model.__table__.columns
                    }
                    for row in session.scalars(select(model))
                ),
                key=lambda value: str(value["id"]),
            )
            for model in (
                CandidateRevisionModel,
                ClassificationResultModel,
                DataQualityIssueModel,
                DependencyRecordModel,
                ReviewItemModel,
                TransformationEventModel,
            )
        }


def test_classification_persists_complete_idempotent_evidence(
    engine: Engine, tmp_path: Path
):
    from services.pipeline.classify import classify_run
    from services.pipeline.rules.registry import default_registry

    run_id = prepared(engine, tmp_path)
    registry = default_registry()
    outcome = classify_run(run_id, registry, uow_for(engine), FixedClock())
    before = evidence(engine)
    with uow_for(engine) as check:
        for revision in outcome.graph.revisions:
            from services.infrastructure.db.derived_codec import (
                evidence_equal,
                object_json,
            )

            stored_revision = check.candidates.get(revision.id)
            assert (
                stored_revision.created_at.isoformat()
                == revision.created_at.isoformat()
            )
            assert object_json(stored_revision.payload) == object_json(revision.payload)
            assert evidence_equal(stored_revision, revision), (
                stored_revision,
                revision,
            )
    repeated = classify_run(run_id, registry, uow_for(engine), FixedClock())
    assert outcome == repeated
    assert evidence(engine) == before
    with Session(engine) as session:
        run = session.get(RunModel, run_id)
        assert run.state == RunState.CLASSIFIED
        assert run.rules_version == registry.rules_version
        assert run.build_revision == "separate-build-revision"
        assert run.counts == outcome.counts
    assert all(d.resolved_entity_id is None for d in outcome.dependencies)
    # Repository replay verifies every column, including time and dependency state.
    with Session(engine) as session:
        repo = SqlAlchemyClassificationRepository(session)
        with pytest.raises(ValueError, match="identity mismatch"):
            repo.add(
                replace(outcome.results[0], evaluated_at=NOW + timedelta(seconds=1))
            )
        with pytest.raises(ValueError, match="identity mismatch"):
            repo.add_dependency(
                replace(outcome.dependencies[0], referenced_business_value="changed")
            )
        review_repo = SqlAlchemyReviewRepository(session)
        with pytest.raises(ValueError, match="identity mismatch"):
            review_repo.add(
                replace(outcome.reviews[0], created_at=NOW + timedelta(seconds=1))
            )


def test_review_replay_evidence_is_type_sensitive(engine: Engine, tmp_path: Path):
    from services.pipeline.classify import classify_run
    from services.pipeline.rules.registry import default_registry

    run_id = prepared(engine, tmp_path)
    outcome = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    review = outcome.reviews[0]
    reason = replace(
        review.reasons[0], source_refs=(SourceRef(review.raw_record_id, True),)
    )
    with Session(engine) as session:
        # Change one fresh deterministic review identity to a synthetic evidence probe.
        session.execute(delete(ReviewItemModel).where(ReviewItemModel.id == review.id))
        stored = replace(review, reasons=(reason,))
        repo = SqlAlchemyReviewRepository(session)
        repo.add(stored)
        equal_in_python = replace(
            stored,
            reasons=(
                replace(reason, source_refs=(SourceRef(review.raw_record_id, 1),)),
            ),
        )
        with pytest.raises(ValueError, match="identity mismatch"):
            repo.add(equal_in_python)


def test_classification_failure_rolls_back_all_writes(
    engine: Engine, tmp_path: Path, monkeypatch
):
    from services.pipeline.classify import classify_run
    from services.pipeline.rules.registry import default_registry

    run_id = prepared(engine, tmp_path)
    before = evidence(engine)

    def fail(self, review):
        raise RuntimeError("injected review failure")

    monkeypatch.setattr(SqlAlchemyReviewRepository, "add", fail)
    with pytest.raises(RuntimeError, match="injected"):
        classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert evidence(engine) == before
    with Session(engine) as session:
        assert session.get(RunModel, run_id).state == RunState.NORMALISED
