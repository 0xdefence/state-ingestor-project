"""Framework-free query entry points; snapshot ownership belongs to the port."""

from services.application.queries import (
    ReviewDetailQuery,
    ReviewQueueQuery,
    RunDetailQuery,
    WorkspaceQuery,
)
from services.application.read_ports import ReadRepository
from services.application.views import (
    ReviewDetailView,
    ReviewQueueView,
    RunDetailView,
    WorkspaceView,
)


def get_workspace(repo: ReadRepository, query: WorkspaceQuery) -> WorkspaceView:
    return repo.workspace(query)


def get_run_detail(repo: ReadRepository, query: RunDetailQuery) -> RunDetailView:
    return repo.run_detail(query)


def get_review_queue(repo: ReadRepository, query: ReviewQueueQuery) -> ReviewQueueView:
    return repo.review_queue(query)


def get_review_detail(
    repo: ReadRepository, query: ReviewDetailQuery
) -> ReviewDetailView:
    return repo.review_detail(query)
