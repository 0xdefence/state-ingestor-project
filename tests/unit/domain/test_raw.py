from collections.abc import Mapping, MutableMapping, MutableSequence
from typing import cast
from uuid import uuid4

import pytest

from services.domain.raw import RawRecord, RawRecordKind


def test_raw_record_freezes_parse_metadata_recursively() -> None:
    parse_metadata: dict[str, object] = {
        "dialect": {"delimiter": ","},
        "source_lines": [2, 3],
    }
    record = RawRecord(
        id=uuid4(),
        run_id=uuid4(),
        source_line_start=2,
        source_line_end=3,
        kind=RawRecordKind.DATA,
        fields=("order",),
        field_count=1,
        parse_metadata=parse_metadata,
    )

    original_dialect = cast(dict[str, object], parse_metadata["dialect"])
    original_lines = cast(list[int], parse_metadata["source_lines"])
    original_dialect["delimiter"] = ";"
    original_lines.append(4)
    parse_metadata["quoted"] = True

    assert record.parse_metadata == {
        "dialect": {"delimiter": ","},
        "source_lines": (2, 3),
    }

    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], record.parse_metadata)["quoted"] = True

    record_dialect = record.parse_metadata["dialect"]
    assert isinstance(record_dialect, Mapping)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], record_dialect)["delimiter"] = ";"

    record_lines = record.parse_metadata["source_lines"]
    with pytest.raises(TypeError):
        cast(MutableSequence[object], record_lines)[0] = 4
