"""Inward-facing FX persistence contract; the caller owns the transaction."""

from datetime import date
from typing import Protocol
from uuid import UUID

from services.domain.fx import FxRate, FxSnapshot


class FxRepository(Protocol):
    def add(self, snapshot: FxSnapshot) -> None: ...
    def get(self, snapshot_id: UUID) -> FxSnapshot: ...
    def latest(self) -> FxSnapshot: ...
    def rate_on_or_before(
        self,
        snapshot_id: UUID,
        currency: str,
        requested_date: date,
    ) -> FxRate | None: ...
