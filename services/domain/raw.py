"""Immutable raw CSV records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import cast
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

    def __post_init__(self) -> None:
        frozen_metadata = _freeze_metadata(self.parse_metadata)
        object.__setattr__(self, "parse_metadata", frozen_metadata)


def _freeze_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {key: _freeze_metadata_value(value) for key, value in metadata.items()}
    )


def _freeze_metadata_value(value: object) -> object:
    if isinstance(value, Mapping):
        nested_mapping = cast(Mapping[object, object], value)
        return MappingProxyType({
            key: _freeze_metadata_value(item) for key, item in nested_mapping.items()
        })
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        sequence = cast(Sequence[object], value)
        return tuple(_freeze_metadata_value(item) for item in sequence)
    return value
