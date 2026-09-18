"""Exclusive, transport-independent file scopes and immutable read requests."""

from dataclasses import dataclass
from uuid import UUID

from services.application.errors import ApplicationValidationError
from services.domain.decisions import EffectiveReviewState
from services.domain.issues import Verdict


@dataclass(frozen=True, slots=True)
class CurrentFileScope:
    run_id: UUID


@dataclass(frozen=True, slots=True)
class SelectedFilesScope:
    run_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if not self.run_ids:
            raise ApplicationValidationError("selected scope requires at least one run")
        object.__setattr__(self, "run_ids", tuple(dict.fromkeys(self.run_ids)))


@dataclass(frozen=True, slots=True)
class AllFilesScope:
    pass


type FileScope = CurrentFileScope | SelectedFilesScope | AllFilesScope


@dataclass(frozen=True, slots=True)
class WorkspaceQuery:
    scope: FileScope


@dataclass(frozen=True, slots=True)
class RunDetailQuery:
    run_id: UUID


@dataclass(frozen=True, slots=True)
class ReviewQueueQuery:
    scope: FileScope
    effective_state: EffectiveReviewState | None = None
    verdict: Verdict | None = None


@dataclass(frozen=True, slots=True)
class ReviewDetailQuery:
    review_item_id: UUID
