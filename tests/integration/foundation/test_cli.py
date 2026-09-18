"""Exercise production composition against migrated PostgreSQL and frozen bytes."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from services.cli.main import app, create_app
from services.domain.runs import RunState
from services.infrastructure.db.models import (
    PipelineCheckpointModel,
    RawRecordModel,
    RunModel,
    SourceOccurrenceModel,
)
from services.infrastructure.runtime import Runtime, Settings, build_runtime
from tests.integration.foundation.test_migrations import (
    migration_config,
)
from tests.integration.foundation.test_migrations import postgres_url as postgres_url
from tests.unit.pipeline.test_parse import FixedClock


def test_cli_persists_ingest_process_retry_and_reprocess(
    postgres_url: str, tmp_path: Path
) -> None:
    command.upgrade(migration_config(postgres_url), "head")

    @contextmanager
    def factory(settings: Settings) -> Iterator[Runtime]:
        with build_runtime(settings, clock=FixedClock()) as runtime:
            yield runtime

    app = create_app(factory)
    runner = CliRunner()
    source = tmp_path / "source.csv"
    source.write_bytes(b"ORDER,1\nORDER,2\n")
    env = {
        "DATABASE_URL": postgres_url,
        "SOURCE_ROOT": str(tmp_path / "frozen"),
        "APPLICATION_BUILD_REVISION": "cli-build-first",
    }
    result = runner.invoke(app, ["ingest", str(source)], env=env)
    assert result.exit_code == 0, result.output
    engine = create_engine(postgres_url)
    try:
        with Session(engine) as session:
            first = session.scalars(select(RunModel)).one()
            run_id, source_id = first.id, first.source_file_id
            occurrence = session.scalars(select(SourceOccurrenceModel)).one()
            occurrence_id = occurrence.id
            assert occurrence.ingested_at == FixedClock().now()
            assert first.state == RunState.INGESTED
            assert first.build_revision == "cli-build-first"
            assert not session.scalars(select(RawRecordModel)).all()
        source.unlink()
        env["APPLICATION_BUILD_REVISION"] = "cli-build-later"
        for action in ["process", "retry"]:
            result = runner.invoke(
                app, [action, str(run_id), "--batch-size", "1"], env=env
            )
            assert result.exit_code == 0, result.output
            assert "State: Processed" in result.stdout
            assert "Raw records: 2" in result.stdout
            assert "Candidates normalised: 2" in result.stdout
            assert "Canonical revisions promoted: 0" in result.stdout
        with Session(engine) as session:
            assert len(session.scalars(select(RawRecordModel)).all()) == 2
            checkpoints = session.scalars(select(PipelineCheckpointModel)).all()
            assert {
                (checkpoint.stage, checkpoint.batch_number, checkpoint.record_ordinal)
                for checkpoint in checkpoints
            } == {("parse", 2, 2), ("normalise", 2, 2)}
            assert session.get(RunModel, run_id).state is RunState.STAGED
            assert session.get(RunModel, run_id).build_revision == "cli-build-first"
        result = runner.invoke(
            app,
            ["reprocess", str(source_id), "--occurrence-id", str(occurrence_id)],
            env=env,
        )
        assert result.exit_code == 0, result.output
        with Session(engine) as session:
            successor = session.scalars(
                select(RunModel).where(RunModel.predecessor_run_id == run_id)
            ).one()
            assert successor.state == RunState.INGESTED
            assert successor.build_revision == "cli-build-later"
        missing = runner.invoke(
            app,
            ["reprocess", str(source_id), "--occurrence-id", str(UUID(int=999))],
            env=env,
        )
        assert missing.exit_code != 0
        assert "not found" in missing.output.lower()
        assert "Traceback" not in missing.output
    finally:
        engine.dispose()


def test_unmigrated_database_is_an_actionable_error(
    postgres_url: str, tmp_path: Path
) -> None:
    result = CliRunner().invoke(
        app,
        ["process", str(UUID(int=1))],
        env={"DATABASE_URL": postgres_url, "SOURCE_ROOT": str(tmp_path / "frozen")},
    )
    assert result.exit_code != 0
    assert "Database operation failed" in result.output
    assert "migrations" in result.output
    assert "Traceback" not in result.output
    assert postgres_url not in result.output
