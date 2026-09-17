"""Stable persisted-graph projection for complete pipeline recovery proofs."""

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from services.infrastructure.db.models import (
    CandidateRevisionModel,
    CanonicalBusinessKeyModel,
    CanonicalIdentityModel,
    CanonicalRevisionModel,
    ClassificationResultModel,
    DataQualityIssueModel,
    DependencyRecordModel,
    DuplicateRelationModel,
    PipelineCheckpointModel,
    RawRecordModel,
    ReobservationLinkModel,
    ReviewItemModel,
    RunModel,
    RunSourceOccurrenceModel,
    SourceFileModel,
    SourceOccurrenceModel,
    TransformationEventModel,
)


def _value(value: object, labels: dict[UUID, str]) -> object:
    if isinstance(value, UUID):
        return labels.get(value, str(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _value(item, labels) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_value(item, labels) for item in value]
    return value


def _row(row: object, labels: dict[UUID, str]) -> dict[str, object]:
    table = row.__class__.__table__
    return {
        column.name: _value(getattr(row, column.name), labels)
        for column in table.columns
    }


def graph_snapshot(engine: Engine, run_id: UUID) -> dict[str, object]:
    """Return complete domain evidence for one run in deterministic order.

    Canonical UUID4 values are represented by their candidate lineage so two
    executions can be compared without discarding canonical relationships.
    Pipeline lifecycle events are intentionally omitted: retry attempt history
    describes transactions, while this projection describes committed evidence.
    """
    with Session(engine) as session:
        run = session.get(RunModel, run_id)
        if run is None:
            raise LookupError(f"Unknown run: {run_id}")
        raws = session.scalars(
            select(RawRecordModel).where(RawRecordModel.run_id == run_id)
        ).all()
        raw_ids = {row.id for row in raws}
        candidates = session.scalars(select(CandidateRevisionModel)).all()
        candidates = [row for row in candidates if row.raw_record_id in raw_ids]
        candidate_ids = {row.id for row in candidates}
        classifications = session.scalars(select(ClassificationResultModel)).all()
        classifications = [
            row for row in classifications if row.candidate_revision_id in candidate_ids
        ]
        classification_ids = {row.id for row in classifications}
        canonical_revisions = session.scalars(select(CanonicalRevisionModel)).all()
        canonical_revisions = [
            row
            for row in canonical_revisions
            if row.candidate_revision_id in candidate_ids
        ]
        identity_ids = {row.identity_id for row in canonical_revisions}
        labels = {
            row.id: f"canonical-revision:{row.candidate_revision_id}"
            for row in canonical_revisions
        }
        labels.update(
            {
                row.identity_id: f"canonical-identity:{row.candidate_revision_id}"
                for row in canonical_revisions
            }
        )
        run_occurrences = session.scalars(
            select(RunSourceOccurrenceModel).where(
                RunSourceOccurrenceModel.run_id == run_id
            )
        ).all()
        occurrence_ids = {row.source_occurrence_id for row in run_occurrences}
        source_occurrences = [
            row
            for row in session.scalars(select(SourceOccurrenceModel)).all()
            if row.id in occurrence_ids
        ]
        sections: dict[str, list[Any]] = {
            "source_file": [session.get(SourceFileModel, run.source_file_id)],
            "source_occurrence": source_occurrences,
            "run_source_occurrence": run_occurrences,
            "raw_record": raws,
            "candidate_revision": candidates,
            "transformation_event": [
                row
                for row in session.scalars(select(TransformationEventModel)).all()
                if row.candidate_revision_id in candidate_ids
            ],
            "data_quality_issue": [
                row
                for row in session.scalars(select(DataQualityIssueModel)).all()
                if row.candidate_revision_id in candidate_ids
            ],
            "classification_result": classifications,
            "dependency_record": [
                row
                for row in session.scalars(select(DependencyRecordModel)).all()
                if row.classification_id in classification_ids
            ],
            "duplicate_relation": [
                row
                for row in session.scalars(select(DuplicateRelationModel)).all()
                if row.later_raw_id in raw_ids
            ],
            "review_item": session.scalars(
                select(ReviewItemModel).where(ReviewItemModel.run_id == run_id)
            ).all(),
            "canonical_identity": [
                row
                for row in session.scalars(select(CanonicalIdentityModel)).all()
                if row.id in identity_ids
            ],
            "canonical_revision": canonical_revisions,
            "canonical_business_key": [
                row
                for row in session.scalars(select(CanonicalBusinessKeyModel)).all()
                if row.identity_id in identity_ids
            ],
            "reobservation_link": [
                row
                for row in session.scalars(select(ReobservationLinkModel)).all()
                if row.candidate_revision_id in candidate_ids
            ],
            "pipeline_checkpoint": session.scalars(
                select(PipelineCheckpointModel).where(
                    PipelineCheckpointModel.run_id == run_id
                )
            ).all(),
        }
        result: dict[str, object] = {"run": _row(run, labels)}
        for name, rows in sections.items():
            encoded = [_row(row, labels) for row in rows if row is not None]
            result[name] = sorted(
                encoded,
                key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
            )
        return result
