from uuid import UUID

from fastapi import APIRouter

from services.api.dependencies import (
    ReaderDependency,
    RuntimeDependency,
    ScopeDependency,
)
from services.api.models import DecisionBody
from services.api.presenters import present
from services.application.decisions import decide_review
from services.application.queries import ReviewDetailQuery, ReviewQueueQuery
from services.application.query_services import get_review_detail, get_review_queue
from services.domain.decisions import EffectiveReviewState
from services.domain.issues import Verdict

router = APIRouter()


@router.get("/reviews")
def reviews(
    repo: ReaderDependency,
    scope: ScopeDependency,
    effective_state: EffectiveReviewState | None = None,
    verdict: Verdict | None = None,
) -> object:
    with repo as opened:
        return present(
            get_review_queue(opened, ReviewQueueQuery(scope, effective_state, verdict))
        )


@router.get("/reviews/{review_item_id}")
def review_detail(review_item_id: UUID, repo: ReaderDependency) -> object:
    with repo as opened:
        return present(get_review_detail(opened, ReviewDetailQuery(review_item_id)))


@router.post("/reviews/{review_item_id}/decisions")
def decision(
    review_item_id: UUID, body: DecisionBody, runtime: RuntimeDependency
) -> object:
    command = body.command(review_item_id)
    with runtime as opened:
        return present(decide_review(command, opened.uow_factory(), opened.clock))
