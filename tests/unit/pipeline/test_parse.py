from collections.abc import Generator, Mapping, MutableMapping, MutableSequence
from csv import Error
from io import BytesIO
from typing import cast
from uuid import UUID

import pytest

from services.domain.raw import RawRecord, RawRecordKind
from services.pipeline.parse import parse_records

RUN_ID = UUID("ca42ca01-19e8-4c41-bb6d-66ed13a18864")
OTHER_RUN_ID = UUID("4fd2a24c-6354-41a6-9c14-03a348b09a83")
HEADER = "record_type,id,name,contact_or_sku,value,quantity,date,status,tags,notes"


def test_bom_header_is_recognised_without_changing_frozen_bytes() -> None:
    """PAR-01: decoding the BOM must not rewrite the source or field text."""
    payload = b"\xef\xbb\xbf" + HEADER.encode() + b"\n"
    binary = BytesIO(payload)

    (record,) = parse_records(binary, RUN_ID)

    assert record.kind is RawRecordKind.HEADER
    assert record.fields == (
        "record_type",
        "id",
        "name",
        "contact_or_sku",
        "value",
        "quantity",
        "date",
        "status",
        "tags",
        "notes",
    )
    assert record.field_count == 10
    assert (record.source_line_start, record.source_line_end) == (1, 1)
    assert record.parse_metadata["encoding"] == "utf-8-sig"
    assert binary.getvalue() == payload


def test_blank_line_is_preserved() -> None:
    """PAR-02: csv.reader returns [] for a truly empty physical line."""
    records = list(parse_records(BytesIO(b'\n""\n,\n \n\r\n'), RUN_ID))

    assert [record.kind for record in records] == [
        RawRecordKind.BLANK,
        RawRecordKind.DATA,
        RawRecordKind.DATA,
        RawRecordKind.DATA,
        RawRecordKind.BLANK,
    ]
    assert [record.fields for record in records] == [(), ("",), ("", ""), (" ",), ()]
    assert [record.field_count for record in records] == [0, 1, 2, 1, 0]
    assert [(r.source_line_start, r.source_line_end) for r in records] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 5),
    ]


def test_repeated_header_is_preserved_and_classified() -> None:
    """PAR-03: only the complete exact header is a structural record."""
    payload = f"\n{HEADER}\nORDER,1\n{HEADER}\n {HEADER}\nrecord_type,id\n"

    records = list(parse_records(BytesIO(payload.encode()), RUN_ID))

    assert [record.kind for record in records] == [
        RawRecordKind.BLANK,
        RawRecordKind.HEADER,
        RawRecordKind.DATA,
        RawRecordKind.REPEATED_HEADER,
        RawRecordKind.DATA,
        RawRecordKind.DATA,
    ]
    assert records[1].fields == records[3].fields
    assert (records[3].source_line_start, records[3].source_line_end) == (4, 4)
    assert records[4].fields[0] == " record_type"


def test_quoted_multiline_field_is_one_logical_record() -> None:
    """PAR-04: embedded LF consumes physical lines without splitting a record."""
    payload = f'{HEADER}\nPRODUCT,SKU-1,"first line\nsecond line"\nORDER,1\n'

    records = list(parse_records(BytesIO(payload.encode()), RUN_ID))

    assert len(records) == 3
    record = records[1]
    assert record.kind is RawRecordKind.DATA
    assert record.source_line_start == 2
    assert record.source_line_end == 3
    assert record.fields[-1] == "first line\nsecond line"
    assert (records[2].source_line_start, records[2].source_line_end) == (4, 4)


def test_short_row_preserves_actual_field_count() -> None:
    """PAR-05: missing columns stay missing rather than being padded."""
    (record,) = parse_records(
        BytesIO(b"ORDER,ORD-3004,Ada,SKU-1,12.00,2,TBD\n"), RUN_ID
    )

    assert record.kind is RawRecordKind.DATA
    assert record.fields == ("ORDER", "ORD-3004", "Ada", "SKU-1", "12.00", "2", "TBD")
    assert record.field_count == 7


def test_long_row_preserves_all_fields() -> None:
    """PAR-06: unquoted overflow must not be merged into a repaired note."""
    payload = b"CUSTOMER,CUST-1005,Ada,a@b.c,10,,2023-01-01,Active,vip,note,more,end\n"
    (record,) = parse_records(BytesIO(payload), RUN_ID)

    assert record.fields == (
        "CUSTOMER",
        "CUST-1005",
        "Ada",
        "a@b.c",
        "10",
        "",
        "2023-01-01",
        "Active",
        "vip",
        "note",
        "more",
        "end",
    )
    assert record.field_count == 12


def test_trailing_empty_field_is_preserved() -> None:
    """PAR-07: a trailing delimiter contributes a real additional field."""
    (record,) = parse_records(
        BytesIO(b"ORDER,1,Ada,SKU-1,10,1,TBD,paid,vip,note,\n"), RUN_ID
    )

    assert record.fields == (
        "ORDER",
        "1",
        "Ada",
        "SKU-1",
        "10",
        "1",
        "TBD",
        "paid",
        "vip",
        "note",
        "",
    )
    assert record.field_count == 11


def test_escaped_quote_decodes_without_quality_issue() -> None:
    """PAR-08: quote decoding is parser provenance, not a business repair."""
    (record,) = parse_records(BytesIO(b'CUSTOMER,1,"Ada ""Ace"", Lovelace"\n'), RUN_ID)

    assert record.fields == ("CUSTOMER", "1", 'Ada "Ace", Lovelace')
    assert record.kind is RawRecordKind.DATA
    assert record.parse_metadata == {
        "logical_ordinal": 1,
        "parser": "csv.reader",
        "encoding": "utf-8-sig",
        "newline": "",
        "dialect": {
            "delimiter": ",",
            "quotechar": '"',
            "doublequote": True,
            "skipinitialspace": False,
            "strict": True,
        },
    }


def test_unicode_whitespace_and_embedded_crlf_are_preserved() -> None:
    payload = 'UNKNOWN,  José 李 أحمد 🌟  ,"  1,240.50  ",many,"a\r\nb"\r\n'

    (record,) = parse_records(BytesIO(payload.encode()), RUN_ID)

    assert record.fields == (
        "UNKNOWN",
        "  José 李 أحمد 🌟  ",
        "  1,240.50  ",
        "many",
        "a\r\nb",
    )
    assert record.kind is RawRecordKind.DATA
    assert (record.source_line_start, record.source_line_end) == (1, 2)


def test_logical_ordinal_gives_stable_run_scoped_ids_without_deduplication() -> None:
    payload = b'ORDER,1,"a\nb"\nORDER,1,"a\nb"\n'
    first = list(parse_records(BytesIO(payload), RUN_ID))
    replay = list(parse_records(BytesIO(payload), RUN_ID))
    other_run = list(parse_records(BytesIO(payload), OTHER_RUN_ID))
    other_spans = list(parse_records(BytesIO(b"ORDER,1,a\nORDER,1,a\n"), RUN_ID))

    assert first == replay
    assert first[0].fields == first[1].fields
    assert len({record.id for record in first}) == 2
    assert all(record.id.version == 5 and record.run_id == RUN_ID for record in first)
    assert {record.id for record in first}.isdisjoint(record.id for record in other_run)
    assert [record.id for record in first] == [record.id for record in other_spans]
    assert [record.parse_metadata["logical_ordinal"] for record in first] == [1, 2]


def test_parser_output_fields_and_metadata_are_immutable() -> None:
    (record,) = parse_records(BytesIO(b"ORDER,1\n"), RUN_ID)

    with pytest.raises(TypeError):
        cast(MutableSequence[str], record.fields)[0] = "changed"
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], record.parse_metadata)["logical_ordinal"] = 9
    dialect = record.parse_metadata["dialect"]
    assert isinstance(dialect, Mapping)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], dialect)["delimiter"] = ";"


@pytest.mark.parametrize("payload", [b"", b"\xef\xbb\xbf"])
def test_empty_source_has_no_records_and_remains_open(payload: bytes) -> None:
    binary = BytesIO(payload)

    assert list(parse_records(binary, RUN_ID)) == []
    binary.seek(0)
    assert binary.read() == payload


def test_early_generator_close_leaves_callers_stream_usable() -> None:
    payload = b"ORDER,1\nORDER,2\n"
    binary = BytesIO(payload)
    records = parse_records(binary, RUN_ID)
    assert next(records).fields == ("ORDER", "1")

    cast(Generator[RawRecord, None, None], records).close()

    binary.seek(0)
    assert binary.read() == payload


@pytest.mark.parametrize(
    ("payload", "error"),
    [(b"\xff\n", UnicodeDecodeError), (b'ORDER,"unclosed', Error)],
)
def test_parse_errors_propagate_and_leave_callers_stream_usable(
    payload: bytes,
    error: type[Exception],
) -> None:
    binary = BytesIO(payload)

    with pytest.raises(error):
        list(parse_records(binary, RUN_ID))

    binary.seek(0)
    assert binary.read() == payload
