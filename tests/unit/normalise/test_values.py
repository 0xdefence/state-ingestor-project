"""NOR-02 through NOR-11: approved forms and evidence, not permissive parsing."""

from datetime import date, datetime
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.dates import normalise_date
from services.pipeline.normalise.evidence import FieldPath
from services.pipeline.normalise.money import normalise_money
from services.pipeline.normalise.status import normalise_status
from services.pipeline.normalise.tags import normalise_tags
from services.pipeline.normalise.text import (
    match_key,
    normalise_integer,
    normalise_text,
)

SOURCE = SourceRef(UUID("ec975410-dc09-5701-aee0-70c75e0b5e5a"), 4)
MONEY = FieldPath("customer.lifetime_spend", (SOURCE,))
REVISION = UUID("073dc0fa-72db-58de-b9df-a7f5c3c771c0")


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("19.99", "19.99", "GBP"),
        ("$1,240.50", "1240.50", "USD"),
        ("45,00", "45.00", "GBP"),
        ("€2.345,00", "2345.00", "EUR"),
        ("1.5e3", "1500.00", "GBP"),
        (" 275.00 ", "275.00", "GBP"),
        ("-100", "-100.00", "GBP"),
        ("0", "0.00", "GBP"),
        ("1234.567", "1234.57", "GBP"),
        ("1.245", "1.24", "GBP"),
    ],
)
def test_money_formats(raw: str, amount: str, currency: str) -> None:
    result = normalise_money(raw, MONEY)
    assert (result.amount, result.currency) == (Decimal(amount), currency)
    assert isinstance(result.amount, Decimal)
    assert result.amount.as_tuple().exponent == -2
    assert result.raw_value == raw
    assert result.field.source_refs == (SOURCE,)
    assert result.field.state is FieldState.KNOWN
    assert not result.issues


@pytest.mark.parametrize(
    ("raw", "amount", "annotation"),
    [
        ("12.99 (10% off)", "12.99", "10% off"),
        ("£759.99 (GBP)", "759.99", "GBP"),
    ],
)
def test_money_annotation_is_preserved(raw: str, amount: str, annotation: str) -> None:
    result = normalise_money(raw, MONEY)
    assert (result.amount, result.annotation) == (Decimal(amount), annotation)
    assert result.raw_value == raw
    assert result.transformations[-1].operation is TransformationCode.PARSE_MONEY


def test_ambiguous_single_comma_uses_thousands_rule_and_records_issue() -> None:
    result = normalise_money("1,240", MONEY)
    assert result.amount == Decimal("1240.00")
    assert len(result.issues) == 1
    issue = result.issues[0].for_revision(REVISION)
    assert issue.code is IssueCode.AMBIGUOUS_MONEY
    assert "1,240" in issue.summary
    assert issue.source_refs == (SOURCE,)
    assert result.field.issue_refs == (issue.id,)
    assert issue.id.version == 5


@pytest.mark.parametrize(
    "raw",
    [
        "149.99|with discount code",
        "USD 1200.50",
        "85.50|EUR 79.90",
        "$8.99 x5=$44.95",
        "3,500 SR",
        "¥15000",
        "$69.99 | €65.00",
        "$2,340 MXN converted @ 17.5 = $133.71",
        "NaN",
        "Infinity",
        "1_000",
        "12,34,56",
        "1.2.3",
        "1e3",
        "1.5E3",
        "1.5e+3",
        "1.5e-3",
        "١٢.٣٤",
        "12.99 (one) (two)",
        "TBD",
        "NULL",
    ],
)
def test_unsupported_money_is_unresolved(raw: str) -> None:
    result = normalise_money(raw, MONEY)
    assert result.field.state is FieldState.UNRESOLVED
    assert result.field.value is None
    assert result.amount is None
    assert result.raw_value == raw
    assert result.issues[0].code is IssueCode.INVALID_MONEY
    assert result.field.issue_refs == (result.issues[0].id,)


def test_money_is_independent_of_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = "ROUND_UP"
        assert normalise_money("123456789.125", MONEY).amount == Decimal("123456789.12")


@pytest.mark.parametrize(
    ("kind", "path", "raw", "state", "value"),
    [
        ("text", "customer.email", "-", FieldState.ABSENT, None),
        ("text", "customer.notes", "", FieldState.ABSENT, None),
        ("text", "customer.email", "n/a", FieldState.KNOWN, "n/a"),
        ("text", "customer.notes", "-", FieldState.KNOWN, "-"),
        ("money", "order.unit_price", "NULL", FieldState.ABSENT, None),
        ("money", "product.unit_price", "TBD", FieldState.DEFERRED, None),
        ("integer", "order.quantity", "None", FieldState.ABSENT, None),
        ("integer", "product.stock_qty", "None", FieldState.UNRESOLVED, None),
        ("integer", "product.stock_qty", "many", FieldState.UNRESOLVED, None),
        ("integer", "product.stock_qty", "5|in-stock", FieldState.UNRESOLVED, None),
        ("integer", "order.quantity", "-2", FieldState.KNOWN, -2),
    ],
)
def test_null_and_deferred_tokens_are_contextual(
    kind: str,
    path: str,
    raw: str,
    state: FieldState,
    value: object,
) -> None:
    field = FieldPath(path, (SOURCE,))
    if kind == "money":
        result = normalise_money(raw, field).field
    elif kind == "integer":
        result = normalise_integer(raw, field)
    else:
        result = normalise_text(raw, field)
    assert result.state is state
    assert result.value == value
    assert result.source_refs == (SOURCE,)
    if state is FieldState.UNRESOLVED:
        assert result.issues[0].code is IssueCode.INVALID_INTEGER


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-05-12", date(2023, 5, 12)),
        ("01/22/2023", date(2023, 1, 22)),
        ("15-Jan-2024", date(2024, 1, 15)),
        ("2024/02/03 14:22:00", datetime(2024, 2, 3, 14, 22)),
        (" 2023-08-01 ", date(2023, 8, 1)),
    ],
)
def test_supported_date_formats(raw: str, expected: date | datetime) -> None:
    result = normalise_date(raw, FieldPath("order.ordered_at", (SOURCE,)))
    assert isinstance(result, CandidateField)
    assert result.value == expected
    assert type(result.value) is type(expected)
    assert result.source_refs == (SOURCE,)
    assert result.transformations[-1].operation is TransformationCode.PARSE_DATE
    assert not result.issues


def test_slash_date_is_month_day_year() -> None:
    result = normalise_date("03/04/2023", FieldPath("customer.signup_date"))
    assert result.value == date(2023, 3, 4)
    assert not result.issues


@pytest.mark.parametrize(
    "raw",
    [
        "2024-01-10T15:30:00Z",
        "3/15/24",
        "01-02-2024",
        "2024-02-20 09:15",
        "2024.04.10",
        "15.3.2023",
        "2024-05-22 14:30:45.123",
        "2023/12/15",
        "2024-07-01 +00:00",
        "2023.11.20",
        "2024-08-15T10:00:00",
        "unknown",
        "2024-09-01 13:45",
        "2023-02-29",
        "2024-1-01",
        "1/22/2023",
        "15-jan-2024",
        "2024/02/03 24:00:00",
        "0000-01-01",
    ],
)
def test_unregistered_or_impossible_dates_are_unresolved(raw: str) -> None:
    result = normalise_date(raw, FieldPath("order.ordered_at"))
    assert result.state is FieldState.UNRESOLVED
    assert result.value is None
    assert result.issues[0].code is IssueCode.INVALID_DATE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Active", "active"),
        ("ACTIVE", "active"),
        ("active", "active"),
        ("Y", "active"),
        ("Inactive", "inactive"),
        ("inactive", "inactive"),
    ],
)
def test_customer_status_mapping(raw: str, expected: str) -> None:
    result = normalise_status(raw, FieldPath("customer.status"))
    assert result.value == expected
    assert bool(result.transformations) is (raw != expected)
    if raw != expected:
        assert result.transformations[0].operation is TransformationCode.STATUS_MAPPING


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("customer.status", "true"),
        ("customer.status", "PENDING"),
        ("customer.status", "unknown"),
        ("customer.status", "INACTIVE"),
        ("product.status", "out_of_stock"),
        ("product.status", "pre_order"),
        ("order.status", "in_transit"),
        ("order.status", "processing"),
        ("order.status", "backorder"),
        ("order.status", "returned"),
        ("order.status", "awaiting_shipment"),
        ("order.status", "completed"),
    ],
)
def test_unregistered_status_is_unresolved(path: str, raw: str) -> None:
    result = normalise_status(raw, FieldPath(path))
    assert result.state is FieldState.UNRESOLVED
    assert result.issues[0].code is IssueCode.INVALID_STATUS


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        ("product.status", "in_stock"),
        ("product.status", "discontinued"),
        ("product.status", "pending_review"),
        ("product.status", "backordered"),
        ("order.status", "pending"),
        ("order.status", "shipped"),
        ("order.status", "cancelled"),
        ("order.status", "refunded"),
    ],
)
def test_registered_status_is_identity(path: str, raw: str) -> None:
    result = normalise_status(raw, FieldPath(path))
    assert result.value == raw
    assert not result.issues
    assert not result.transformations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vip|Newsletter", ("vip", "newsletter")),
        ("", ()),
        ("N/A", ()),
        ("vip|vip", ("vip", "vip")),
        ("na", ("na",)),
    ],
)
def test_tags_normalise_to_ordered_list(raw: str, expected: tuple[str, ...]) -> None:
    result = normalise_tags(raw, FieldPath("customer.tags"))
    assert result.value == expected
    assert result.state is FieldState.KNOWN


@pytest.mark.parametrize(
    "raw", ["|vip|", "|vip", "vip|", "vip||other", "n/a", "é", "vip| new"]
)
def test_malformed_tags_are_unresolved(raw: str) -> None:
    result = normalise_tags(raw, FieldPath("customer.tags"))
    assert result.state is FieldState.UNRESOLVED
    assert result.issues[0].code is IssueCode.INVALID_TAGS


@pytest.mark.parametrize(
    "raw", ["O'Brien, Zoë 🌟 & 李\nمحمد!", 'Quoted "text"', "a  b"]
)
def test_free_text_trims_edges_and_preserves_content(raw: str) -> None:
    result = normalise_text(" \t" + raw + " \n", FieldPath("customer.name", (SOURCE,)))
    assert result.value == raw
    assert result.source_refs == (SOURCE,)
    event = result.transformations[0].for_revision(REVISION)
    assert event.operation is TransformationCode.TRIM
    assert event.before.value == " \t" + raw + " \n"
    assert event.after.value == raw
    assert result.transformation_refs == (event.id,)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sofia Rossi 🌟", "sofia rossi"),
        ("  Wei Zhang  ", "wei zhang"),
        ("ＡＢＣ\tStraße", "abc strasse"),
        ("Johnson, Michael", "johnson, michael"),
        ("Zoë & £ + ∑", "zoë & £ + ∑"),
        ("李\nمحمد", "李 محمد"),
    ],
)
def test_match_key_is_not_stored_value(raw: str, expected: str) -> None:
    display = normalise_text(raw, FieldPath("customer.name"))
    assert match_key(raw) == expected
    assert display.value == raw.strip()


@pytest.mark.parametrize("raw", ["", " ", " TBD ", " nonsense "])
def test_money_non_known_states_retain_evidence(raw: str) -> None:
    path = FieldPath("product.unit_price", (SOURCE,))
    result = normalise_money(raw, path)
    assert result.field.value is None
    assert result.field.source_refs == (SOURCE,)
    assert result.raw_value == raw
    if raw != raw.strip():
        assert result.transformations[0].operation is TransformationCode.TRIM
    assert result == normalise_money(raw, path)


def test_evidence_identity_separates_records_and_fields() -> None:
    first = normalise_money("bad", MONEY)
    other_field = normalise_money("bad", FieldPath("order.unit_price", (SOURCE,)))
    other_record = normalise_money(
        "bad",
        FieldPath(
            "customer.lifetime_spend",
            (SourceRef(UUID("f412c085-8b70-5f74-a2ab-089021dbaae0"), 4),),
        ),
    )
    assert (
        len({first.issues[0].id, other_field.issues[0].id, other_record.issues[0].id})
        == 3
    )


def test_money_does_not_inherit_decimal_traps_or_exponent_limits() -> None:
    from decimal import Inexact, Rounded

    with localcontext() as context:
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.Emax = 2
        assert normalise_money("1234.567", MONEY).amount == Decimal("1234.57")


@pytest.mark.parametrize("path", ["product.status", "order.status"])
def test_empty_status_is_absent_for_required_field_validation(path: str) -> None:
    result = normalise_status("  ", FieldPath(path, (SOURCE,)))
    assert result.state is FieldState.ABSENT
    assert result.value is None
    assert result.source_refs == (SOURCE,)
    assert result.transformations[0].operation is TransformationCode.TRIM


def test_empty_date_preserves_source_and_trim_evidence() -> None:
    result = normalise_date("  ", FieldPath("order.ordered_at", (SOURCE,)))
    assert result.state is FieldState.ABSENT
    assert result.value is None
    assert result.source_refs == (SOURCE,)
    assert result.transformations[0].operation is TransformationCode.TRIM


@pytest.mark.parametrize("raw", [" 19.99 ", "bad", "", "TBD"])
def test_assembly_can_extract_domain_field_without_losing_evidence(raw: str) -> None:
    result = normalise_money(raw, FieldPath("product.unit_price", (SOURCE,))).field
    domain_field = result.as_candidate_field()
    assert type(domain_field) is CandidateField
    assert domain_field == CandidateField(
        result.state,
        result.value,
        (SOURCE,),
        tuple(event.id for event in result.transformations),
        tuple(issue.id for issue in result.issues),
    )


def test_assembly_can_assign_revision_global_transformation_sequence() -> None:
    money = normalise_money(" 19.99 ", MONEY)
    status = normalise_status("Active", FieldPath("customer.status", (SOURCE,)))
    drafts = (*money.transformations, *status.transformations)
    events = tuple(
        draft.for_revision(REVISION, sequence=sequence)
        for sequence, draft in enumerate(drafts, start=1)
    )
    assert tuple(event.sequence for event in events) == (1, 2, 3)
    assert tuple(event.id for event in events) == tuple(draft.id for draft in drafts)
