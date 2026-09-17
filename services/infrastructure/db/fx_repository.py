"""Immutable snapshot writes and publication-bounded Decimal rate reads."""

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.domain.fx import FxRate, FxSnapshot
from services.infrastructure.db.models import FxRateModel, FxSnapshotModel


def _rate(row: FxRateModel) -> FxRate:
    return FxRate(
        row.currency, row.publication_date, row.eur_reference_rate, row.source_url
    )


class SqlAlchemyFxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, snapshot_id: UUID) -> FxSnapshot:
        row = self._session.get(FxSnapshotModel, snapshot_id)
        if row is None:
            raise LookupError("FX snapshot not found")
        rates = self._session.scalars(
            select(FxRateModel)
            .where(FxRateModel.snapshot_id == snapshot_id)
            .order_by(FxRateModel.publication_date, FxRateModel.currency)
        )
        return FxSnapshot(
            row.id,
            row.manifest_hash,
            row.effective_at,
            row.source,
            row.source_url,
            tuple(_rate(rate) for rate in rates),
        )

    def add(self, snapshot: FxSnapshot) -> None:
        # ON CONFLICT serializes concurrent imports on the immutable manifest key.
        created = self._session.scalar(
            insert(FxSnapshotModel)
            .values(
                id=snapshot.id,
                manifest_hash=snapshot.manifest_hash,
                effective_at=snapshot.effective_at,
                source=snapshot.source,
                source_url=snapshot.source_url,
            )
            .on_conflict_do_nothing()
            .returning(FxSnapshotModel.id)
        )
        if created is None:
            existing_id = self._session.scalar(
                select(FxSnapshotModel.id).where(
                    FxSnapshotModel.manifest_hash == snapshot.manifest_hash
                )
            )
            if existing_id is None or self.get(existing_id) != snapshot:
                raise ValueError("immutable FX snapshot/rates mismatch")
            return
        self._session.add_all(
            [
                FxRateModel(
                    snapshot_id=snapshot.id,
                    currency=rate.currency,
                    publication_date=rate.publication_date,
                    eur_reference_rate=rate.eur_reference_rate,
                    source_url=rate.source_url,
                )
                for rate in snapshot.rates
            ]
        )
        self._session.flush()

    def rate_on_or_before(
        self,
        snapshot_id: UUID,
        currency: str,
        requested_date: date,
    ) -> FxRate | None:
        row = self._session.scalar(
            select(FxRateModel)
            .where(
                FxRateModel.snapshot_id == snapshot_id,
                FxRateModel.currency == currency,
                FxRateModel.publication_date <= requested_date,
            )
            .order_by(FxRateModel.publication_date.desc())
            .limit(1)
        )
        return _rate(row) if row is not None else None
