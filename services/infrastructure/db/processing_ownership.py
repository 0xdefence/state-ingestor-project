"""PostgreSQL command ownership independent of short stage transactions."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class PostgresProcessingOwnership:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    @contextmanager
    def hold(self, run_id: UUID) -> Generator[None]:
        # A namespaced 64-bit key avoids sharing the canonical lock namespace.
        # A hash collision only serializes unrelated runs; it cannot lose ownership.
        key = int.from_bytes(
            sha256(f"pipeline-processing:{run_id}".encode()).digest()[:8],
            signed=True,
        )
        # This dedicated transaction never performs pipeline writes or commits.
        # PostgreSQL releases xact locks on rollback, disconnect, or worker crash;
        # Session.close rolls back before returning the connection to its pool.
        with self._sessions() as owner:
            owner.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            yield
