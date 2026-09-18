"""Approval and dependent-key contention must share one transaction lock order."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from sqlalchemy.orm import sessionmaker

from services.application.decisions import DecideReview, decide_review
from services.application.load import stage_run
from services.domain.decisions import DecisionOutcome
from services.domain.issues import Readiness, Verdict
from services.infrastructure.db.canonical_repository import (
    SqlAlchemyCanonicalRepository,
)
from services.infrastructure.db.decision_repository import SqlAlchemyDecisionRepository
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyClassificationRepository,
)
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.runtime import _repositories
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.pipeline.test_classification_core import evidence, prepared
from tests.integration.review.test_decisions import REVIEW_PRODUCT, uow_for
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock


def test_parent_cascade_and_reviewed_dependant_key_complete_without_deadlock(
    engine, tmp_path
):
    commands = []
    for label, csv in (
        ("parent", CUSTOMER + REVIEW_PRODUCT + ORDER),
        (
            "order",
            CUSTOMER
            + PRODUCT.replace("SKU-2004", "SKU-2005")
            + ORDER.replace("SKU-2004", "SKU-2005").replace("shipped", "unknown"),
        ),
    ):
        run = prepared(engine, tmp_path / label, csv)
        summary = classify_run(run, default_registry(), uow_for(engine), FixedClock())
        stage_run(run, lambda: uow_for(engine), FixedClock())
        classification = next(r for r in summary.results if r.verdict == "NEEDS_REVIEW")
        item = next(
            i for i in summary.reviews if i.classification_id == classification.id
        )
        commands.append(
            DecideReview(
                item.id,
                classification.candidate_revision_id,
                0,
                DecisionOutcome.APPROVE,
                "Alex",
                None,
                label,
            )
        )
    before = evidence(engine)
    parent_scanning = Event()
    order_at_canonical_boundary = Event()

    def factory(label):
        class Decisions(SqlAlchemyDecisionRepository):
            def lock_idempotency(self, key):
                if label == "order":
                    assert parent_scanning.wait(5)
                super().lock_idempotency(key)

        class Canonicals(SqlAlchemyCanonicalRepository):
            def lock_promotions(self):
                if label == "order":
                    order_at_canonical_boundary.set()
                super().lock_promotions()

            def activate(self, promotion):
                super().activate(promotion)
                if label == "order":
                    # Before the fix this is reached while owning the order key.
                    order_at_canonical_boundary.set()

        class Classifications(SqlAlchemyClassificationRepository):
            def blocked(self):
                rows = super().blocked()
                if label == "parent":
                    parent_scanning.set()
                    # After the fix the other command reaches its serialization
                    # boundary, before it can acquire the dependent order key.
                    assert order_at_canonical_boundary.wait(5)
                return rows

        def repositories(session):
            return replace(
                _repositories(session),
                decisions=Decisions(session),
                canonicals=Canonicals(session),
                classifications=Classifications(session),
            )

        return SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)

    def submit(command):
        try:
            return decide_review(
                command, factory(command.idempotency_key), FixedClock()
            )
        except Exception as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit, command) for command in commands]
        results = [future.result(timeout=15) for future in futures]
    assert not any(isinstance(result, Exception) for result in results), results
    assert all(result.effective_state == "approved" for result in results)
    with uow_for(engine) as uow:
        key = uow.canonicals.get_key("order", "ORD-3001")
        current = uow.canonicals.current(key.identity_id)
        assert current.candidate_revision_id == commands[1].candidate_revision_id
        assert current.revision_number == 2
        assert len(uow.canonicals.promotion_events(key.identity_id)) == 2
        assert all(
            len(uow.decisions.for_review(command.review_item_id)) == 1
            for command in commands
        )
    assert evidence(engine) == before


def test_concurrent_parent_approvals_activate_the_shared_dependant(engine, tmp_path):
    run = prepared(
        engine,
        tmp_path,
        CUSTOMER.replace(",active,", ",unknown,") + REVIEW_PRODUCT + ORDER,
    )
    summary = classify_run(run, default_registry(), uow_for(engine), FixedClock())
    blocked = next(
        r for r in summary.results if r.readiness is Readiness.BLOCKED_BY_DEPENDENCY
    )
    commands = []
    for result in summary.results:
        if result.verdict is Verdict.NEEDS_REVIEW:
            item = next(i for i in summary.reviews if i.classification_id == result.id)
            commands.append(
                DecideReview(
                    item.id,
                    result.candidate_revision_id,
                    0,
                    DecisionOutcome.APPROVE,
                    "Alex",
                    None,
                    str(len(commands)),
                )
            )
    assert len(commands) == 2
    before = evidence(engine)
    first_scanning = Event()
    second_waiting = Event()

    def factory(first):
        class Canonicals(SqlAlchemyCanonicalRepository):
            def lock_promotions(self):
                if not first:
                    assert first_scanning.wait(5)
                    second_waiting.set()
                super().lock_promotions()

        class Classifications(SqlAlchemyClassificationRepository):
            def blocked(self):
                rows = super().blocked()
                if first:
                    first_scanning.set()
                    assert second_waiting.wait(5)
                return rows

        def repositories(session):
            return replace(
                _repositories(session),
                canonicals=Canonicals(session),
                classifications=Classifications(session),
            )

        return SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)

    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(decide_review, command, factory(i == 0), FixedClock())
            for i, command in enumerate(commands)
        ]
        results = [future.result(timeout=15) for future in futures]
    assert all(result.effective_state == "approved" for result in results)
    with uow_for(engine) as uow:
        dependant = uow.canonicals.for_candidate(blocked.candidate_revision_id)
        assert dependant is not None
        assert uow.canonicals.current(dependant.identity_id) == dependant
        assert len(uow.canonicals.promotion_events(dependant.identity_id)) == 1
    assert evidence(engine) == before
