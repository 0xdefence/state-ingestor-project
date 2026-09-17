"""FX-01..05: reproducible Decimal conversion and retained audit evidence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from services.domain.fx import FxSnapshot

FIXTURE = Path("data/fx/ecb-history.csv")
MANIFEST = Path("data/fx/manifest.json")
FIXED_TIME = datetime(2026, 9, 16, 12, tzinfo=UTC)


def test_fx_manifest_matches_fixture() -> None:
    manifest = json.loads(MANIFEST.read_bytes())
    assert sha256(FIXTURE.read_bytes()).hexdigest() == manifest["sha256"]
    assert manifest["source"] == "ECB"
    assert manifest["coverage"]["currencies"] == ["GBP", "USD"]
    assert manifest["coverage"]["start"] <= "2023-04-02"
    assert manifest["coverage"]["end"] >= "2026-09-16"


def snapshot() -> FxSnapshot:
    from services.domain.fx import FxRate, FxSnapshot

    return FxSnapshot(
        UUID(int=1),
        "a" * 64,
        FIXED_TIME,
        "ECB",
        "https://www.ecb.europa.eu/test",
        tuple(
            FxRate(currency, day, Decimal(rate), "https://www.ecb.europa.eu/test")
            for day, gbp, usd in (
                (date(2024, 2, 2), "0.85", "1.10"),
                (date(2026, 9, 16), "0.90", "1.20"),
            )
            for currency, rate in (("GBP", gbp), ("USD", usd))
        ),
    )


def test_cross_rate_uses_euro_reference_rates() -> None:
    from services.pipeline.fx import convert_to_gbp

    result = convert_to_gbp(Decimal("110"), "USD", date(2024, 2, 2), snapshot())
    assert result.rate == Decimal("0.7727272727272727272727272727")
    assert result.gbp.value == Decimal("85.00")
    # Caller decimal context cannot silently change stored conversion evidence.
    with localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = "ROUND_DOWN"
        assert (
            convert_to_gbp(Decimal("110"), "USD", date(2024, 2, 2), snapshot())
            == result
        )


def test_weekend_uses_latest_earlier_rate() -> None:
    from services.pipeline.fx import convert_to_gbp

    result = convert_to_gbp(Decimal("110"), "USD", date(2024, 2, 3), snapshot())
    assert result.publication_date == date(2024, 2, 2)
    assert result.gbp.value == Decimal("85.00")
    assert (
        convert_to_gbp(Decimal("110"), "USD", date(2024, 2, 1), snapshot()).rate is None
    )


def test_order_uses_order_date() -> None:
    from services.domain.fields import SourceRef
    from services.domain.ids import deterministic_id
    from services.pipeline.fx import convert_to_gbp

    result = convert_to_gbp(Decimal("110"), "USD", date(2024, 2, 3), snapshot())
    revision = deterministic_id(UUID(int=9), "revision")
    event = result.transformation_for_revision(
        revision, "order.unit_price", (SourceRef(UUID(int=9), 4),), sequence=3
    )
    assert event is not None
    assert event.operation == "FX_CONVERTED_AT_ORDER_DATE"
    assert result.source_amount == Decimal("110")
    assert result.source_currency == "USD"
    assert result.source == "ECB"
    assert result.snapshot_id == UUID(int=1)
    assert result.evidence.publication_date == date(2024, 2, 2)
    assert result.evidence.source_url == "https://www.ecb.europa.eu/test"
    assert event.after.value == result.evidence
    assert event.sequence == 3
    assert (
        result.transformation_for_revision(
            revision, "order.unit_price", (SourceRef(UUID(int=9), 4),), sequence=3
        )
        == event
    )


def test_lifetime_spend_uses_run_snapshot() -> None:
    from services.pipeline.fx import convert_lifetime_spend_to_gbp

    result = convert_lifetime_spend_to_gbp(Decimal("120"), "USD", snapshot())
    assert result.gbp.value == Decimal("90.00")
    assert result.requested_date == date(2026, 9, 16)
    assert result.operation == "FX_CONVERTED_AT_RUN_DATE"


@pytest.mark.parametrize(
    "currency, requested", [("JPY", date(2024, 2, 3)), ("USD", date(1998, 1, 1))]
)
def test_missing_rate_preserves_source_and_requires_review(
    currency: str, requested: date
) -> None:
    from services.domain.ids import deterministic_id
    from services.pipeline.fx import convert_to_gbp

    result = convert_to_gbp(Decimal("123.45"), currency, requested, snapshot())
    assert (result.source_amount, result.source_currency) == (
        Decimal("123.45"),
        currency,
    )
    assert result.gbp.state == "unresolved"
    assert result.gbp.value is None
    assert result.readiness == "ineligible"
    issue = result.issue_for_revision(
        deterministic_id(UUID(int=9), "r"), "order.unit_price", ()
    )
    assert issue is not None and issue.code == "FX_RATE_UNAVAILABLE"
    assert (
        result.transformation_for_revision(
            deterministic_id(UUID(int=9), "r"), "order.unit_price", (), sequence=1
        )
        is None
    )


def test_gbp_identity_is_exact_without_rates() -> None:
    from dataclasses import replace

    from services.pipeline.fx import convert_to_gbp

    result = convert_to_gbp(
        Decimal("1.23456789"), "GBP", date(1900, 1, 1), replace(snapshot(), rates=())
    )
    assert result.gbp.value == Decimal("1.23456789")
    assert result.rate == Decimal("1")
    assert result.publication_date is None


def test_eur_uses_gbp_reference_and_money_rounds_half_even() -> None:
    from services.pipeline.fx import convert_to_gbp

    assert convert_to_gbp(
        Decimal("1.10"), "EUR", date(2024, 2, 3), snapshot()
    ).gbp.value == Decimal("0.94")
    assert convert_to_gbp(
        Decimal("-1.10"), "EUR", date(2024, 2, 3), snapshot()
    ).gbp.value == Decimal("-0.94")


def test_large_source_money_keeps_pennies_without_context_overflow() -> None:
    from services.pipeline.fx import convert_lifetime_spend_to_gbp

    result = convert_lifetime_spend_to_gbp(
        Decimal("1000000000000000000000000000000"), "USD", snapshot()
    )
    assert result.gbp.value == Decimal("750000000000000000000000000000.00")
