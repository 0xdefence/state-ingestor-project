"""Frozen fixture and complete persisted-pipeline golden proof."""

from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from services.application.ingest import IngestFile, ingest_file
from services.application.normalise import normalise_run
from services.application.process import (
    ProcessRun,
    RetryRun,
    parse_run,
    process_run,
    retry_run,
)
from services.domain.raw import RawRecordKind
from services.domain.runs import RunState
from services.infrastructure.db.models import (
    CandidateRevisionModel,
    CanonicalBusinessKeyModel,
    CanonicalCurrentModel,
    CanonicalIdentityModel,
    CanonicalPromotionEventModel,
    CanonicalRevisionModel,
    ClassificationResultModel,
    DataQualityIssueModel,
    DependencyRecordModel,
    DuplicateRelationModel,
    PipelineCheckpointModel,
    PipelineEventModel,
    RawRecordModel,
    ReobservationLinkModel,
    ReviewDecisionModel,
    ReviewItemModel,
    RunModel,
    TransformationEventModel,
)
from services.infrastructure.fx_importer import import_fx_snapshot
from services.infrastructure.source_store import FilesystemSourceStore
from services.pipeline.normalise.candidates import NormaliseContext
from tests.integration.foundation.test_concurrent_ingest import engine as engine
from tests.integration.foundation.test_parse_recovery import uow_for
from tests.support.graph_snapshot import graph_snapshot
from tests.unit.normalise.test_fx import FIXTURE as FX_FIXTURE
from tests.unit.normalise.test_fx import MANIFEST as FX_MANIFEST
from tests.unit.pipeline.test_parse import FixedClock

SAMPLE = Path("data/messy_sample_data.csv")


def _ingest(engine: Engine, store: FilesystemSourceStore, source_bytes: bytes):
    return ingest_file(
        IngestFile(
            BytesIO(source_bytes),
            SAMPLE.name,
            str(SAMPLE),
            "golden-test",
        ),
        uow_for(engine),
        store,
        FixedClock(),
    )


def _reset_pipeline(engine: Engine, run_id) -> None:
    with Session(engine) as session:
        for model in (
            CanonicalCurrentModel,
            CanonicalPromotionEventModel,
            ReviewDecisionModel,
            ReobservationLinkModel,
            CanonicalBusinessKeyModel,
    CanonicalCurrentModel,
    CanonicalPromotionEventModel,
    ReviewDecisionModel,
            DependencyRecordModel,
            CanonicalRevisionModel,
            CanonicalIdentityModel,
            ReviewItemModel,
            ClassificationResultModel,
            DuplicateRelationModel,
            DataQualityIssueModel,
            TransformationEventModel,
            CandidateRevisionModel,
            PipelineCheckpointModel,
            PipelineEventModel,
            RawRecordModel,
        ):
            session.execute(delete(model))
        session.execute(
            update(RunModel)
            .where(RunModel.id == run_id)
            .values(
                state=RunState.INGESTED,
                stage_failure=None,
                fx_snapshot_id=None,
                rules_version=None,
                counts=None,
            )
        )
        session.commit()


def test_frozen_sample_complete_graph_and_normalise_recovery(
    engine: Engine, tmp_path: Path
) -> None:
    source_bytes = SAMPLE.read_bytes()
    assert sha256(source_bytes).hexdigest() == (
        "e550ba421ebaac800d2e734c513f65d2bc4197f8b622a2fc82230974744110c4"
    )
    assert (len(source_bytes), source_bytes.count(b"\n")) == (5953, 54)

    import_fx_snapshot(FX_FIXTURE, FX_MANIFEST, lambda: uow_for(engine))
    store = FilesystemSourceStore(tmp_path / "sources")
    ingested = _ingest(engine, store, source_bytes)
    result = process_run(
        ProcessRun(ingested.run_id, 10),
        lambda: uow_for(engine),
        store,
        FixedClock(),
    )

    with uow_for(engine) as uow:
        run = uow.runs.get(ingested.run_id)
        raw = uow.raw_records.for_run(ingested.run_id)
        revisions = uow.candidates.for_run(ingested.run_id)
        initial = [revision for revision in revisions if revision.revision_number == 1]
        terminal = {
            revision.raw_record_id: revision
            for revision in sorted(revisions, key=lambda item: item.revision_number)
        }
        classifications = [
            classification
            for revision in terminal.values()
            for classification in uow.classifications.for_revision(revision.id)
            if classification.rules_version == run.rules_version
            and classification.fx_snapshot_id == run.fx_snapshot_id
        ]

    raw_counts = Counter(record.kind.value for record in raw)
    assert raw_counts == {
        "data": 48,
        "header": 1,
        "repeated_header": 1,
        "blank": 2,
    }
    assert len(raw) == 52
    assert Counter(
        record.fields[0]
        for record in raw
        if record.kind is RawRecordKind.DATA and record.fields
    ) == {"CUSTOMER": 16, "PRODUCT": 16, "ORDER": 16}
    assert {
        (record.source_line_start, record.source_line_end)
        for record in raw
        if record.source_line_start != record.source_line_end
    } == {(15, 16), (36, 37)}
    assert {
        line
        for record in raw
        for line in range(record.source_line_start, record.source_line_end + 1)
    } == set(range(1, 55))
    assert {
        record.source_line_start: record.kind
        for record in raw
        if record.kind is not RawRecordKind.DATA
    } == {
        1: RawRecordKind.HEADER,
        18: RawRecordKind.BLANK,
        19: RawRecordKind.REPEATED_HEADER,
        45: RawRecordKind.BLANK,
    }
    assert {
        record.source_line_start: record.field_count
        for record in raw
        if record.kind is RawRecordKind.DATA and record.field_count != 10
    } == {13: 7, 14: 12, 24: 11, 31: 11, 39: 9}
    assert len(initial) == len(terminal) == len(classifications) == 48
    assert run.state is result.state is RunState.STAGED
    assert sum(run.counts.values()) == 48
    assert result.classification_counts == {
        "CLEAN": 15,
        "AUTO_REPAIRED": 2,
        "NEEDS_REVIEW": 29,
        "REJECTED": 1,
        "DUPLICATE": 1,
    }
    assert result.promoted_count == 17

    uninterrupted = graph_snapshot(engine, ingested.run_id)
    raw_ids = {row["id"] for row in uninterrupted["raw_record"]}
    candidate_ids = {
        row["id"] for row in uninterrupted["candidate_revision"]
    }
    classification_ids = {
        row["id"] for row in uninterrupted["classification_result"]
    }
    assert all(
        row["raw_record_id"] in raw_ids
        for row in uninterrupted["candidate_revision"]
    )
    assert all(
        row["candidate_revision_id"] in candidate_ids
        for row in uninterrupted["classification_result"]
    )
    assert all(
        row["classification_id"] in classification_ids
        for row in uninterrupted["review_item"]
    )
    assert all(
        row["candidate_revision_id"] in candidate_ids
        for row in uninterrupted["canonical_revision"]
    )
    assert len(uninterrupted["classification_result"]) == 48
    assert len(uninterrupted["duplicate_relation"]) == 1
    assert len(uninterrupted["canonical_revision"]) == 17
    assert process_run(
        ProcessRun(ingested.run_id, 10),
        lambda: uow_for(engine),
        store,
        FixedClock(),
    ) == result
    assert graph_snapshot(engine, ingested.run_id) == uninterrupted

    _reset_pipeline(engine, ingested.run_id)
    parse_run(
        ingested.run_id,
        10,
        lambda: uow_for(engine),
        store,
        clock=FixedClock(),
    )
    with uow_for(engine) as uow:
        uow.runs.pin_fx_snapshot(ingested.run_id, uow.fx.latest().id)
        uow.commit()

    def fail_second_batch(batch: int) -> None:
        if batch == 2:
            raise RuntimeError("injected normalisation failure")

    with pytest.raises(RuntimeError, match="injected normalisation"):
        normalise_run(
            ingested.run_id,
            10,
            lambda: uow_for(engine),
            NormaliseContext(FixedClock()),
            failure_injector=fail_second_batch,
        )
    recovered = retry_run(
        RetryRun(ingested.run_id, 10),
        lambda: uow_for(engine),
        store,
        FixedClock(),
    )
    assert recovered == result
    assert graph_snapshot(engine, ingested.run_id) == uninterrupted
