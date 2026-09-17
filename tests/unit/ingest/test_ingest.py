"""Exact-file command contracts with real value objects and in-memory ports."""

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Never, Self
from uuid import UUID

import pytest

from services.application.ingest import (
    IngestFile,
    IngestResult,
    ReprocessSource,
    ingest_file,
    reprocess_source,
)
from services.application.ports import (
    PipelineCheckpoint,
    Run,
    RunSourceOccurrence,
    SourceFile,
    SourceOccurrence,
    Stage,
)
from services.domain.runs import RunState
from services.infrastructure.source_store import FilesystemSourceStore

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
CONTENT = b"\xef\xbb\xbfkind,name\r\ncustomer,\xf0\x9f\x98\x80\r\n"


class FixedClock:
    def now(self) -> datetime:
        return NOW


class MemorySources:
    def __init__(self) -> None:
        self.files: dict[UUID, SourceFile] = {}
        self.occurrences: dict[UUID, SourceOccurrence] = {}

    def get(self, source_file_id: UUID) -> SourceFile:
        return self.files[source_file_id]

    def get_or_create(self, source: SourceFile) -> tuple[SourceFile, bool]:
        for existing in self.files.values():
            if existing.sha256 == source.sha256:
                return existing, False
        self.files[source.id] = source
        return source, True

    def get_occurrence_by_key(self, key: str) -> SourceOccurrence | None:
        return next(
            (o for o in self.occurrences.values() if o.idempotency_key == key), None
        )

    def add_occurrence(self, occurrence: SourceOccurrence) -> None:
        self.occurrences[occurrence.id] = occurrence


class MemoryRuns:
    def __init__(self) -> None:
        self.rows: dict[UUID, Run] = {}
        self.links: list[RunSourceOccurrence] = []

    def lock_source(self, source_file_id: UUID) -> None:
        pass

    def get(self, run_id: UUID) -> Run:
        return self.rows[run_id]

    def get_terminal_run(self, source_file_id: UUID) -> Run | None:
        return max(
            (r for r in self.rows.values() if r.source_file_id == source_file_id),
            key=lambda r: r.reprocess_sequence,
            default=None,
        )

    def get_for_occurrence(self, occurrence_id: UUID) -> Run:
        return next(
            self.rows[link.run_id]
            for link in self.links
            if link.source_occurrence_id == occurrence_id
        )

    def add(self, run: Run) -> None:
        self.rows[run.id] = run

    def link_occurrence(self, link: RunSourceOccurrence) -> None:
        self.links.append(link)

    def set_state(
        self, run_id: UUID, state: RunState, *, stage_failure: str | None = None
    ) -> None:
        self.rows[run_id] = replace(
            self.rows[run_id], state=state, stage_failure=stage_failure
        )


class MemoryCheckpoints:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, Stage], PipelineCheckpoint] = {}

    def get(self, run_id: UUID, stage: Stage) -> PipelineCheckpoint | None:
        return self.rows.get((run_id, stage))

    def advance(self, checkpoint: PipelineCheckpoint) -> None:
        self.rows[checkpoint.run_id, checkpoint.stage] = checkpoint


class MemoryUow:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sources = MemorySources()
        self.runs = MemoryRuns()
        self.checkpoints = MemoryCheckpoints()
        self.commits = 0
        self.entries = 0

    @property
    def raw_records(self) -> Never:
        raise AssertionError("ingest must not access pipeline writes")

    @property
    def events(self) -> Never:
        raise AssertionError("ingest must not access pipeline writes")

    def __enter__(self) -> Self:
        # A frozen final object must exist before any transaction starts.
        assert any(p.is_file() for p in self.root.rglob("*"))
        self.entries += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


@pytest.fixture
def setup(tmp_path: Path) -> tuple[MemoryUow, FilesystemSourceStore, FixedClock]:
    store = FilesystemSourceStore(tmp_path / "sources")
    return MemoryUow(tmp_path / "sources"), store, FixedClock()


def command(
    filename: str = "first.csv",
    *,
    content: bytes = CONTENT,
    key: str | None = None,
    actor: str = "first actor",
) -> IngestFile:
    return IngestFile(BytesIO(content), filename, "/imports/" + filename, actor, key)


def test_freezes_source_bytes_without_modification(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    result = ingest_file(command(), uow, store, clock)
    source = uow.sources.get(result.source_file_id)
    with store.open(source.locator) as frozen:
        assert frozen.read() == CONTENT
    assert source.sha256 == sha256(CONTENT).hexdigest()
    assert source.byte_size == len(CONTENT)
    assert result == IngestResult(
        source.id, result.source_occurrence_id, result.run_id, False, False, False, None
    )
    assert all(
        i.version == 4 for i in (source.id, result.source_occurrence_id, result.run_id)
    )
    assert uow.runs.get(result.run_id).state == RunState.INGESTED
    assert uow.commits == 1


def test_identical_content_records_each_occurrence_but_reuses_run(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(), uow, store, clock)
    second = ingest_file(
        command("renamed.csv", actor="second actor"), uow, store, clock
    )
    assert second == IngestResult(
        first.source_file_id,
        second.source_occurrence_id,
        first.run_id,
        True,
        True,
        True,
        None,
    )
    assert second.source_occurrence_id != first.source_occurrence_id
    assert (
        len(uow.sources.files),
        len(uow.sources.occurrences),
        len(uow.runs.rows),
        len(uow.runs.links),
    ) == (1, 2, 1, 2)
    assert list(uow.sources.occurrences.values()) == [
        SourceOccurrence(
            first.source_occurrence_id,
            first.source_file_id,
            "first.csv",
            "/imports/first.csv",
            "first actor",
            None,
            NOW,
        ),
        SourceOccurrence(
            second.source_occurrence_id,
            first.source_file_id,
            "renamed.csv",
            "/imports/renamed.csv",
            "second actor",
            None,
            NOW,
        ),
    ]
    assert [link.relation for link in uow.runs.links] == [
        "initiated",
        "duplicate_upload",
    ]


def test_same_idempotency_key_returns_same_run(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(key="request-1"), uow, store, clock)
    second = ingest_file(command(key="request-1"), uow, store, clock)
    assert second.source_occurrence_id == first.source_occurrence_id
    assert second.run_id == first.run_id
    assert (
        len(uow.sources.occurrences) == len(uow.runs.links) == len(uow.runs.rows) == 1
    )


def test_explicit_reprocess_creates_new_run_for_frozen_source(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(), uow, store, clock)
    second = reprocess_source(
        ReprocessSource(first.source_file_id, first.source_occurrence_id), uow, clock
    )
    assert second == IngestResult(
        first.source_file_id,
        first.source_occurrence_id,
        second.run_id,
        True,
        False,
        False,
        None,
    )
    assert second.run_id != first.run_id
    successor = uow.runs.get(second.run_id)
    assert successor.predecessor_run_id == first.run_id
    assert successor.reprocess_sequence == 1
    assert uow.runs.links[-1].relation == "initiated"
    assert len(uow.sources.files) == len(uow.sources.occurrences) == 1
    with store.open(uow.sources.get(first.source_file_id).locator) as frozen:
        assert frozen.read() == CONTENT


def test_same_filename_with_different_bytes_creates_new_source_and_run(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(), uow, store, clock)
    second = ingest_file(command(content=b"other bytes"), uow, store, clock)
    assert second.source_file_id != first.source_file_id
    assert second.run_id != first.run_id
    assert not second.source_reused and not second.run_reused
    assert len(uow.sources.files) == len(uow.runs.rows) == 2


def test_duplicate_completed_run_performs_no_pipeline_work(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(), uow, store, clock)
    uow.runs.set_state(first.run_id, RunState.STAGED)
    before = dict(uow.runs.rows)
    second = ingest_file(command("completed.csv"), uow, store, clock)
    assert second == IngestResult(
        first.source_file_id,
        second.source_occurrence_id,
        first.run_id,
        True,
        True,
        True,
        None,
    )
    assert uow.runs.rows == before
    assert len(uow.sources.occurrences) == len(uow.runs.links) == 2


def test_duplicate_incomplete_run_resumes_when_processing_requested(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    # Task 4 returns durable metadata; Task 6 consumes it to dispatch retry_run.
    uow, store, clock = setup
    first = ingest_file(command(), uow, store, clock)
    uow.runs.set_state(first.run_id, RunState.PARSING, stage_failure="parse_failed")
    checkpoint = PipelineCheckpoint(
        first.run_id, "parse", 2, 100, UUID("00000000-0000-0000-0000-000000000123"), NOW
    )
    uow.checkpoints.advance(checkpoint)
    second = ingest_file(command("retry.csv"), uow, store, clock)
    assert second == IngestResult(
        first.source_file_id,
        second.source_occurrence_id,
        first.run_id,
        True,
        True,
        True,
        checkpoint,
    )
    assert uow.runs.get(first.run_id).stage_failure == "parse_failed"
    assert len(uow.runs.rows) == 1


def test_duplicate_after_reprocess_reuses_latest_run(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup
    first = ingest_file(command(key="original"), uow, store, clock)
    request = ReprocessSource(first.source_file_id, first.source_occurrence_id)
    second = reprocess_source(request, uow, clock)
    third = reprocess_source(request, uow, clock)
    duplicate = ingest_file(command("later.csv"), uow, store, clock)
    assert duplicate.run_id == third.run_id
    assert uow.runs.get(third.run_id).predecessor_run_id == second.run_id
    assert uow.runs.get(second.run_id).predecessor_run_id == first.run_id
    assert uow.runs.get(first.run_id).predecessor_run_id is None
    replay = ingest_file(command(key="original"), uow, store, clock)
    assert replay.run_id == first.run_id
    assert replay.source_occurrence_id == first.source_occurrence_id
    assert len(uow.runs.rows) == 3
    assert len(uow.sources.occurrences) == 2


def test_failed_freeze_opens_no_transaction(
    setup: tuple[MemoryUow, FilesystemSourceStore, FixedClock],
) -> None:
    uow, store, clock = setup

    class BrokenInput(BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        ingest_file(
            IngestFile(BrokenInput(), "bad", "/bad", "operator"), uow, store, clock
        )
    assert uow.entries == 0
    assert not uow.sources.files
