"""Inactive governed identities remain usable by clean loading and cascades."""

from dataclasses import replace
from datetime import timedelta

import pytest

from services.application.decisions import DecideReview, decide_review
from services.application.load import stage_run
from services.domain.canonical import PromotionAction
from services.domain.decisions import DecisionOutcome
from services.domain.issues import Readiness, Verdict
from services.domain.runs import RunState
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.pipeline.test_classification_core import evidence, prepared
from tests.integration.review.test_decisions import (
    REVIEW_PRODUCT,
    setup_review,
    snapshot,
    uow_for,
)
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock


class LaterClock(FixedClock):
    def now(self):
        return super().now() + timedelta(days=1)


def withdraw_approval(engine, command):
    approved = decide_review(command, uow_for(engine), FixedClock())
    decide_review(
        replace(
            command,
            outcome=DecisionOutcome.REJECT,
            reason="Withdraw accepted observation",
            expected_sequence=1,
            supersedes_decision_id=approved.decision.id,
            idempotency_key=command.idempotency_key + "-withdraw",
        ),
        uow_for(engine),
        FixedClock(),
    )
    with uow_for(engine) as uow:
        assert uow.canonicals.current(approved.canonical_revision.identity_id) is None
    return approved


@pytest.mark.parametrize(
    ("csv", "verdict"),
    [
        (PRODUCT, Verdict.CLEAN),
        (PRODUCT.replace("SKU-2004", "SKU-00204"), Verdict.AUTO_REPAIRED),
    ],
)
@pytest.mark.parametrize("retry", [False, True])
def test_load_reuses_withdrawn_identity_and_replays_without_rewriting_history(
    engine, tmp_path, csv, verdict, retry
):
    command = setup_review(engine, tmp_path / "review")
    approved = withdraw_approval(engine, command)
    original = approved.canonical_revision
    history = snapshot(engine)
    run = prepared(engine, tmp_path / "new", csv)
    summary = classify_run(run, default_registry(), uow_for(engine), LaterClock())
    (classification,) = summary.results
    assert (classification.verdict, classification.readiness) == (
        verdict,
        Readiness.ELIGIBLE,
    )
    before = evidence(engine)
    if retry:

        def fail(number):
            raise RuntimeError("injected retained identity load")

        with pytest.raises(RuntimeError, match="injected retained"):
            stage_run(run, lambda: uow_for(engine), LaterClock(), fail)
        assert snapshot(engine) == history
        with uow_for(engine) as uow:
            failed = uow.runs.get(run)
            assert (failed.state, failed.stage_failure) == (
                RunState.CLASSIFIED,
                "load_failed",
            )
    assert stage_run(run, lambda: uow_for(engine), LaterClock()).staged_count == 1
    with uow_for(engine) as uow:
        current = uow.canonicals.current(original.identity_id)
        assert current.candidate_revision_id == classification.candidate_revision_id
        assert current.revision_number == 2
        assert current.staged_at == LaterClock().now()
        assert uow.canonicals.get_revision(original.id) == original
        events = uow.canonicals.promotion_events(original.identity_id)
        (activation,) = [e for e in events if e.canonical_revision_id == current.id]
        assert activation.action is PromotionAction.ACTIVATE
        assert activation.decision_id is None
        assert activation.prior_current_revision_id is None
    stable = snapshot(engine)
    assert len(stable["canonical_revision"]) == 2
    assert len(stable["canonical_promotion_event"]) == 3
    for table, rows in history.items():
        assert all(row in stable[table] for row in rows)
    assert stable["canonical_identity"] == history["canonical_identity"]
    assert stable["canonical_business_key"] == history["canonical_business_key"]
    assert stage_run(run, lambda: uow_for(engine), LaterClock()).staged_count == 1
    assert decide_review(command, uow_for(engine), LaterClock()) == replace(
        approved, replayed=True
    )
    assert snapshot(engine) == stable
    assert evidence(engine) == before


def test_dependency_cascade_reuses_withdrawn_identity(engine, tmp_path):
    prior = prepared(
        engine,
        tmp_path / "prior",
        CUSTOMER + PRODUCT + ORDER.replace("shipped", "unknown"),
    )
    prior_summary = classify_run(
        prior, default_registry(), uow_for(engine), FixedClock()
    )
    stage_run(prior, lambda: uow_for(engine), FixedClock())
    review = next(r for r in prior_summary.results if r.verdict is Verdict.NEEDS_REVIEW)
    item = next(i for i in prior_summary.reviews if i.classification_id == review.id)
    approved = withdraw_approval(
        engine,
        DecideReview(
            item.id,
            review.candidate_revision_id,
            0,
            DecisionOutcome.APPROVE,
            "Alex",
            None,
            "prior-order",
        ),
    )
    history = snapshot(engine)
    run = prepared(
        engine,
        tmp_path / "new",
        CUSTOMER
        + REVIEW_PRODUCT.replace("SKU-2004", "SKU-2005")
        + ORDER.replace("SKU-2004", "SKU-2005"),
    )
    summary = classify_run(run, default_registry(), uow_for(engine), LaterClock())
    stage_run(run, lambda: uow_for(engine), LaterClock())
    blocked = next(
        r for r in summary.results if r.readiness is Readiness.BLOCKED_BY_DEPENDENCY
    )
    assert blocked.verdict is Verdict.CLEAN
    before = evidence(engine)
    review = next(r for r in summary.results if r.verdict is Verdict.NEEDS_REVIEW)
    item = next(i for i in summary.reviews if i.classification_id == review.id)
    command = DecideReview(
        item.id,
        review.candidate_revision_id,
        0,
        DecisionOutcome.APPROVE,
        "Alex",
        None,
        "new-product",
    )
    decide_review(command, uow_for(engine), LaterClock())
    with uow_for(engine) as uow:
        original = approved.canonical_revision
        current = uow.canonicals.current(original.identity_id)
        assert current is not None
        assert current.candidate_revision_id == blocked.candidate_revision_id
        assert current.revision_number == 2
        assert uow.canonicals.get_revision(original.id) == original
        assert len(uow.canonicals.promotion_events(original.identity_id)) == 3
    stable = snapshot(engine)
    for table, rows in history.items():
        assert all(row in stable[table] for row in rows)
    assert stage_run(run, lambda: uow_for(engine), LaterClock()).staged_count == 0
    assert decide_review(command, uow_for(engine), LaterClock()).replayed
    assert snapshot(engine) == stable
    assert evidence(engine) == before


def test_clean_load_cannot_overwrite_a_reactivated_identity(engine, tmp_path):
    approved = withdraw_approval(engine, setup_review(engine, tmp_path / "review"))
    runs = []
    for label, csv in (
        ("first", PRODUCT),
        ("second", PRODUCT.replace("Widget", "Other Widget")),
    ):
        run = prepared(engine, tmp_path / label, csv)
        summary = classify_run(run, default_registry(), uow_for(engine), LaterClock())
        assert summary.results[0].readiness is Readiness.ELIGIBLE
        runs.append(run)
    stage_run(runs[0], lambda: uow_for(engine), LaterClock())
    before = snapshot(engine)
    with pytest.raises(ValueError, match="already active"):
        stage_run(runs[1], lambda: uow_for(engine), LaterClock())
    assert snapshot(engine) == before
    with uow_for(engine) as uow:
        current = uow.canonicals.current(approved.canonical_revision.identity_id)
        assert current.revision_number == 2
        assert uow.runs.get(runs[1]).stage_failure == "load_failed"
