from __future__ import annotations

from collections.abc import (
    Generator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from csv import Error
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from services.application.ingest import IngestFile, ingest_file
from services.application.ports import PipelineEvent, Stage
from services.domain.raw import RawRecord, RawRecordKind
from services.domain.runs import RunState
from services.infrastructure.source_store import FilesystemSourceStore
from services.pipeline.parse import parse_records
from tests.unit.ingest.test_ingest import (
    MemoryCheckpoints,
    MemoryRuns,
    MemorySources,
)

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


# A transactional in-memory port implementation keeps recovery tests independent
# of PostgreSQL; the integration suite verifies the same boundary with real SQL.


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 16, 12, tzinfo=UTC)


class MemoryRaw:
    def __init__(self) -> None:
        self.rows: dict[UUID, RawRecord] = {}

    def add_batch(self, records: Sequence[RawRecord]) -> None:
        for record in records:
            if record.id in self.rows and self.rows[record.id] != record:
                raise ValueError("Raw identity mismatch")
            self.rows[record.id] = record


class MemoryEvents:
    def __init__(self) -> None:
        self.rows: list[PipelineEvent] = []

    def next_attempt_number(self, run_id: UUID, stage: Stage) -> int:
        attempts = [
            event.facts["attempt_number"]
            for event in self.rows
            if event.run_id == run_id
            and event.stage == stage
            and event.event_type in ("stage_started", "stage_retried")
        ]
        return max((cast(int, attempt) for attempt in attempts), default=0) + 1

    def append(self, event: PipelineEvent) -> None:
        self.rows.append(event)


class ParseMemoryUow:
    def __init__(self, database: ParseMemoryDatabase) -> None:
        self.database = database
        self.sources = MemorySources()
        self.sources.files = dict(database.sources.files)
        self.sources.occurrences = dict(database.sources.occurrences)
        self.runs = MemoryRuns()
        self.runs.rows = dict(database.runs.rows)
        self.runs.links = list(database.runs.links)
        self.checkpoints = MemoryCheckpoints()
        self.checkpoints.rows = dict(database.checkpoints.rows)
        self.raw_records = MemoryRaw()
        self.raw_records.rows = dict(database.raw_records.rows)
        self.events = MemoryEvents()
        self.events.rows = list(database.events.rows)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    def commit(self) -> None:
        self.database.sources = self.sources
        self.database.runs = self.runs
        self.database.checkpoints = self.checkpoints
        self.database.raw_records = self.raw_records
        self.database.events = self.events

    def rollback(self) -> None:
        pass


class ParseMemoryDatabase:
    def __init__(self) -> None:
        self.sources = MemorySources()
        self.runs = MemoryRuns()
        self.checkpoints = MemoryCheckpoints()
        self.raw_records = MemoryRaw()
        self.events = MemoryEvents()
        self.transactions: list[ParseMemoryUow] = []

    def factory(self) -> ParseMemoryUow:
        uow = ParseMemoryUow(self)
        self.transactions.append(uow)
        return uow


RECOVERY_BYTES = (HEADER + '\nORDER,1,"a\nb"\n\n' + HEADER + "\nORDER,2,\n").encode()


def prepared_run(
    tmp_path: Path, payload: bytes = RECOVERY_BYTES
) -> tuple[ParseMemoryDatabase, FilesystemSourceStore, UUID]:
    database = ParseMemoryDatabase()
    store = FilesystemSourceStore(tmp_path)
    upload = BytesIO(payload)
    result = ingest_file(
        IngestFile(upload, "input.csv", "/unavailable/input.csv", "operator"),
        database.factory(),
        store,
        FixedClock(),
    )
    upload.close()
    return database, store, result.run_id


def fail_second_batch(batch_number: int) -> None:
    if batch_number == 2:
        raise RuntimeError("injected before batch 2 commit")


def test_failed_batch_rolls_back_only_that_batch(tmp_path: Path) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(tmp_path)
    with pytest.raises(RuntimeError, match="injected"):
        parse_run(
            run_id, 2, database.factory, store, fail_second_batch, clock=FixedClock()
        )

    assert [r.fields for r in database.raw_records.rows.values()] == [
        tuple(HEADER.split(",")),
        ("ORDER", "1", "a\nb"),
    ]
    checkpoint = database.checkpoints.get(run_id, "parse")
    assert checkpoint is not None
    assert (checkpoint.batch_number, checkpoint.record_ordinal) == (1, 2)
    assert checkpoint.last_record_id == list(database.raw_records.rows)[-1]
    assert database.runs.get(run_id).state == RunState.PARSING
    assert database.runs.get(run_id).stage_failure == "parse_failed"
    events = database.events.rows
    assert [event.event_type for event in events] == [
        "stage_started",
        "batch_committed",
        "stage_failed",
    ]
    assert events[-1].facts["record_ordinal"] == 2
    assert events[-1].facts["error_type"] == "RuntimeError"
    assert all(
        event.occurred_at == datetime(2026, 9, 16, 12, tzinfo=UTC) for event in events
    )


def test_retry_from_checkpoint_is_idempotent(tmp_path: Path) -> None:
    from services.application.process import (
        ProcessRun,
        RetryRun,
        parse_run,
        process_run,
        retry_run,
    )

    database, store, run_id = prepared_run(tmp_path)
    with pytest.raises(RuntimeError):
        parse_run(
            run_id, 2, database.factory, store, fail_second_batch, clock=FixedClock()
        )
    committed = dict(database.raw_records.rows)
    uninterrupted = ParseMemoryDatabase()
    uninterrupted.sources = database.sources
    uninterrupted.runs.rows = {
        run_id: replace(
            database.runs.get(run_id), state=RunState.INGESTED, stage_failure=None
        )
    }
    baseline = process_run(
        ProcessRun(run_id, batch_size=2), uninterrupted.factory, store, FixedClock()
    )
    result = retry_run(
        RetryRun(run_id, batch_size=2), database.factory, store, FixedClock()
    )
    expected = list(parse_records(BytesIO(RECOVERY_BYTES), run_id))
    assert list(database.raw_records.rows.values()) == expected
    assert all(
        database.raw_records.rows[key] == value for key, value in committed.items()
    )
    assert result == baseline
    assert database.raw_records.rows == uninterrupted.raw_records.rows
    assert result.state == RunState.PARSED
    assert result.parse.record_count == 5
    assert database.runs.get(run_id).stage_failure is None
    checkpoint = database.checkpoints.get(run_id, "parse")
    assert checkpoint is not None
    assert (checkpoint.batch_number, checkpoint.record_ordinal) == (3, 5)
    before = list(database.events.rows)
    repeated = retry_run(RetryRun(run_id), database.factory, store, FixedClock())
    assert repeated == result
    assert database.events.rows == before
    assert [
        e.facts["record_ordinal"] for e in before if e.event_type == "batch_committed"
    ] == [2, 4, 5]


@pytest.mark.parametrize("payload", [b"", b"\xef\xbb\xbf"])
def test_parse_empty_source_completes_without_checkpoint(
    tmp_path: Path, payload: bytes
) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(tmp_path, payload)
    result = parse_run(run_id, 2, database.factory, store, clock=FixedClock())
    assert result.record_count == 0
    assert database.runs.get(run_id).state == RunState.PARSED
    assert database.checkpoints.get(run_id, "parse") is None
    assert not database.raw_records.rows


def test_parse_error_retains_prior_batches(tmp_path: Path) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(
        tmp_path, b'ORDER,1\nORDER,2\nORDER,"unclosed'
    )
    with pytest.raises(Error):
        parse_run(run_id, 2, database.factory, store, clock=FixedClock())
    assert len(database.raw_records.rows) == 2
    assert database.runs.get(run_id).stage_failure == "parse_failed"
    assert database.events.rows[-1].event_type == "stage_failed"


def test_parse_invalid_batch_size_does_not_start_run(tmp_path: Path) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(tmp_path)
    with pytest.raises(ValueError):
        parse_run(run_id, 0, database.factory, store, clock=FixedClock())
    assert database.runs.get(run_id).state == RunState.INGESTED
    assert not database.events.rows


def test_completion_commit_failure_retries_without_rewriting_batches(
    tmp_path: Path,
) -> None:
    from services.application.process import RetryRun, parse_run, retry_run

    database, store, run_id = prepared_run(tmp_path)

    class FailedCompletionUow(ParseMemoryUow):
        def commit(self) -> None:
            if self.runs.get(run_id).state == RunState.PARSED:
                raise RuntimeError("completion commit failed")
            super().commit()

    def factory() -> FailedCompletionUow:
        return FailedCompletionUow(database)

    with pytest.raises(RuntimeError, match="completion commit failed"):
        parse_run(run_id, 2, factory, store, clock=FixedClock())
    assert len(database.raw_records.rows) == 5
    assert database.runs.get(run_id).stage_failure == "parse_failed"
    assert not any(e.event_type == "stage_completed" for e in database.events.rows)
    batches = [e for e in database.events.rows if e.event_type == "batch_committed"]
    result = retry_run(
        RetryRun(run_id, batch_size=2), database.factory, store, FixedClock()
    )
    assert result.state == RunState.PARSED
    assert [
        e for e in database.events.rows if e.event_type == "batch_committed"
    ] == batches


def test_missing_frozen_source_records_recoverable_failure(tmp_path: Path) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            path.unlink()
    with pytest.raises(FileNotFoundError):
        parse_run(run_id, 2, database.factory, store, clock=FixedClock())
    assert database.runs.get(run_id).stage_failure == "parse_failed"
    assert not database.raw_records.rows
    assert database.events.rows[-1].facts["error_type"] == "FileNotFoundError"


def test_invalid_utf8_records_failure_without_completion(tmp_path: Path) -> None:
    from services.application.process import parse_run

    database, store, run_id = prepared_run(tmp_path, b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        parse_run(run_id, 2, database.factory, store, clock=FixedClock())
    assert database.runs.get(run_id).stage_failure == "parse_failed"
    assert not database.raw_records.rows
    assert database.checkpoints.get(run_id, "parse") is None
    assert [e.event_type for e in database.events.rows] == [
        "stage_started",
        "stage_failed",
    ]


@pytest.mark.parametrize("failure", ["batch", "completion"])
def test_event_ids_are_stable_uuid5_through_recovery(
    tmp_path: Path, failure: str
) -> None:
    from services.application.process import RetryRun, parse_run, retry_run

    database, store, run_id = prepared_run(tmp_path)
    baseline = ParseMemoryDatabase()
    baseline.sources = database.sources
    baseline.runs.rows = dict(database.runs.rows)
    parse_run(run_id, 2, baseline.factory, store, clock=FixedClock())

    class FailedCompletionUow(ParseMemoryUow):
        def commit(self) -> None:
            if self.runs.get(run_id).state == RunState.PARSED:
                raise RuntimeError("completion commit failed")
            super().commit()

    def completion_factory() -> FailedCompletionUow:
        return FailedCompletionUow(database)

    with pytest.raises(RuntimeError):
        if failure == "batch":
            parse_run(
                run_id,
                2,
                database.factory,
                store,
                fail_second_batch,
                clock=FixedClock(),
            )
        else:
            parse_run(run_id, 2, completion_factory, store, clock=FixedClock())
    retry_run(RetryRun(run_id, 2), database.factory, store, FixedClock())

    def stable_ids(
        database: ParseMemoryDatabase,
    ) -> dict[tuple[str, object, object], UUID]:
        events = [
            event
            for event in database.events.rows
            if event.event_type in ("batch_committed", "stage_completed")
        ]
        assert len(events) == 4
        assert all(event.id.version == 5 for event in events)
        return {
            (
                event.event_type,
                event.facts["batch_number"],
                event.facts["record_ordinal"],
            ): event.id
            for event in events
        }

    assert stable_ids(database) == stable_ids(baseline)
    before = list(database.events.rows)
    retry_run(RetryRun(run_id, 2), database.factory, store, FixedClock())
    assert database.events.rows == before


def test_event_attempts_distinguish_repeated_failures_at_same_checkpoint(
    tmp_path: Path,
) -> None:
    from services.application.process import RetryRun, parse_run, retry_run

    database, store, run_id = prepared_run(tmp_path)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            parse_run(
                run_id,
                2,
                database.factory,
                store,
                fail_second_batch,
                clock=FixedClock(),
            )
    failures = [
        event for event in database.events.rows if event.event_type == "stage_failed"
    ]
    starts = [
        event
        for event in database.events.rows
        if event.event_type in ("stage_started", "stage_retried")
    ]
    assert len(failures) == len(starts) == 3
    assert all(event.id.version == 5 for event in starts + failures)
    assert [event.facts["attempt_number"] for event in starts] == [1, 2, 3]
    assert [event.facts["attempt_number"] for event in failures] == [1, 2, 3]
    assert [event.facts["record_ordinal"] for event in failures] == [2, 2, 2]
    assert len({event.id for event in starts + failures}) == 6
    before = list(database.events.rows)
    retry_run(RetryRun(run_id, 2), database.factory, store, FixedClock())
    assert database.events.rows[: len(before)] == before
    assert database.events.rows[-1].facts["attempt_number"] == 4
