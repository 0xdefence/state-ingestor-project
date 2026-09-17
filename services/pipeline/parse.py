"""Interpret frozen CSV bytes without repairing or normalising raw evidence."""

import csv
from collections.abc import Iterator
from io import TextIOWrapper
from typing import BinaryIO
from uuid import UUID

from services.domain.ids import deterministic_id
from services.domain.raw import RAW_NAMESPACE, RawRecord, RawRecordKind

_HEADER = (
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


def parse_records(binary: BinaryIO, run_id: UUID) -> Iterator[RawRecord]:
    """Yield every logical record, leaving the caller's binary stream open.

    Consume from the current stream position. A retry must reopen or rewind the
    frozen source: the text wrapper may read ahead even when iteration stops
    early. Ordinals are one-based and include headers and blank records.
    """
    text = TextIOWrapper(binary, encoding="utf-8-sig", newline="")
    try:
        reader = csv.reader(text, strict=True)
        prior_line = 0
        seen_header = False
        for ordinal, parsed_fields in enumerate(reader, start=1):
            fields = tuple(parsed_fields)
            if not fields:
                kind = RawRecordKind.BLANK
            elif fields == _HEADER:
                kind = (
                    RawRecordKind.REPEATED_HEADER
                    if seen_header
                    else RawRecordKind.HEADER
                )
                seen_header = True
            else:
                kind = RawRecordKind.DATA

            line_start = prior_line + 1
            prior_line = reader.line_num
            yield RawRecord(
                id=deterministic_id(RAW_NAMESPACE, run_id, ordinal),
                run_id=run_id,
                source_line_start=line_start,
                source_line_end=reader.line_num,
                kind=kind,
                fields=fields,
                field_count=len(fields),
                parse_metadata={
                    "logical_ordinal": ordinal,
                    "parser": "csv.reader",
                    "encoding": "utf-8-sig",
                    "newline": "",
                    "dialect": {
                        "delimiter": reader.dialect.delimiter,
                        "quotechar": reader.dialect.quotechar,
                        "doublequote": reader.dialect.doublequote,
                        "skipinitialspace": reader.dialect.skipinitialspace,
                        "strict": reader.dialect.strict,
                    },
                },
            )
    finally:
        # This wrapper borrows the source; closing it would close the caller's
        # stream on exhaustion, decoding errors, or generator cancellation.
        text.detach()
