"""Local runtime configuration and concrete port composition for adapters."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import DEFAULT_BUILD_REVISION
from services.application.ports import Clock, Repositories, SourceStore
from services.application.process import UnitOfWorkFactory
from services.application.read_ports import ReadRepository
from services.infrastructure.db.canonical_repository import (
    SqlAlchemyCanonicalRepository,
)
from services.infrastructure.db.decision_repository import SqlAlchemyDecisionRepository
from services.infrastructure.db.derived_repositories import (
    SqlAlchemyCandidateRepository,
    SqlAlchemyClassificationRepository,
    SqlAlchemyReviewRepository,
)
from services.infrastructure.db.fx_repository import SqlAlchemyFxRepository
from services.infrastructure.db.read_repository import SqlAlchemyReadRepository
from services.infrastructure.db.repositories import (
    SqlAlchemyCheckpointRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyRawRecordRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySourceRepository,
)
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.fx_importer import import_fx_snapshot
from services.infrastructure.source_store import FilesystemSourceStore

DEFAULT_DATABASE_URL = "postgresql+psycopg://alexis:alexis@localhost:55432/alexis"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    database_url: str = DEFAULT_DATABASE_URL
    source_root: Path = Path("var/sources")
    build_revision: str = Field(default=DEFAULT_BUILD_REVISION, min_length=1)

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value: str) -> str:
        try:
            url = make_url(value)
        except (ArgumentError, ValueError) as error:
            raise ValueError("Use a PostgreSQL URL with the psycopg driver") from error
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("Use postgresql+psycopg://.../<database>")
        return value

    @field_validator("source_root", mode="before")
    @classmethod
    def validate_root_value(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("Source root must not be empty")
        return value

    @field_validator("source_root")
    @classmethod
    def validate_root(cls, value: Path) -> Path:
        root = value.expanduser().absolute()
        ancestor = root
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if not ancestor.is_dir():
            raise ValueError("Source root must be a directory, with directory parents")
        if not os.access(ancestor, os.W_OK | os.X_OK):
            raise ValueError("Source root must be writable and accessible")
        return root


@dataclass(frozen=True, slots=True)
class Runtime:
    uow_factory: UnitOfWorkFactory
    source_store: SourceStore
    clock: Clock
    read_repository: ReadRepository | None = None
    build_revision: str = DEFAULT_BUILD_REVISION


class RuntimeConfigurationError(Exception):
    """An actionable runtime error safe to display without database credentials."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _repositories(session: Session) -> Repositories:
    return Repositories(
        SqlAlchemySourceRepository(session),
        SqlAlchemyRunRepository(session),
        SqlAlchemyRawRecordRepository(session),
        SqlAlchemyCheckpointRepository(session),
        SqlAlchemyEventRepository(session),
        SqlAlchemyCandidateRepository(session),
        SqlAlchemyClassificationRepository(session),
        SqlAlchemyReviewRepository(session),
        SqlAlchemyFxRepository(session),
        SqlAlchemyCanonicalRepository(session),
        SqlAlchemyDecisionRepository(session),
    )


@contextmanager
def build_runtime(
    settings: Settings, *, clock: Clock | None = None
) -> Generator[Runtime]:
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    try:
        sessions = sessionmaker(engine)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(sessions, _repositories)

        root = Path(__file__).resolve().parents[2]
        import_fx_snapshot(
            root / "data/fx/ecb-history.csv",
            root / "data/fx/manifest.json",
            uow_factory,
        )

        yield Runtime(
            uow_factory,
            FilesystemSourceStore(settings.source_root),
            clock if clock is not None else SystemClock(),
            SqlAlchemyReadRepository(engine),
            settings.build_revision,
        )
    except SQLAlchemyError as error:
        raise RuntimeConfigurationError(
            "Database operation failed. Check DATABASE_URL, PostgreSQL availability "
            "and that migrations have been applied."
        ) from error
    finally:
        engine.dispose()
