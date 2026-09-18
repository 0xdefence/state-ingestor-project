"""Real classified reviews expose exactly the domain's manual decision policy."""

import asyncio

import httpx
import pytest

from services.api.app import create_app
from services.application.queries import (
    CurrentFileScope,
    ReviewDetailQuery,
    ReviewQueueQuery,
)
from services.application.query_services import get_review_detail, get_review_queue
from services.infrastructure.db.read_repository import SqlAlchemyReadRepository
from services.infrastructure.runtime import Settings
from services.pipeline.classify import classify_run
from services.pipeline.rules.registry import default_registry
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.pipeline.test_classification_core import prepared
from tests.integration.review.test_decisions import uow_for
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, FixedClock


@pytest.mark.parametrize(
    "csv,verdict,expected",
    [
        (CUSTOMER.replace(",active,", ",unknown,") + PRODUCT + ORDER, "CLEAN", ()),
        (
            CUSTOMER.replace(",active,", ",unknown,")
            + PRODUCT
            + ORDER.replace("SKU-2004", "SKU-00204"),
            "AUTO_REPAIRED",
            (),
        ),
        (
            PRODUCT.replace("in_stock", "out_of_stock"),
            "NEEDS_REVIEW",
            ("approve", "reject"),
        ),
        ("UNKNOWN,x\n", "REJECTED", ("acknowledge", "reject")),
        (PRODUCT + PRODUCT, "DUPLICATE", ("acknowledge", "reject")),
    ],
    ids=["clean_waiting", "repaired_waiting", "needs_review", "rejected", "duplicate"],
)
def test_projection_and_http_options_match_reachable_verdict(
    engine, tmp_path, csv, verdict, expected
):
    run_id = prepared(engine, tmp_path, csv)
    summary = classify_run(run_id, default_registry(), uow_for(engine), FixedClock())
    classification = next(
        r
        for r in summary.results
        if r.verdict == verdict
        and any(review.classification_id == r.id for review in summary.reviews)
    )
    review = next(
        r for r in summary.reviews if r.classification_id == classification.id
    )
    repository = SqlAlchemyReadRepository(engine)
    detail = get_review_detail(repository, ReviewDetailQuery(review.id))
    assert detail.allowed_outcomes == expected
    queue = get_review_queue(repository, ReviewQueueQuery(CurrentFileScope(run_id)))
    row = next(r for r in queue.items if r.id == review.id)
    if not expected:
        assert row.readiness == "blocked_by_dependency"
        assert row.effective_state == "pending"
        assert row.readiness_label == "Waiting for another record"
        assert row.effective_state_label == "Awaiting review"
        assert row.current_status == "blocked_by_dependency"
        assert row.current_status_label == "Waiting for another record"
        assert detail.item.current_status_label == "Waiting for another record"

    async def scenario():
        app = create_app(
            settings=Settings(
                database_url=engine.url.render_as_string(hide_password=False),
                source_root=tmp_path,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/reviews/{review.id}")
            assert response.status_code == 200
            assert response.json()["allowed_outcomes"] == list(expected)
            if not expected:
                assert (
                    response.json()["item"]["current_status_label"]
                    == "Waiting for another record"
                )
                for outcome in ("approve", "reject", "acknowledge"):
                    response = await client.post(
                        f"/api/reviews/{review.id}/decisions",
                        json={
                            "candidate_revision_id": str(
                                classification.candidate_revision_id
                            ),
                            "expected_sequence": 0,
                            "outcome": outcome,
                            "operator_name": "Alex",
                            "reason": "Not actionable",
                            "idempotency_key": outcome,
                        },
                    )
                    assert response.status_code == 422
                    assert response.json()["error"]["code"] == "illegal_decision"

    asyncio.run(scenario())
