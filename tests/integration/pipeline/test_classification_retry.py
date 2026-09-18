"""CLS-07..12/LOD-09 persistence and transaction failure/replay contracts."""

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from services.domain.issues import ComparisonScope, Verdict
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
    DuplicateRelationModel,
    RawRecordModel,
    ReviewItemModel,
    RunModel,
    TransformationEventModel,
)
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.integration.pipeline.test_classification_core import prepared
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock


def snapshot(engine):
    with Session(engine) as session:
        return {
            model.__tablename__: sorted(
                (
                    {c.name: getattr(row, c.name) for c in model.__table__.columns}
                    for row in session.scalars(select(model))
                ),
                key=lambda row: str(row["id"]),
            )
            for model in (
                RunModel,
                RawRecordModel,
                CandidateRevisionModel,
                ClassificationResultModel,
                DataQualityIssueModel,
                DependencyRecordModel,
                DuplicateRelationModel,
                ReviewItemModel,
                TransformationEventModel,
            )
        }


def test_classification_retry_does_not_duplicate_results(
    engine: Engine, tmp_path: Path
):
    csv = CUSTOMER + PRODUCT + ORDER + ORDER + ORDER.replace(",19.99,", ",20.00,")
    run_id = prepared(engine, tmp_path, csv)
    before = snapshot(engine)
    first = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert [r.verdict for r in first.results][-2:] == [
        Verdict.DUPLICATE,
        Verdict.NEEDS_REVIEW,
    ]
    after = snapshot(engine)
    assert after["raw_record"] == before["raw_record"]
    assert all(
        row in after["candidate_revision"] for row in before["candidate_revision"]
    )
    assert len(after["duplicate_relation"]) == 1
    assert after["duplicate_relation"][0]["comparison_scope"] == "same_run"
    assert len(after["review_item"]) == 2
    repeated = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert repeated == first
    assert snapshot(engine) == after
    assert after["run"][0]["build_revision"] == "separate-build-revision"
    assert after["run"][0]["rules_version"] == default_registry().rules_version


def test_classification_failure_after_duplicate_insert_rolls_back_then_retries(
    engine: Engine, tmp_path: Path, monkeypatch
):
    run_id = prepared(engine, tmp_path, CUSTOMER + CUSTOMER)
    before = snapshot(engine)
    original = SqlAlchemyReviewRepository.add

    def fail(self, review):
        original(self, review)
        raise RuntimeError("injected after review write")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyReviewRepository, "add", fail)
        with pytest.raises(RuntimeError, match="injected"):
            classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    expected = {
        **before,
        "run": [{**before["run"][0], "stage_failure": "classify_failed"}],
    }
    assert snapshot(engine) == expected
    result = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert result.results[1].verdict == Verdict.DUPLICATE
    after = snapshot(engine)
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert snapshot(engine) == after


def test_duplicate_replay_compares_complete_scope(engine: Engine, tmp_path: Path):
    run_id = prepared(engine, tmp_path, CUSTOMER + CUSTOMER)
    outcome = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    (relation,) = outcome.graph.duplicates
    with Session(engine) as session:
        repo = SqlAlchemyClassificationRepository(session)
        with pytest.raises(ValueError, match="identity mismatch"):
            repo.add_duplicate(
                replace(relation, comparison_scope=ComparisonScope.EARLIER_RUN)
            )


@pytest.mark.parametrize(
    "model,field,value",
    [
        (ClassificationResultModel, "verdict", "NEEDS_REVIEW"),
        (DataQualityIssueModel, "tentative_cause", "changed evidence"),
        (DuplicateRelationModel, "comparison_scope", "earlier_run"),
        (ReviewItemModel, "reasons", "changed reason"),
    ],
)
def test_complete_evidence_mismatch_rolls_back_new_rows(
    engine: Engine, tmp_path: Path, model, field, value
):
    run_id = prepared(engine, tmp_path, CUSTOMER + CUSTOMER)
    classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    with Session(engine) as session:
        row = session.scalars(select(model)).first()
        if model is ReviewItemModel:
            row.reasons = [{**row.reasons[0], "summary": value}]
        else:
            setattr(row, field, value)
        # Force new issue writes before replay reaches the tampered object.
        if model is not DataQualityIssueModel:
            session.execute(delete(DataQualityIssueModel))
        session.commit()
    before = snapshot(engine)
    with pytest.raises(ValueError, match="identity mismatch|stable reasons"):
        classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert snapshot(engine) == before


def test_retry_of_initial_terminal_with_classification_issues(
    engine: Engine, tmp_path: Path
):
    # No valid money -> FX does not append a revision. Classifier evidence shares
    # the initial revision, and must not be mistaken for normalization input.
    run_id = prepared(engine, tmp_path, PRODUCT.replace(",19.99,", ",TBD,") * 2)
    first = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    before = snapshot(engine)
    second = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    assert second == first
    assert snapshot(engine) == before
    assert before["run"][0]["state"] == RunState.CLASSIFIED
