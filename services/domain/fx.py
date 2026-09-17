"""Immutable, dependency-free ECB snapshot and conversion evidence values."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


def _decimal(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("FX rates require finite Decimal values")
    return value


@dataclass(frozen=True, slots=True)
class FxRate:
    currency: str
    publication_date: date
    eur_reference_rate: Decimal
    source_url: str

    def __post_init__(self) -> None:
        if not re.fullmatch("[A-Z]{3}", self.currency):
            raise ValueError("FX currency must be an uppercase ISO code")
        if _decimal(self.eur_reference_rate) <= 0:
            raise ValueError("FX reference rate must be positive")
        if self.currency == "EUR" and self.eur_reference_rate != Decimal("1"):
            raise ValueError("EUR reference rate must be one")
        if not self.source_url:
            raise ValueError("FX source provenance is required")


@dataclass(frozen=True, slots=True)
class FxSnapshot:
    id: UUID
    manifest_hash: str
    effective_at: datetime
    source: str
    source_url: str
    rates: tuple[FxRate, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch("[0-9a-f]{64}", self.manifest_hash):
            raise ValueError("FX manifest hash must be SHA-256")
        if self.effective_at.utcoffset() is None:
            raise ValueError("FX snapshot requires an aware effective timestamp")
        if self.source != "ECB" or not self.source_url:
            raise ValueError("FX snapshot requires ECB provenance")
        keys = {(rate.publication_date, rate.currency) for rate in self.rates}
        if len(keys) != len(self.rates):
            raise ValueError("duplicate FX publication/currency")
        if any(rate.source_url != self.source_url for rate in self.rates):
            raise ValueError("FX rate provenance must match snapshot")

    def rate_on_or_before(self, currency: str, requested_date: date) -> FxRate | None:
        return max(
            (
                rate
                for rate in self.rates
                if rate.currency == currency and rate.publication_date <= requested_date
            ),
            key=lambda rate: rate.publication_date,
            default=None,
        )


@dataclass(frozen=True, slots=True)
class FxEvidence:
    source_amount: Decimal
    source_currency: str
    gbp_amount: Decimal | None
    rate: Decimal | None
    publication_date: date | None
    requested_date: date
    source: str
    source_url: str
    snapshot_id: UUID
    manifest_hash: str
    operation: str
    precision: int
    rounding: str
