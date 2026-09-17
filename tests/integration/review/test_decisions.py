"""DEC-01..07: PostgreSQL decisions, reversals, races and atomic promotion."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from services.application.decisions import (
    DecideReview,
    IdempotencyConflictError,
    IllegalDecisionError,
    StaleDecisionError,
    decide_review,
)
from services.application.load import stage_run
from services.domain.decisions import DecisionOutcome
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.runtime import _repositories
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.pipeline.test_classification_core import evidence, prepared
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock

REVIEW_PRODUCT = PRODUCT.replace("in_stock", "out_of_stock")


def uow_for(engine):
    return SqlAlchemyUnitOfWork(sessionmaker(engine), _repositories)


def setup_review(engine, tmp_path, csv=REVIEW_PRODUCT):
    run = prepared(engine, tmp_path, csv)
    summary = classify_run(run, default_registry(), uow_for(engine), FixedClock())
    item = summary.reviews[0]
    classification = next(r for r in summary.results if r.id == item.classification_id)
    return DecideReview(
        item.id,
        classification.candidate_revision_id,
        0,
        DecisionOutcome.APPROVE,
        "Alex",
        "Checked source",
        str(uuid4()),
    )


def snapshot(engine):
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all()
            for table in (
                "review_decision",
                "canonical_identity",
                "canonical_revision",
                "canonical_business_key",
                "canonical_promotion_event",
                "canonical_current",
            )
        }


def test_approval_new_identity_and_idempotency(engine, tmp_path):
    command = setup_review(engine, tmp_path)
    before = evidence(engine)
    result = decide_review(command, uow_for(engine), FixedClock())
    assert result.canonical_revision.revision_number == 1
    assert result.effective_state == "approved"
    assert not result.replayed
    with uow_for(engine) as uow:
        assert (
            uow.canonicals.current(result.canonical_revision.identity_id)
            == result.canonical_revision
        )
        assert (
            len(uow.canonicals.promotion_events(result.canonical_revision.identity_id))
            == 1
        )
    stable = snapshot(engine)
    replay = decide_review(command, uow_for(engine), FixedClock())
    assert replay == replace(result, replayed=True)
    with pytest.raises(IdempotencyConflictError):
        decide_review(
            replace(command, operator_name="Other"), uow_for(engine), FixedClock()
        )
    assert snapshot(engine) == stable
    assert evidence(engine) == before


@pytest.mark.parametrize("csv", ["UNKNOWN,x\n", PRODUCT + PRODUCT])
@pytest.mark.parametrize(
    "outcome", [DecisionOutcome.ACKNOWLEDGE, DecisionOutcome.REJECT]
)
def test_excluded_candidates_never_promote(engine, tmp_path, csv, outcome):
    command = setup_review(engine, tmp_path, csv)
    with pytest.raises(IllegalDecisionError):
        decide_review(command, uow_for(engine), FixedClock())
    result = decide_review(
        replace(command, outcome=outcome), uow_for(engine), FixedClock()
    )
    assert result.canonical_revision is None
    assert not snapshot(engine)["canonical_identity"]


def test_reject_and_reverse_rejection(engine, tmp_path):
    command = setup_review(engine, tmp_path)
    rejected = decide_review(
        replace(command, outcome=DecisionOutcome.REJECT), uow_for(engine), FixedClock()
    )
    assert rejected.canonical_revision is None
    approved = decide_review(
        replace(
            command,
            expected_sequence=1,
            idempotency_key="reverse",
            supersedes_decision_id=rejected.decision.id,
        ),
        uow_for(engine),
        FixedClock(),
    )
    assert approved.decision.sequence == 2
    assert approved.canonical_revision.revision_number == 1


@pytest.mark.parametrize("prior", [False, True])
def test_reverse_approval_restores_prior_current_and_replay_original_result(
    engine, tmp_path, prior
):
    if prior:
        run = prepared(engine, tmp_path / "prior", PRODUCT)
        classify_run(run, default_registry(), uow_for(engine), FixedClock())
        stage_run(run, lambda: uow_for(engine), FixedClock())
    command = setup_review(engine, tmp_path / "review")
    approved = decide_review(command, uow_for(engine), FixedClock())
    assert approved.canonical_revision.revision_number == 1 + int(prior)
    reversal = replace(
        command,
        outcome=DecisionOutcome.REJECT,
        expected_sequence=1,
        idempotency_key="reverse",
        supersedes_decision_id=approved.decision.id,
    )
    rejected = decide_review(reversal, uow_for(engine), FixedClock())
    with uow_for(engine) as uow:
        current = uow.canonicals.current(approved.canonical_revision.identity_id)
        assert (current.revision_number if current else None) == (1 if prior else None)
        history = uow.decisions.for_review(command.review_item_id)
        assert [d.sequence for d in history] == [1, 2]
    assert rejected.effective_state == "rejected"
    assert decide_review(command, uow_for(engine), FixedClock()) == replace(
        approved, replayed=True
    )
    restored = decide_review(
        replace(
            command,
            expected_sequence=2,
            idempotency_key="restore",
            supersedes_decision_id=rejected.decision.id,
        ),
        uow_for(engine),
        FixedClock(),
    )
    assert restored.canonical_revision == approved.canonical_revision


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_revision_id": uuid4()},
        {"expected_sequence": 1},
        {"supersedes_decision_id": uuid4()},
    ],
)
def test_stale_decision_changes_nothing(engine, tmp_path, change):
    command = setup_review(engine, tmp_path)
    before = snapshot(engine)
    with pytest.raises(StaleDecisionError):
        decide_review(replace(command, **change), uow_for(engine), FixedClock())
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "table",
    [
        "review_decision",
        "canonical_identity",
        "canonical_revision",
        "canonical_business_key",
        "canonical_promotion_event",
        "canonical_current",
    ],
)
def test_every_write_rolls_back_atomically(engine, tmp_path, table):
    command = setup_review(engine, tmp_path)
    before = snapshot(engine)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO " + table + " "):
            raise RuntimeError("injected write")

    event.listen(engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            decide_review(command, uow_for(engine), FixedClock())
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert snapshot(engine) == before


def test_concurrent_replay_and_stale_commands(engine, tmp_path):
    command = setup_review(engine, tmp_path)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: decide_review(command, uow_for(engine), FixedClock()),
                range(2),
            )
        )
    assert sorted(r.replayed for r in results) == [False, True]
    assert results[0].decision == results[1].decision
    with pytest.raises(StaleDecisionError):
        decide_review(
            replace(command, idempotency_key="another"), uow_for(engine), FixedClock()
        )


def test_activation_unblocks_clean_dependants_without_rewriting_evidence(
    engine, tmp_path
):
    run = prepared(engine, tmp_path, CUSTOMER + REVIEW_PRODUCT + ORDER)
    summary = classify_run(run, default_registry(), uow_for(engine), FixedClock())
    stage_run(run, lambda: uow_for(engine), FixedClock())
    before = evidence(engine)
    item = next(
        item
        for item in summary.reviews
        if next(r for r in summary.results if r.id == item.classification_id).verdict
        == "NEEDS_REVIEW"
    )
    classification = next(r for r in summary.results if r.id == item.classification_id)
    decide_review(
        DecideReview(
            item.id,
            classification.candidate_revision_id,
            0,
            DecisionOutcome.APPROVE,
            "Alex",
            None,
            "unblock",
        ),
        uow_for(engine),
        FixedClock(),
    )
    assert len(snapshot(engine)["canonical_current"]) == 3
    assert evidence(engine) == before


def test_concurrent_approvals_share_one_governed_identity(engine, tmp_path):
    commands = [
        setup_review(
            engine, tmp_path / str(i), REVIEW_PRODUCT.replace("Widget", f"Widget {i}")
        )
        for i in range(2)
    ]
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda c: decide_review(c, uow_for(engine), FixedClock()), commands
            )
        )
    assert len({r.canonical_revision.identity_id for r in results}) == 1
    assert {r.canonical_revision.revision_number for r in results} == {1, 2}
    with uow_for(engine) as uow:
        current = uow.canonicals.current(results[0].canonical_revision.identity_id)
        assert current.revision_number == 2


def test_old_approval_cannot_withdraw_a_later_current_revision(engine, tmp_path):
    command = setup_review(engine, tmp_path / "first")
    first = decide_review(command, uow_for(engine), FixedClock())
    second = setup_review(
        engine, tmp_path / "second", REVIEW_PRODUCT.replace("Widget", "New Widget")
    )
    decide_review(second, uow_for(engine), FixedClock())
    before = snapshot(engine)
    with pytest.raises(StaleDecisionError, match="later canonical"):
        decide_review(
            replace(
                command,
                expected_sequence=1,
                outcome=DecisionOutcome.REJECT,
                supersedes_decision_id=first.decision.id,
                idempotency_key="reverse",
            ),
            uow_for(engine),
            FixedClock(),
        )
    assert snapshot(engine) == before


@pytest.mark.parametrize("mutation", ["conflict", "withdraw"])
@pytest.mark.parametrize(
    "table", ["review_decision", "canonical_promotion_event", "canonical_current"]
)
def test_current_projection_changes_are_atomic(engine, tmp_path, mutation, table):
    command = setup_review(engine, tmp_path / "first")
    approved = decide_review(command, uow_for(engine), FixedClock())
    if mutation == "conflict":
        command = setup_review(
            engine, tmp_path / "next", REVIEW_PRODUCT.replace("Widget", "New Widget")
        )
    else:
        command = replace(
            command,
            expected_sequence=1,
            outcome=DecisionOutcome.REJECT,
            supersedes_decision_id=approved.decision.id,
            idempotency_key="reverse",
        )
    before = snapshot(engine)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if any(
            statement.startswith(prefix + table + " ")
            for prefix in ("INSERT INTO ", "UPDATE ", "DELETE FROM ")
        ):
            raise RuntimeError("injected mutation")

    event.listen(engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            decide_review(command, uow_for(engine), FixedClock())
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert snapshot(engine) == before


def test_unblocking_cannot_overwrite_a_new_governed_conflict(engine, tmp_path):
    pending_run = prepared(
        engine, tmp_path / "pending", CUSTOMER + REVIEW_PRODUCT + ORDER
    )
    pending = classify_run(
        pending_run, default_registry(), uow_for(engine), FixedClock()
    )
    stage_run(pending_run, lambda: uow_for(engine), FixedClock())
    later_run = prepared(
        engine,
        tmp_path / "later",
        CUSTOMER
        + PRODUCT.replace("SKU-2004", "SKU-2005")
        + ORDER.replace("SKU-2004", "SKU-2005"),
    )
    classify_run(later_run, default_registry(), uow_for(engine), FixedClock())
    stage_run(later_run, lambda: uow_for(engine), FixedClock())
    with uow_for(engine) as uow:
        order_key = uow.canonicals.get_key("order", "ORD-3001")
        previous_order = uow.canonicals.current(order_key.identity_id)
    review = next(r for r in pending.results if r.verdict == "NEEDS_REVIEW")
    item = next(i for i in pending.reviews if i.classification_id == review.id)
    decide_review(
        DecideReview(
            item.id,
            review.candidate_revision_id,
            0,
            DecisionOutcome.APPROVE,
            "Alex",
            None,
            "unblock-conflict",
        ),
        uow_for(engine),
        FixedClock(),
    )
    with uow_for(engine) as uow:
        assert uow.canonicals.current(order_key.identity_id) == previous_order


def test_blocked_dependency_scan_serializes_rechecks(engine, tmp_path):
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from services.infrastructure.db.models import ClassificationResultModel

    run = prepared(engine, tmp_path, CUSTOMER + REVIEW_PRODUCT + ORDER)
    classify_run(run, default_registry(), uow_for(engine), FixedClock())
    with uow_for(engine) as uow:
        blocked = uow.classifications.blocked()
        assert blocked
        with Session(engine) as other, pytest.raises(OperationalError):
            other.execute(
                select(ClassificationResultModel)
                .where(ClassificationResultModel.id == blocked[0].id)
                .with_for_update(nowait=True)
            )
