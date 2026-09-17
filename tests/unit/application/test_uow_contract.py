"""Real local SQL transactions exercise the unit-of-work lifecycle without network."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.application.ports import Repositories, UnitOfWork
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'transactions.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE evidence (value INTEGER NOT NULL)"))
    yield engine
    engine.dispose()


def repositories(session: Session) -> Repositories:
    # Command repositories arrive in Task 4; only the transaction boundary is tested.
    return cast(Repositories, Mock(spec=Repositories))


def count_rows(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.scalar(text("SELECT count(*) FROM evidence")) or 0)


def test_exit_without_commit_rolls_back(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    with uow:
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
    assert count_rows(engine) == 0
    with pytest.raises(RuntimeError, match="active"):
        _ = uow.session


def test_explicit_commit_persists(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    contract: UnitOfWork = uow
    with uow:
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
        contract.commit()
    assert count_rows(engine) == 1


def test_exception_rolls_back_and_propagates(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    with pytest.raises(ValueError, match="batch failed"), uow:
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
        raise ValueError("batch failed")
    assert count_rows(engine) == 0


def test_explicit_rollback_discards_changes(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    with uow:
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
        uow.rollback()
        uow.commit()
    assert count_rows(engine) == 0


def test_reenter_opens_fresh_session_and_resets_commit(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    with uow:
        first_session = uow.session
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
        uow.commit()
    with uow:
        assert uow.session is not first_session
        uow.session.execute(text("INSERT INTO evidence VALUES (2)"))
    assert count_rows(engine) == 1


def test_nested_enter_is_rejected_without_losing_transaction(engine: Engine) -> None:
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine), repositories)
    with uow:
        uow.session.execute(text("INSERT INTO evidence VALUES (1)"))
        with pytest.raises(RuntimeError, match="active"):
            uow.__enter__()
        uow.commit()
    assert count_rows(engine) == 1


def test_close_runs_even_when_rollback_fails(engine: Engine) -> None:
    class FailingRollbackSession(Session):
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

    session = FailingRollbackSession(engine)
    uow = SqlAlchemyUnitOfWork(lambda: session, repositories)
    with pytest.raises(RuntimeError, match="rollback failed"), uow:
        session.execute(text("INSERT INTO evidence VALUES (1)"))
    assert not session.in_transaction()
    assert count_rows(engine) == 0


def test_repository_setup_failure_closes_session(engine: Engine) -> None:
    session = Session(engine)

    def failing_repositories(session: Session) -> Repositories:
        session.execute(text("INSERT INTO evidence VALUES (1)"))
        raise RuntimeError("setup failed")

    uow = SqlAlchemyUnitOfWork(lambda: session, failing_repositories)
    with pytest.raises(RuntimeError, match="setup failed"):
        uow.__enter__()
    assert not session.in_transaction()
    assert count_rows(engine) == 0
