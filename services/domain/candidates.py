"""Immutable typed interpretations of raw evidence; never canonical identities."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.ids import deterministic_id


def _runtime_value(value: object) -> object:
    return value


class EntityType(StrEnum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    ORDER = "order"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if (
            not isinstance(_runtime_value(self.amount), Decimal)
            or not self.amount.is_finite()
        ):
            raise ValueError("money requires a finite Decimal")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase code")


@dataclass(frozen=True, slots=True)
class CustomerCandidate:
    customer_id: CandidateField[str]
    name: CandidateField[str]
    email: CandidateField[str]
    lifetime_spend: CandidateField[Money]
    signup_date: CandidateField[date | datetime]
    status: CandidateField[str]
    tags: CandidateField[tuple[str, ...]]
    notes: CandidateField[str]
    name_match_key: CandidateField[str] = CandidateField(FieldState.ABSENT, None, ())
    lifetime_spend_gbp: CandidateField[Decimal] = CandidateField(
        FieldState.ABSENT, None, ()
    )
    entity_type: EntityType = field(default=EntityType.CUSTOMER, init=False)


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    sku: CandidateField[str]
    name: CandidateField[str]
    category: CandidateField[str]
    unit_price: CandidateField[Money]
    stock_qty: CandidateField[int]
    listed_date: CandidateField[date | datetime]
    status: CandidateField[str]
    tags: CandidateField[tuple[str, ...]]
    notes: CandidateField[str]
    unit_price_gbp: CandidateField[Decimal] = CandidateField(
        FieldState.ABSENT, None, ()
    )
    entity_type: EntityType = field(default=EntityType.PRODUCT, init=False)


@dataclass(frozen=True, slots=True)
class OrderCandidate:
    order_id: CandidateField[str]
    customer_name_raw: CandidateField[str]
    sku: CandidateField[str]
    unit_price: CandidateField[Money]
    quantity: CandidateField[int]
    ordered_at: CandidateField[date | datetime]
    status: CandidateField[str]
    tags: CandidateField[tuple[str, ...]]
    notes: CandidateField[str]
    customer_match_key: CandidateField[str] = CandidateField(
        FieldState.ABSENT, None, ()
    )
    unit_price_gbp: CandidateField[Decimal] = CandidateField(
        FieldState.ABSENT, None, ()
    )
    entity_type: EntityType = field(default=EntityType.ORDER, init=False)


@dataclass(frozen=True, slots=True)
class RejectedCandidateShell:
    raw_record_id: UUID
    source_refs: tuple[SourceRef, ...]
    issue_refs: tuple[UUID, ...]
    entity_type: None = field(default=None, init=False)


type CandidatePayload = (
    CustomerCandidate | ProductCandidate | OrderCandidate | RejectedCandidateShell
)
type FieldValue = str | int | Decimal | date | datetime | Money | tuple[str, ...]
# CandidateField is invariant because its constructor also consumes T. This union
# lets evidence retain each field's precise type without casts in pipeline code.
type EvidenceField = (
    CandidateField[str]
    | CandidateField[int]
    | CandidateField[Decimal]
    | CandidateField[date]
    | CandidateField[datetime]
    | CandidateField[date | datetime]
    | CandidateField[Money]
    | CandidateField[tuple[str, ...]]
    | CandidateField[FieldValue]
)


def candidate_revision_id(raw_record_id: UUID, revision_number: int) -> UUID:
    return deterministic_id(raw_record_id, "candidate_revision", revision_number)


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    id: UUID
    raw_record_id: UUID
    revision_number: int
    parent_revision_id: UUID | None
    origin: str
    payload: CandidatePayload
    created_at: datetime

    def __post_init__(self) -> None:
        if self.revision_number < 1:
            raise ValueError("revision number must be positive")
        if self.revision_number == 1:
            if self.parent_revision_id is not None or self.origin != "normalise":
                raise ValueError("initial revision has no parent and normalise origin")
        else:
            if self.parent_revision_id is None:
                raise ValueError("child revision must name its parent")
            if self.parent_revision_id != candidate_revision_id(
                self.raw_record_id, self.revision_number - 1
            ):
                raise ValueError("child revision must name its immediate parent")
            if not self.origin or self.origin == "normalise":
                raise ValueError("child revision must name its repair origin")
        if self.id != candidate_revision_id(self.raw_record_id, self.revision_number):
            raise ValueError("revision identity must be deterministic")
        if not isinstance(
            _runtime_value(self.payload),
            (
                CustomerCandidate,
                ProductCandidate,
                OrderCandidate,
                RejectedCandidateShell,
            ),
        ):
            raise ValueError("revision requires a typed candidate payload")
        if isinstance(self.payload, RejectedCandidateShell):
            if self.payload.raw_record_id != self.raw_record_id:
                raise ValueError("rejected shell must reference the same raw record")

    @property
    def entity_type(self) -> EntityType | None:
        return self.payload.entity_type
