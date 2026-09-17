"""Explicit money grammar with source currency, annotation and Decimal arithmetic."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

from services.domain.candidates import Money
from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
    PendingIssue,
    PendingTransformation,
)

# A fractional mantissa and lowercase e with an unsigned exponent are the
# approved scientific form. Other exponent spellings require registered support.
_PLAIN = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_SCIENTIFIC = re.compile(r"[0-9]+\.[0-9]+e[0-9]+")
_COMMA_DECIMAL = re.compile(r"-?[0-9]+,[0-9]{2}")
_COMMA_GROUPED = re.compile(r"-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?")
_DOT_GROUPED = re.compile(r"-?[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+")
_ANNOTATION = re.compile(r"(.+) \(([^()]+)\)")
_CURRENCIES = {"$": "USD", "€": "EUR", "£": "GBP"}


@dataclass(frozen=True, slots=True)
class NormalisedMoney:
    field: NormalisedField[Money]
    annotation: str | None = None

    @property
    def amount(self) -> Decimal | None:
        return self.field.value.amount if self.field.value is not None else None

    @property
    def currency(self) -> str | None:
        return self.field.value.currency if self.field.value is not None else None

    @property
    def raw_value(self) -> str:
        return self.field.raw_value

    @property
    def issues(self) -> tuple[PendingIssue, ...]:
        return self.field.issues

    @property
    def transformations(self) -> tuple[PendingTransformation, ...]:
        return self.field.transformations


def normalise_money(raw: str, field: FieldPath) -> NormalisedMoney:
    evidence = FieldEvidence(raw, field)
    text = evidence.text
    if not text or (field.path == "order.unit_price" and text == "NULL"):
        return NormalisedMoney(evidence.finish(FieldState.ABSENT, None))
    if field.path == "product.unit_price" and text == "TBD":
        return NormalisedMoney(evidence.finish(FieldState.DEFERRED, None))
    annotation = None
    if match := _ANNOTATION.fullmatch(text):
        text, annotation = match.groups()
    currency = _CURRENCIES.get(text[:1], "GBP")
    if text[:1] in _CURRENCIES:
        text = text[1:]
    number: str | None = None
    value: Money | None = None
    ambiguous = False
    if _PLAIN.fullmatch(text) or _SCIENTIFIC.fullmatch(text):
        number = text
    elif _COMMA_DECIMAL.fullmatch(text):
        number = text.replace(",", ".")
    elif _COMMA_GROUPED.fullmatch(text):
        number = text.replace(",", "")
        ambiguous = text.count(",") == 1 and "." not in text
    elif _DOT_GROUPED.fullmatch(text):
        number = text.replace(".", "").replace(",", ".")
    if number is not None:
        try:
            amount = Decimal(number)
            # Fixed rounding and sufficient precision do not inherit a caller's
            # ambient Decimal context. No binary float enters the calculation.
            context = Context(
                prec=max(28, len(amount.as_tuple().digits), amount.adjusted() + 3),
                rounding=ROUND_HALF_EVEN,
                Emin=-999999,
                Emax=999999,
                capitals=1,
                clamp=0,
                flags=[],
                traps=[InvalidOperation],
            )
            amount = amount.quantize(Decimal("0.01"), context=context)
            value = Money(amount, currency)
        except (InvalidOperation, ValueError):
            value = None
    if value is None:
        evidence.issue(IssueCode.INVALID_MONEY, "Unsupported money format")
        return NormalisedMoney(evidence.finish(FieldState.UNRESOLVED, None), annotation)
    if ambiguous:
        evidence.issue(
            IssueCode.AMBIGUOUS_MONEY, "Single comma interpreted as thousands"
        )
    evidence.transform(TransformationCode.PARSE_MONEY, evidence.text, value)
    return NormalisedMoney(evidence.finish(FieldState.KNOWN, value), annotation)
