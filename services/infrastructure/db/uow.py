"""Explicit transactions over a single SQLAlchemy session per context."""

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session

from services.application.canonical_ports import CanonicalRepository
from services.application.decision_ports import DecisionRepository
from services.application.derived_ports import (
    CandidateRepository,
    ClassificationRepository,
    ReviewRepository,
)
from services.application.fx_ports import FxRepository
from services.application.ports import (
    CheckpointRepository,
    EventRepository,
    RawRecordRepository,
    Repositories,
    RunRepository,
    SourceRepository,
)
from services.infrastructure.db.processing_ownership import PostgresProcessingOwnership


class SqlAlchemyUnitOfWork:
    """Bind supplied command repositories to the context's single session."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        repository_factory: Callable[[Session], Repositories],
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._session: Session | None = None
        self._repositories: Repositories | None = None
        self._committed = False
        self.processing = PostgresProcessingOwnership(session_factory)

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        return self._session

    @property
    def repositories(self) -> Repositories:
        if self._repositories is None:
            raise RuntimeError("Unit of work is not active")
        return self._repositories

    @property
    def sources(self) -> SourceRepository:
        return self.repositories.sources

    @property
    def runs(self) -> RunRepository:
        return self.repositories.runs

    @property
    def raw_records(self) -> RawRecordRepository:
        return self.repositories.raw_records

    @property
    def checkpoints(self) -> CheckpointRepository:
        return self.repositories.checkpoints

    @property
    def events(self) -> EventRepository:
        return self.repositories.events

    @property
    def candidates(self) -> CandidateRepository:
        return self.repositories.candidates

    @property
    def classifications(self) -> ClassificationRepository:
        return self.repositories.classifications

    @property
    def reviews(self) -> ReviewRepository:
        return self.repositories.reviews

    @property
    def decisions(self) -> DecisionRepository:
        return self.repositories.decisions

    @property
    def canonicals(self) -> CanonicalRepository:
        return self.repositories.canonicals

    @property
    def fx(self) -> FxRepository:
        return self.repositories.fx

    def __enter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Unit of work is already active")
        self._committed = False
        self._session = self._session_factory()
        try:
            self._repositories = self._repository_factory(self._session)
        except BaseException:
            self._session.close()
            self._session = None
            raise
        return self

    def commit(self) -> None:
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        self.session.rollback()
        self._committed = False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            if not self._committed:
                self.rollback()
        finally:
            try:
                session.close()
            finally:
                self._session = None
                self._repositories = None
