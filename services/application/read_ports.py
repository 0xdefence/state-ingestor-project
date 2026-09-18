"""Aggregate reads are one consistent snapshot, independent of transport."""

from typing import Protocol

from services.application.queries import (
    ReviewDetailQuery,
    ReviewQueueQuery,
    RunDetailQuery,
    WorkspaceQuery,
)
from services.application.views import (
    ReviewDetailView,
    ReviewQueueView,
    RunDetailView,
    WorkspaceView,
)


class ReadRepository(Protocol):
    def workspace(self, query: WorkspaceQuery) -> WorkspaceView: ...
    def run_detail(self, query: RunDetailQuery) -> RunDetailView: ...
    def review_queue(self, query: ReviewQueueQuery) -> ReviewQueueView: ...
    def review_detail(self, query: ReviewDetailQuery) -> ReviewDetailView: ...
