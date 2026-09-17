"""CLI contracts through real application commands and transactional memory ports."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from typer import Typer
from typer.testing import CliRunner

from services.application.load import LoadResult
from services.application.process import StageResult, parse_run
from services.cli.main import create_app
from services.domain.runs import RunState
from services.infrastructure.runtime import Runtime, Settings
from services.infrastructure.source_store import FilesystemSourceStore
from tests.unit.ingest.test_ingest import MemoryRuns
from tests.unit.pipeline.test_parse import (
    RECOVERY_BYTES,
    FixedClock,
    ParseMemoryDatabase,
    ParseMemoryUow,
    fail_second_batch,
)

RUNNER = CliRunner()
CliFixture = tuple[Typer, ParseMemoryDatabase, Runtime, Path]


@pytest.fixture
def cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Typer, ParseMemoryDatabase, Runtime, Path]:
    database = ParseMemoryDatabase()
    snapshot_id = UUID(int=99)

    def pin_fx_snapshot(self, run_id, pinned):
        self.rows[run_id] = replace(self.rows[run_id], fx_snapshot_id=pinned)

    monkeypatch.setattr(MemoryRuns, "pin_fx_snapshot", pin_fx_snapshot, raising=False)
    monkeypatch.setattr(
        MemoryRuns, "promoted_count", lambda _self, _run_id: 3, raising=False
    )
    monkeypatch.setattr(
        ParseMemoryUow,
        "fx",
        SimpleNamespace(latest=lambda: SimpleNamespace(id=snapshot_id)),
        raising=False,
    )

    def normalise(run_id, _batch_size, uow_factory, _context):
        with uow_factory() as work:
            count = len(work.raw_records.rows)
            work.runs.set_state(run_id, RunState.NORMALISED)
            work.commit()
        return StageResult(run_id, count)

    def classify(run_id, _registry, work, _clock):
        counts = {"CLEAN": 3, "NEEDS_REVIEW": 2}
        with work:
            work.runs.rows[run_id] = replace(
                work.runs.rows[run_id],
                state=RunState.CLASSIFIED,
                counts=counts,
                rules_version="test-rules",
            )
            work.commit()
        return SimpleNamespace(counts=counts)

    def load(run_id, uow_factory, _clock):
        with uow_factory() as work:
            work.runs.set_state(run_id, RunState.STAGED)
            work.commit()
        return LoadResult(run_id, 3)

    monkeypatch.setattr("services.application.process.normalise_run", normalise)
    monkeypatch.setattr("services.application.process.classify_run", classify)
    monkeypatch.setattr("services.application.process.stage_run", load)
    runtime = Runtime(
        database.factory, FilesystemSourceStore(tmp_path / "store"), FixedClock()
    )

    @contextmanager
    def factory(settings: Settings) -> Iterator[Runtime]:
        yield runtime

    source = tmp_path / "input.csv"
    source.write_bytes(RECOVERY_BYTES)
    return create_app(factory), database, runtime, source


def test_first_ingest_is_freeze_only(cli: CliFixture) -> None:
    app, database, runtime, source = cli
    source.write_bytes(b"\xff")
    result = RUNNER.invoke(app, ["ingest", str(source), "--actor-label", "Ada"])
    assert result.exit_code == 0, result.output
    assert "New source and run" in result.stdout
    assert "Exact file already ingested" not in result.stdout
    run = next(iter(database.runs.rows.values()))
    occurrence = next(iter(database.sources.occurrences.values()))
    assert f"Run: {run.id}" in result.stdout
    assert f"Occurrence: {occurrence.id}" in result.stdout
    assert occurrence.actor_label == "Ada"
    assert occurrence.filename == "input.csv"
    assert occurrence.original_locator == str(source)
    assert occurrence.ingested_at == FixedClock().now()
    assert run.state == RunState.INGESTED
    assert not database.raw_records.rows
    frozen = database.sources.get(run.source_file_id)
    with runtime.source_store.open(frozen.locator) as stream:
        assert stream.read() == b"\xff"


def test_duplicate_ingest_reports_reused_run(cli: CliFixture) -> None:
    app, database, _, source = cli
    assert RUNNER.invoke(app, ["ingest", str(source)]).exit_code == 0
    run_id = next(iter(database.runs.rows))
    # Arrange a completed prior ingest before asserting the exact-duplicate copy.
    database.runs.set_state(run_id, RunState.STAGED)
    renamed = source.with_name("renamed.csv")
    renamed.write_bytes(source.read_bytes())
    result = RUNNER.invoke(app, ["ingest", str(renamed), "--process"])
    assert result.exit_code == 0, result.output
    assert "Exact file already ingested" in result.stdout
    assert f"Run: {run_id}" in result.stdout
    assert "Occurrence:" in result.stdout
    assert len(database.runs.rows) == 1
    assert len(database.sources.occurrences) == 2
    assert not database.events.rows
    assert "parsed successfully" not in result.stdout.lower()


def test_idempotency_replay_retains_occurrence_and_run(cli: CliFixture) -> None:
    app, database, _, source = cli
    args = ["ingest", str(source), "--idempotency-key", "submission-1"]
    first = RUNNER.invoke(app, args)
    second = RUNNER.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    assert "Idempotency replay" in second.stdout
    assert "Exact file already ingested" not in second.stdout
    assert len(database.sources.occurrences) == len(database.runs.rows) == 1
    occurrence_id = next(iter(database.sources.occurrences))
    assert f"Occurrence: {occurrence_id}" in second.stdout


@pytest.mark.parametrize("command", ["process", "retry", "ingest"])
def test_processing_resumes_durable_pipeline(cli: CliFixture, command: str) -> None:
    app, database, runtime, source = cli
    assert RUNNER.invoke(app, ["ingest", str(source)]).exit_code == 0
    run_id = next(iter(database.runs.rows))
    with pytest.raises(RuntimeError, match="injected"):
        parse_run(
            run_id,
            2,
            database.factory,
            runtime.source_store,
            fail_second_batch,
            clock=FixedClock(),
        )
    prior_ids = set(database.raw_records.rows)
    args = (
        ["ingest", str(source), "--process"]
        if command == "ingest"
        else [command, str(run_id)]
    )
    result = RUNNER.invoke(app, [*args, "--batch-size", "2"])
    assert result.exit_code == 0, result.output
    assert f"Run: {run_id}" in result.stdout
    assert "later stages" not in result.stdout.lower()
    assert database.runs.get(run_id).state == RunState.STAGED
    assert database.runs.get(run_id).counts == {"CLEAN": 3, "NEEDS_REVIEW": 2}
    assert prior_ids <= set(database.raw_records.rows)
    assert len(database.raw_records.rows) == 5
    assert [
        e.facts["record_ordinal"]
        for e in database.events.rows
        if e.event_type == "batch_committed"
    ] == [2, 4, 5]
    if command == "ingest":
        assert "Resume checkpoint: parse, record 2" in result.stdout
    else:
        assert "Raw records: 5" in result.stdout
        assert "State: Processed" in result.stdout
        assert "Candidates normalised: 5" in result.stdout
        assert "Outcomes: {'CLEAN': 3, 'NEEDS_REVIEW': 2}" in result.stdout
        assert "Canonical revisions promoted: 3" in result.stdout


def test_ingest_process_dispatches_new_run(cli: CliFixture) -> None:
    app, database, _, source = cli
    result = RUNNER.invoke(app, ["ingest", str(source), "--process"])
    assert result.exit_code == 0, result.output
    assert len(database.raw_records.rows) == 5
    assert next(iter(database.runs.rows.values())).state == RunState.STAGED


def test_reprocess_creates_successor_without_parsing(cli: CliFixture) -> None:
    app, database, _, source = cli
    assert RUNNER.invoke(app, ["ingest", str(source)]).exit_code == 0
    first = next(iter(database.runs.rows.values()))
    occurrence_id = next(iter(database.sources.occurrences))
    result = RUNNER.invoke(
        app,
        ["reprocess", str(first.source_file_id), "--occurrence-id", str(occurrence_id)],
    )
    assert result.exit_code == 0, result.output
    assert "New reprocess run" in result.stdout
    successor = database.runs.get_terminal_run(first.source_file_id)
    assert successor.predecessor_run_id == first.id
    assert f"Run: {successor.id}" in result.stdout
    assert f"Occurrence: {occurrence_id}" in result.stdout
    assert successor.state == RunState.INGESTED
    assert not database.raw_records.rows
    duplicate = RUNNER.invoke(app, ["ingest", str(source)])
    assert f"Run: {successor.id}" in duplicate.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["process", "bad-uuid"],
        ["retry", "bad-uuid"],
        ["reprocess", "bad-uuid", "--occurrence-id", str(UUID(int=1))],
        ["reprocess", str(UUID(int=1)), "--occurrence-id", "bad-uuid"],
        ["process", str(UUID(int=1)), "--batch-size", "0"],
        ["retry", str(UUID(int=1)), "--batch-size", "-1"],
    ],
)
def test_invalid_boundary_inputs_do_not_open_transactions(
    cli: CliFixture, args: list[str]
) -> None:
    app, database, _, _ = cli
    result = RUNNER.invoke(app, args)
    assert result.exit_code != 0
    assert "Error" in result.output
    assert "Traceback" not in result.output
    assert not database.transactions


@pytest.mark.parametrize(
    "change", ["missing", "directory", "unreadable", "batch", "actor"]
)
def test_invalid_ingest_is_rejected_before_writes(cli: CliFixture, change: str) -> None:
    app, database, _, source = cli
    args = ["ingest", str(source)]
    if change == "missing":
        source.unlink()
    elif change == "directory":
        args[1] = str(source.parent)
    elif change == "unreadable":
        source.chmod(0)
    elif change == "batch":
        args += ["--batch-size", "0"]
    else:
        args += ["--actor-label", "   "]
    try:
        result = RUNNER.invoke(app, args)
    finally:
        if change == "unreadable":
            source.chmod(0o600)
    assert result.exit_code != 0
    assert "Error" in result.output
    assert "Traceback" not in result.output
    assert not database.transactions


@pytest.mark.parametrize(
    "args",
    [
        ["--database-url", "sqlite:///wrong.db"],
        ["--database-url", "not-a-url-with-password"],
        ["--database-url", "postgresql+psycopg://host"],
        ["--source-root", ""],
    ],
)
def test_invalid_settings_are_safe_errors(cli: CliFixture, args: list[str]) -> None:
    app, database, _, source = cli
    result = RUNNER.invoke(app, [*args, "ingest", str(source)])
    assert result.exit_code != 0
    assert "Error" in result.output
    assert "not-a-url-with-password" not in result.output
    assert "Traceback" not in result.output
    assert not database.transactions


def test_source_root_cannot_be_a_file(cli: CliFixture) -> None:
    app, database, _, source = cli
    result = RUNNER.invoke(app, ["--source-root", str(source), "ingest", str(source)])
    assert result.exit_code != 0
    assert "source_root" in result.output
    assert not database.transactions


def test_parse_failure_is_operator_error_and_retains_ingest(cli: CliFixture) -> None:
    app, database, _, source = cli
    source.write_bytes(b'ORDER,"unclosed')
    result = RUNNER.invoke(app, ["ingest", str(source), "--process"])
    assert result.exit_code != 0
    assert "unexpected end of data" in result.output
    assert "Traceback" not in result.output
    assert len(database.sources.occurrences) == 1
    assert next(iter(database.runs.rows.values())).stage_failure == "parse_failed"


def test_unknown_run_and_later_stage_retry_are_useful(cli: CliFixture) -> None:
    app, database, _, source = cli
    missing = RUNNER.invoke(app, ["process", str(UUID(int=123))])
    assert missing.exit_code != 0
    assert "not found" in missing.output.lower()
    assert RUNNER.invoke(app, ["ingest", str(source)]).exit_code == 0
    run_id = next(iter(database.runs.rows))
    database.runs.set_state(
        run_id, RunState.NORMALISING, stage_failure="normalise_failed"
    )
    resumed = RUNNER.invoke(app, ["retry", str(run_id)])
    assert resumed.exit_code == 0, resumed.output
    assert "State: Processed" in resumed.output


def test_reprocess_does_not_offer_process_flag(cli: CliFixture) -> None:
    app, database, _, _ = cli
    result = RUNNER.invoke(
        app,
        [
            "reprocess",
            str(UUID(int=1)),
            "--occurrence-id",
            str(UUID(int=2)),
            "--process",
        ],
    )
    assert result.exit_code != 0
    assert not database.transactions


def test_environment_batch_size_is_validated(cli: CliFixture) -> None:
    app, database, _, source = cli
    result = RUNNER.invoke(app, ["ingest", str(source)], env={"BATCH_SIZE": "0"})
    assert result.exit_code != 0
    assert "batch_size" in result.output
    assert not database.transactions


def test_source_root_parent_must_be_writable(cli: CliFixture) -> None:
    app, database, _, source = cli
    root = source.parent / "read-only"
    root.mkdir()
    root.chmod(0o500)
    try:
        result = RUNNER.invoke(
            app, ["--source-root", str(root / "sources"), "ingest", str(source)]
        )
    finally:
        root.chmod(0o700)
    assert result.exit_code != 0
    assert "writable" in result.output
    assert not database.transactions
