"""Immutable raw CSV records."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

RAW_NAMESPACE = UUID("8ad59d91-b94d-4d9f-8ab8-15bd638c2b9e")


class RawRecordKind(StrEnum):
    """The parser's structural classification of a logical CSV record."""

    DATA = "data"
    BLANK = "blank"
    HEADER = "header"
    REPEATED_HEADER = "repeated_header"


@dataclass(frozen=True, slots=True)
class RawRecord:
    """A frozen logical record from one processing run."""

    id: UUID
    run_id: UUID
    source_line_start: int
    source_line_end: int
    kind: RawRecordKind
    fields: tuple[str, ...]
    field_count: int
    parse_metadata: Mapping[str, object]
