"""Offline conversion: 28-digit rates, HALF_EVEN, GBP rounded to pennies.

GBP identity is exact. Orders supply their order date; lifetime spend explicitly
uses the snapshot's fixed effective date. Both legs use the same publication.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

from services.domain.candidates import Money
from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.fx import FxEvidence, FxSnapshot
from services.domain.ids import deterministic_id
from services.domain.issues import (
    DataQualityIssue,
    IssueCode,
    Readiness,
    Severity,
    TransformationCode,
    TransformationEvent,
)

PRECISION = 28


@dataclass(frozen=True, slots=True)
class FxConversion:
    evidence: FxEvidence
    gbp: CandidateField[Decimal]

    @property
    def source_amount(self) -> Decimal:
        return self.evidence.source_amount

    @property
    def source_currency(self) -> str:
        return self.evidence.source_currency

    @property
    def rate(self) -> Decimal | None:
        return self.evidence.rate

    @property
    def publication_date(self) -> date | None:
        return self.evidence.publication_date

    @property
    def source(self) -> str:
        return self.evidence.source

    @property
    def snapshot_id(self) -> UUID:
        return self.evidence.snapshot_id

    @property
    def requested_date(self) -> date:
        return self.evidence.requested_date

    @property
    def operation(self) -> str:
        return self.evidence.operation

    @property
    def readiness(self) -> Readiness:
        return (
            Readiness.ELIGIBLE
            if self.gbp.state == FieldState.KNOWN
            else Readiness.INELIGIBLE
        )

    def transformation_for_revision(
        self,
        revision_id: UUID,
        field_path: str,
        source_refs: tuple[SourceRef, ...],
        *,
        sequence: int,
    ) -> TransformationEvent | None:
        if self.rate is None or self.source_currency == "GBP":
            return None
        return TransformationEvent(
            deterministic_id(revision_id, "fx", field_path, self.evidence),
            revision_id,
            TransformationCode(self.operation),
            field_path,
            CandidateField[Money].known(
                Money(self.source_amount, self.source_currency), source_refs=source_refs
            ),
            CandidateField[FxEvidence].known(self.evidence, source_refs=source_refs),
            sequence,
        )

    def issue_for_revision(
        self,
        revision_id: UUID,
        field_path: str,
        source_refs: tuple[SourceRef, ...],
    ) -> DataQualityIssue | None:
        if self.rate is not None:
            return None
        return DataQualityIssue(
            deterministic_id(revision_id, "fx-unavailable", field_path, self.evidence),
            revision_id,
            IssueCode.FX_RATE_UNAVAILABLE,
            Severity.ERROR,
            field_path,
            f"No ECB {self.source_currency}/GBP rate on or before "
            f"{self.requested_date.isoformat()} in snapshot {self.snapshot_id}.",
            source_refs,
        )


def convert_to_gbp(
    amount: Decimal,
    currency: str,
    requested_date: date,
    snapshot: FxSnapshot,
    *,
    operation: TransformationCode = TransformationCode.FX_CONVERTED_AT_ORDER_DATE,
) -> FxConversion:
    Money(amount, currency)  # Reject float and non-finite inputs at the boundary.
    if operation not in (
        TransformationCode.FX_CONVERTED_AT_ORDER_DATE,
        TransformationCode.FX_CONVERTED_AT_RUN_DATE,
    ):
        raise ValueError("FX conversion requires a date-basis operation")
    rate: Decimal | None = None
    publication: date | None = None
    converted: Decimal | None = None
    if currency == "GBP":
        rate, converted = Decimal("1"), amount
    else:
        source = snapshot.rate_on_or_before(
            "GBP" if currency == "EUR" else currency, requested_date
        )
        gbp = (
            snapshot.rate_on_or_before("GBP", source.publication_date)
            if source is not None
            else None
        )
        if (
            source is not None
            and gbp is not None
            and (source.publication_date == gbp.publication_date)
        ):
            with localcontext(Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)):
                rate = gbp.eur_reference_rate / (
                    Decimal("1") if currency == "EUR" else source.eur_reference_rate
                )
            # Preserve the exact product of the source amount and configured
            # rate before rounding pennies, including large normalized amounts.
            money_context = Context(
                prec=max(
                    PRECISION,
                    len(amount.as_tuple().digits) + len(rate.as_tuple().digits),
                    amount.adjusted() + rate.adjusted() + 4,
                ),
                rounding=ROUND_HALF_EVEN,
            )
            converted = money_context.multiply(amount, rate).quantize(
                Decimal("0.01"), context=money_context
            )
            publication = source.publication_date
    evidence = FxEvidence(
        amount,
        currency,
        converted,
        rate,
        publication,
        requested_date,
        snapshot.source,
        snapshot.source_url,
        snapshot.id,
        snapshot.manifest_hash,
        operation.value,
        PRECISION,
        ROUND_HALF_EVEN,
    )
    return FxConversion(
        evidence,
        CandidateField(
            FieldState.KNOWN if converted is not None else FieldState.UNRESOLVED,
            converted,
            (),
        ),
    )


def convert_lifetime_spend_to_gbp(
    amount: Decimal,
    currency: str,
    snapshot: FxSnapshot,
) -> FxConversion:
    return convert_to_gbp(
        amount,
        currency,
        snapshot.effective_at.date(),
        snapshot,
        operation=TransformationCode.FX_CONVERTED_AT_RUN_DATE,
    )
