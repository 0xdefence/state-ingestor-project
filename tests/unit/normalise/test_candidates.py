"""NOR-01 and append-only candidate revision invariants."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from services.domain.candidates import (
    CandidateRevision,
    EntityType,
    Money,
    ProductCandidate,
    RejectedCandidateShell,
    candidate_revision_id,
)
from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.ids import deterministic_id

RAW_ID = UUID(int=42)
FIXED_TIME = datetime(2026, 9, 17, 12, tzinfo=UTC)
REFS = (SourceRef(RAW_ID, 3), SourceRef(RAW_ID, 1))


def product() -> ProductCandidate:
    return ProductCandidate(
        sku=CandidateField.known("SKU-00204", source_refs=REFS),
        name=CandidateField.known("Café", source_refs=REFS),
        category=CandidateField.known("Electronics", source_refs=REFS),
        unit_price=CandidateField.known(
            Money(Decimal("123456789.123456789"), "EUR"), source_refs=REFS
        ),
        stock_qty=CandidateField(FieldState.UNRESOLVED, None, REFS),
        listed_date=CandidateField.known(date(2023, 4, 2), source_refs=REFS),
        status=CandidateField.known("pending_review", source_refs=REFS),
        tags=CandidateField.known(("one", "two"), source_refs=REFS),
        notes=CandidateField(FieldState.DEFERRED, None, REFS),
    )


def revision() -> CandidateRevision:
    return CandidateRevision(
        candidate_revision_id(RAW_ID, 1),
        RAW_ID,
        1,
        None,
        "normalise",
        product(),
        FIXED_TIME,
    )


@pytest.mark.parametrize(
    "state,value",
    [
        (FieldState.KNOWN, Decimal("1.20")),
        (FieldState.ABSENT, None),
        (FieldState.DEFERRED, None),
        (FieldState.UNRESOLVED, None),
    ],
)
def test_candidate_field_states_are_structured(
    state: FieldState, value: Decimal | None
) -> None:
    field = CandidateField(
        state,
        value,
        REFS,
        (deterministic_id(RAW_ID, "t"),),
        (deterministic_id(RAW_ID, "i"),),
    )
    assert field.state is state and field.value == value
    assert field.source_refs == REFS
    assert field.transformation_refs and field.issue_refs


@pytest.mark.parametrize("state", list(FieldState))
def test_field_states_reject_illegal_value(state: FieldState) -> None:
    with pytest.raises(ValueError):
        CandidateField(state, None if state is FieldState.KNOWN else "invalid", REFS)


def test_child_revision_requires_parent_and_next_number() -> None:
    initial = revision()
    with pytest.raises(ValueError, match="child revision must name its parent"):
        replace(
            initial,
            id=candidate_revision_id(RAW_ID, 2),
            revision_number=2,
            origin="SKU_ZERO_PADDING",
        )
    with pytest.raises(ValueError, match="immediate parent"):
        replace(
            initial,
            id=candidate_revision_id(RAW_ID, 3),
            revision_number=3,
            parent_revision_id=initial.id,
            origin="SKU_ZERO_PADDING",
        )
    child = replace(
        initial,
        id=candidate_revision_id(RAW_ID, 2),
        revision_number=2,
        parent_revision_id=initial.id,
        origin="SKU_ZERO_PADDING",
    )
    assert child.id.version == 5 and child.entity_type is EntityType.PRODUCT


@pytest.mark.parametrize(
    "changes",
    [
        {"revision_number": 0},
        {"origin": "SKU_ZERO_PADDING"},
        {"parent_revision_id": UUID(int=1)},
        {"id": UUID(int=1)},
    ],
)
def test_initial_revision_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(revision(), **changes)


def test_revision_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        revision().origin = "changed"  # type: ignore[misc]


def test_rejected_revision_does_not_invent_entity_type() -> None:
    shell = RejectedCandidateShell(RAW_ID, REFS, (deterministic_id(RAW_ID, "issue"),))
    assert replace(revision(), payload=shell).entity_type is None
    with pytest.raises(ValueError, match="raw record"):
        replace(revision(), payload=replace(shell, raw_record_id=UUID(int=99)))


def test_revision_rejects_untyped_payload() -> None:
    with pytest.raises(ValueError, match="typed candidate"):
        replace(revision(), payload={"sku": "SKU-00204"})


def test_money_rejects_binary_float() -> None:
    with pytest.raises(ValueError, match="Decimal"):
        Money(1.2, "GBP")  # type: ignore[arg-type]


def test_candidate_derived_fields_preserve_structured_absence() -> None:
    assert product().unit_price_gbp.state is FieldState.ABSENT
