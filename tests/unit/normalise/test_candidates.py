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
from services.domain.raw import RawRecord
from services.pipeline.normalise.candidates import NormaliseResult

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


def fixture_record(line: int) -> RawRecord:
    from pathlib import Path

    from services.pipeline.parse import parse_records

    with Path("data/messy_sample_data.csv").open("rb") as source:
        return next(
            r
            for r in parse_records(source, UUID(int=73))
            if r.source_line_start == line
        )


def assemble(raw: RawRecord) -> NormaliseResult:
    from services.pipeline.normalise.candidates import (
        NormaliseContext,
        build_initial_candidate,
    )
    from tests.unit.pipeline.test_parse import FixedClock

    return build_initial_candidate(raw, NormaliseContext(FixedClock()))


@pytest.mark.parametrize(
    "line,typename",
    [(2, "CustomerCandidate"), (3, "ProductCandidate"), (4, "OrderCandidate")],
)
def test_record_type_selects_typed_candidate(line: int, typename: str) -> None:
    result = assemble(fixture_record(line))
    assert type(result.revision.payload).__name__ == typename
    assert result.revision.revision_number == 1
    assert result.revision.id.version == 5
    assert result == assemble(fixture_record(line))


@pytest.mark.parametrize("discriminator", ["", "ALIEN", "customer"])
def test_invalid_discriminator_creates_rejected_shell(discriminator: str) -> None:
    from services.domain.issues import IssueCode

    raw = fixture_record(2)
    raw = replace(raw, fields=(discriminator, *raw.fields[1:]))
    result = assemble(raw)
    shell = result.revision.payload
    assert isinstance(shell, RejectedCandidateShell)
    assert shell.raw_record_id == raw.id and shell.entity_type is None
    assert result.issues[0].code is IssueCode.INVALID_DISCRIMINATOR
    assert result.issues[0].id in shell.issue_refs


def test_short_row_marks_missing_fields_absent() -> None:
    from services.domain.issues import IssueCode, TransformationCode

    raw = fixture_record(13)
    result = assemble(raw)
    candidate = result.revision.payload
    assert raw.field_count == 7 and len(raw.fields) == 7
    assert all(
        getattr(candidate, field).state is FieldState.ABSENT
        for field in ("status", "tags", "notes")
    )
    issue = next(i for i in result.issues if i.code is IssueCode.MISSING_REQUIRED_VALUE)
    assert issue.field_path == "order.status"
    assert issue.id in candidate.status.issue_refs
    assert issue.source_refs == (SourceRef(raw.id),)
    assert TransformationCode.TRAILING_FIELDS_ABSENT in [
        t.operation for t in result.transformations
    ]


def test_overflow_notes_salvage_is_explicit() -> None:
    from services.domain.issues import IssueCode, TransformationCode

    raw = fixture_record(14)
    result = assemble(raw)
    assert raw.field_count == 12 and len(raw.fields) == 12
    assert result.revision.payload.notes.value == ",".join(raw.fields[9:]).strip()
    issue = next(i for i in result.issues if i.code is IssueCode.NOTES_REJOINED)
    assert issue.source_refs == tuple(
        SourceRef(raw.id, index) for index in range(9, 12)
    )
    assert "lossy" in issue.summary.lower()
    assert issue.id in result.revision.payload.notes.issue_refs
    event = next(
        t
        for t in result.transformations
        if t.operation is TransformationCode.NOTES_REJOINED
    )
    assert event.before.value == raw.fields[9:]
    assert event.id in result.revision.payload.notes.transformation_refs


def test_routine_parse_mechanics_do_not_create_quality_issues() -> None:
    from io import BytesIO

    from services.pipeline.parse import parse_records

    header = "record_type,id,name,contact_or_sku,value,quantity,date,status,tags,notes"

    content = (
        "\ufeff" + header + '\nPRODUCT,SKU-1000,"Quoted ""name""",Electronics,1.00,1,'
        '2023-01-01,in_stock,,"line one\nline two"\n\n' + header + "\n"
    ).encode()
    records = list(parse_records(BytesIO(content), UUID(int=88)))
    results = [assemble(raw) for raw in records]
    assert all(not result.issues for result in results)
    assert [result.revision is not None for result in results] == [
        False,
        True,
        False,
        False,
    ]
    assert results[1].revision.payload.notes.value == "line one\nline two"
    assert all(record.parse_metadata for record in records)


def test_shifted_row_and_unregistered_overflow_are_rejected() -> None:
    from services.domain.issues import IssueCode

    shifted = fixture_record(39)
    for raw in (
        shifted,
        replace(
            fixture_record(3),
            fields=(*fixture_record(3).fields, "extra"),
            field_count=11,
        ),
    ):
        result = assemble(raw)
        assert isinstance(result.revision.payload, RejectedCandidateShell)
        assert result.issues[0].code is IssueCode.INVALID_STRUCTURE
        assert not result.transformations


@pytest.mark.parametrize("line", [24, 31])
def test_extra_trailing_empty_fields_are_provenance_only(line: int) -> None:
    from services.domain.issues import TransformationCode

    raw = fixture_record(line)
    result = assemble(raw)
    assert not isinstance(result.revision.payload, RejectedCandidateShell)
    event = next(
        t
        for t in result.transformations
        if t.operation is TransformationCode.TRAILING_EMPTY_FIELDS
    )
    assert event.before.value == raw.fields
    assert event.after.value == raw.fields[:10]
    assert not any("STRUCTURE" in issue.code for issue in result.issues)


def test_all_field_evidence_is_plain_linked_and_globally_ordered() -> None:
    from dataclasses import fields

    from services.domain.issues import TransformationCode

    result = assemble(fixture_record(20))
    events = result.transformations
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.id for event in events}) == len(events)
    assert all(event.candidate_revision_id == result.revision.id for event in events)
    assert TransformationCode.MATCH_KEY in [event.operation for event in events]
    for field in fields(result.revision.payload):
        value = getattr(result.revision.payload, field.name)
        if isinstance(value, CandidateField):
            assert type(value) is CandidateField
            assert set(value.transformation_refs) <= {t.id for t in events}
            assert set(value.issue_refs) <= {i.id for i in result.issues}
    assert result.revision.payload.name.value == "Wei Zhang"
    assert result.revision.payload.name_match_key.value == "wei zhang"


def test_money_annotation_is_retained_by_assembly() -> None:
    result = assemble(fixture_record(21))
    assert result.revision.payload.unit_price_annotation == "10% off"
    assert result.revision.payload.unit_price.value.amount == Decimal("12.99")


def test_missing_padded_status_collects_each_transformation_once() -> None:
    from services.domain.issues import IssueCode, TransformationCode

    raw = fixture_record(3)
    result = assemble(replace(raw, fields=(*raw.fields[:7], "  ", *raw.fields[8:])))
    assert result.revision is not None
    status = result.revision.payload.status
    assert status.state is FieldState.ABSENT
    transformations = [
        t for t in result.transformations if t.field_path == "product.status"
    ]
    assert len(transformations) == 1
    assert transformations[0].operation is TransformationCode.TRIM
    assert status.transformation_refs == (transformations[0].id,)
    assert any(i.code is IssueCode.MISSING_REQUIRED_VALUE for i in result.issues)
