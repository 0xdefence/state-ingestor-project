"""Same-run commands serialize across commits without weakening frozen evidence."""

import json
import select as io_select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from threading import Barrier, Event, Lock
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from services.application.ingest import IngestFile, ingest_file
from services.application.process import ProcessRun, process_run
from services.domain.runs import RunState
from services.infrastructure.db.models import PipelineCheckpointModel, RunModel
from services.infrastructure.db.uow import SqlAlchemyUnitOfWork
from services.infrastructure.source_store import FilesystemSourceStore
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import repositories, uow_for
from tests.support.graph_snapshot import graph_snapshot
from tests.unit.classify.test_rules import NOW, PRODUCT, SNAPSHOT


class AdvancingClock:
    def __init__(self):
        self.instant = NOW
        self.lock = Lock()

    def now(self):
        with self.lock:
            self.instant += timedelta(seconds=1)
            return self.instant


def test_two_workers_re_read_after_ownership_and_replay_unchanged(engine, tmp_path):
    clock = AdvancingClock()
    store = FilesystemSourceStore(tmp_path)
    run_id = ingest_file(
        IngestFile(
            BytesIO((PRODUCT + "\n\n").encode()), "products.csv", "test", "test"
        ),
        uow_for(engine),
        store,
        clock,
    ).run_id
    with uow_for(engine) as uow:
        uow.fx.add(SNAPSHOT)
        uow.commit()

    opened = Barrier(2)
    resume = Event()
    observations = []
    terminal_snapshots = []

    class PausedSource:
        def open(self, locator):
            opened.wait(timeout=10)
            assert resume.wait(timeout=10)
            return store.open(locator)

    class ObservedWork(SqlAlchemyUnitOfWork):
        def commit(self):
            super().commit()
            with Session(engine) as read:
                observations.append(
                    {
                        row.stage: row.record_ordinal
                        for row in read.scalars(select(PipelineCheckpointModel))
                    }
                )
                if read.get(RunModel, run_id).state == RunState.STAGED:
                    terminal_snapshots.append(graph_snapshot(engine, run_id))

    def factory():
        return ObservedWork(sessionmaker(engine), repositories)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            process_run, ProcessRun(run_id, 1), factory, PausedSource(), clock
        )
        opened.wait(timeout=10)  # parse start is committed, no source read yet
        second = pool.submit(process_run, ProcessRun(run_id, 1), factory, store, clock)
        try:
            # Wait for the actual PostgreSQL lock wait (or the old broken worker
            # finishing). This distinguishes serialization without a timing guess.
            deadline = monotonic() + 5
            while not second.done():
                with engine.connect() as conn:
                    waiting = conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND wait_event = 'advisory'"
                        )
                    )
                if waiting:
                    break
                assert monotonic() < deadline, (
                    "second worker did not wait for ownership"
                )
            resume.set()
            first_result = first.result(timeout=15)
            second_result = second.result(timeout=15)
        finally:
            resume.set()

    assert first_result == second_result
    assert first_result.state is RunState.STAGED
    assert first_result.promoted_count == 1
    assert observations[-1] == {"parse": 3, "normalise": 3}
    for stage in ("parse", "normalise"):
        ordinals = [row.get(stage, 0) for row in observations]
        assert ordinals == sorted(ordinals)
    before = graph_snapshot(engine, run_id)
    assert terminal_snapshots and all(row == before for row in terminal_snapshots)
    assert process_run(ProcessRun(run_id, 1), factory, store, clock) == first_result
    assert graph_snapshot(engine, run_id) == before


def test_stale_completion_and_failure_cannot_regress_terminal_run(engine, tmp_path):
    clock = AdvancingClock()
    store = FilesystemSourceStore(tmp_path)
    run_id = ingest_file(
        IngestFile(BytesIO(PRODUCT.encode()), "product.csv", "test", "test"),
        uow_for(engine),
        store,
        clock,
    ).run_id
    with uow_for(engine) as uow:
        uow.fx.add(SNAPSHOT)
        uow.commit()
    process_run(ProcessRun(run_id), lambda: uow_for(engine), store, clock)
    before = graph_snapshot(engine, run_id)
    for state, failure in (
        (RunState.PARSED, None),
        (RunState.NORMALISED, None),
        (RunState.PARSING, "parse_failed"),
        (RunState.NORMALISING, "normalise_failed"),
        (RunState.NORMALISED, "classify_failed"),
        (RunState.CLASSIFIED, "load_failed"),
    ):
        with uow_for(engine) as uow:
            uow.runs.set_state(run_id, state, stage_failure=failure)
            uow.commit()
    assert graph_snapshot(engine, run_id) == before


def test_checkpoint_replay_preserves_progress_and_validates_record_identity(
    engine, tmp_path
):
    clock = AdvancingClock()
    store = FilesystemSourceStore(tmp_path)
    run_id = ingest_file(
        IngestFile(BytesIO((PRODUCT + "\n").encode()), "product.csv", "test", "test"),
        uow_for(engine),
        store,
        clock,
    ).run_id
    from services.application.process import parse_run

    parse_run(run_id, 1, lambda: uow_for(engine), store, clock=clock)
    with uow_for(engine) as uow:
        checkpoint = uow.checkpoints.get(run_id, "parse")
        assert checkpoint is not None
        uow.checkpoints.advance(replace(checkpoint, batch_number=1, record_ordinal=1))
        uow.commit()
    with uow_for(engine) as uow:
        assert uow.checkpoints.get(run_id, "parse") == checkpoint
        uow.checkpoints.advance(replace(checkpoint, updated_at=clock.now()))
        uow.commit()
    with uow_for(engine) as uow:
        assert uow.checkpoints.get(run_id, "parse") == checkpoint
        raw = uow.raw_records.for_run(run_id)
        with pytest.raises(ValueError, match="identity mismatch"):
            uow.checkpoints.advance(replace(checkpoint, last_record_id=raw[0].id))


def test_processing_ownership_releases_after_worker_crash(engine):
    run_id = uuid4()
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import json, sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from services.infrastructure.db.processing_ownership import PostgresProcessingOwnership
config = json.loads(sys.stdin.readline())
engine = create_engine(config["database_url"])
with PostgresProcessingOwnership(sessionmaker(engine)).hold(UUID(config["run_id"])):
    print("owned", flush=True)
    sys.stdin.read()
""",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdin is not None and worker.stdout is not None
        worker.stdin.write(
            json.dumps(
                {
                    "database_url": engine.url.render_as_string(hide_password=False),
                    "run_id": str(run_id),
                }
            )
            + "\n"
        )
        worker.stdin.flush()
        assert io_select.select([worker.stdout], [], [], 10)[0], (
            "worker failed to claim"
        )
        assert worker.stdout.readline().strip() == "owned"
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database())"
                    )
                )
                == 1
            )
        worker.kill()  # No Python finally block or explicit unlock is allowed to run.
        worker.wait(timeout=5)
        with uow_for(engine).processing.hold(run_id):
            pass  # Would time out if the dead worker left ownership behind.
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)
