"""Narrow append-only derived repositories sharing their caller's session."""

from dataclasses import replace
from datetime import UTC
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.domain.candidates import (
    CandidateRevision,
    CustomerCandidate,
    EvidenceField,
    OrderCandidate,
    ProductCandidate,
    RejectedCandidateShell,
)
from services.domain.fields import CandidateField, SourceRef
from services.domain.issues import (
    ClassificationResult,
    ComparisonScope,
    DataQualityIssue,
    DependencyKind,
    DependencyRecord,
    DependencyState,
    DuplicateRelation,
    IssueCode,
    Readiness,
    ReviewItem,
    ReviewReason,
    ReviewState,
    Severity,
    TransformationCode,
    TransformationEvent,
    Verdict,
)
from services.infrastructure.db.derived_codec import (
    array_json,
    decode,
    evidence_equal,
    object_json,
    read_as,
    read_tuple,
)
from services.infrastructure.db.models import (
    CandidateRevisionModel,
    ClassificationResultModel,
    DataQualityIssueModel,
    DependencyRecordModel,
    DuplicateRelationModel,
    RawRecordModel,
    ReviewItemModel,
    TransformationEventModel,
)


def _candidate(row: CandidateRevisionModel) -> CandidateRevision:
    payload = decode(row.payload)
    if not isinstance(
        payload,
        (CustomerCandidate, ProductCandidate, OrderCandidate, RejectedCandidateShell),
    ):
        raise ValueError("stored revision must contain a typed candidate")
    result = CandidateRevision(
        row.id,
        row.raw_record_id,
        row.revision_number,
        row.parent_revision_id,
        row.origin,
        payload,
        row.created_at.astimezone(UTC),
    )
    if result.entity_type != row.entity_type:
        raise ValueError("stored revision entity type mismatch")
    return result


def _transformation(row: TransformationEventModel) -> TransformationEvent:
    return TransformationEvent(
        row.id,
        row.candidate_revision_id,
        TransformationCode(row.operation),
        row.field_path,
        cast(EvidenceField, read_as(row.before, CandidateField)),
        cast(EvidenceField, read_as(row.after, CandidateField)),
        row.sequence,
    )


def _issue(row: DataQualityIssueModel) -> DataQualityIssue:
    return DataQualityIssue(
        row.id,
        row.candidate_revision_id,
        IssueCode(row.code),
        Severity(row.severity),
        row.field_path,
        row.summary,
        read_tuple(row.source_refs, SourceRef),
        row.tentative_cause,
    )


def _classification(row: ClassificationResultModel) -> ClassificationResult:
    return ClassificationResult(
        row.id,
        row.candidate_revision_id,
        row.rules_version,
        row.fx_snapshot_id,
        Verdict(row.verdict),
        Readiness(row.readiness),
        row.evaluated_at.astimezone(UTC),
    )


def _dependency(row: DependencyRecordModel) -> DependencyRecord:
    return DependencyRecord(
        row.id,
        row.classification_id,
        DependencyKind(row.kind),
        row.referenced_business_value,
        row.resolved_entity_id,
        DependencyState(row.state),
    )


def _duplicate(row: DuplicateRelationModel) -> DuplicateRelation:
    return DuplicateRelation(
        row.id,
        row.later_raw_id,
        row.earlier_raw_id,
        ComparisonScope(row.comparison_scope),
    )


def _review(row: ReviewItemModel) -> ReviewItem:
    return ReviewItem(
        row.id,
        row.run_id,
        row.raw_record_id,
        row.classification_id,
        read_tuple(row.reasons, ReviewReason),
        row.created_at.astimezone(UTC),
        ReviewState(row.display_state),
    )


class SqlAlchemyCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, revision: CandidateRevision) -> None:
        revision = replace(revision, created_at=revision.created_at.astimezone(UTC))
        existing = self._session.get(CandidateRevisionModel, revision.id)
        if existing is not None:
            if not evidence_equal(_candidate(existing), revision) or not evidence_equal(
                existing.payload, object_json(revision.payload)
            ):
                raise ValueError("candidate revisions are immutable: identity mismatch")
            return
        if revision.parent_revision_id is not None:
            parent = self.get(revision.parent_revision_id)
            if (
                parent.raw_record_id != revision.raw_record_id
                or parent.revision_number + 1 != revision.revision_number
            ):
                raise ValueError("child revision must name its immediate parent")
        self._session.add(
            CandidateRevisionModel(
                id=revision.id,
                raw_record_id=revision.raw_record_id,
                parent_revision_id=revision.parent_revision_id,
                revision_number=revision.revision_number,
                entity_type=revision.entity_type,
                origin=revision.origin,
                payload=object_json(revision.payload),
                created_at=revision.created_at,
            )
        )
        self._session.flush()

    def get(self, revision_id: UUID) -> CandidateRevision:
        row = self._session.get(CandidateRevisionModel, revision_id)
        if row is None:
            raise LookupError(f"Unknown candidate revision: {revision_id}")
        return _candidate(row)

    def for_run(self, run_id: UUID) -> tuple[CandidateRevision, ...]:
        return tuple(
            _candidate(row)
            for row in self._session.scalars(
                select(CandidateRevisionModel)
                .join(
                    RawRecordModel,
                    RawRecordModel.id == CandidateRevisionModel.raw_record_id,
                )
                .where(RawRecordModel.run_id == run_id)
                .order_by(
                    RawRecordModel.source_line_start,
                    RawRecordModel.id,
                    CandidateRevisionModel.revision_number,
                )
            )
        )

    def add_transformation(self, event: TransformationEvent) -> None:
        existing = self._session.get(TransformationEventModel, event.id)
        if existing is not None:
            if (
                not evidence_equal(_transformation(existing), event)
                or not evidence_equal(existing.before, object_json(event.before))
                or not evidence_equal(existing.after, object_json(event.after))
            ):
                raise ValueError("Transformation identity mismatch")
            return
        self._session.add(
            TransformationEventModel(
                id=event.id,
                candidate_revision_id=event.candidate_revision_id,
                operation=event.operation,
                field_path=event.field_path,
                before=object_json(event.before),
                after=object_json(event.after),
                sequence=event.sequence,
            )
        )
        self._session.flush()

    def transformations(self, revision_id: UUID) -> tuple[TransformationEvent, ...]:
        return tuple(
            _transformation(row)
            for row in self._session.scalars(
                select(TransformationEventModel)
                .where(TransformationEventModel.candidate_revision_id == revision_id)
                .order_by(TransformationEventModel.sequence)
            )
        )

    def add_issue(self, issue: DataQualityIssue) -> None:
        existing = self._session.get(DataQualityIssueModel, issue.id)
        if existing is not None:
            if not evidence_equal(_issue(existing), issue) or not evidence_equal(
                existing.source_refs, array_json(issue.source_refs)
            ):
                raise ValueError("Issue identity mismatch")
            return
        self._session.add(
            DataQualityIssueModel(
                id=issue.id,
                candidate_revision_id=issue.candidate_revision_id,
                code=issue.code,
                severity=issue.severity,
                field_path=issue.field_path,
                summary=issue.summary,
                tentative_cause=issue.tentative_cause,
                source_refs=array_json(issue.source_refs),
            )
        )
        self._session.flush()

    def issues(self, revision_id: UUID) -> tuple[DataQualityIssue, ...]:
        return tuple(
            _issue(row)
            for row in self._session.scalars(
                select(DataQualityIssueModel)
                .where(DataQualityIssueModel.candidate_revision_id == revision_id)
                .order_by(DataQualityIssueModel.id)
            )
        )


class SqlAlchemyClassificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, result: ClassificationResult) -> None:
        result = replace(result, evaluated_at=result.evaluated_at.astimezone(UTC))
        existing = self._session.get(ClassificationResultModel, result.id)
        if existing is not None:
            if not evidence_equal(_classification(existing), result):
                raise ValueError("Classification identity mismatch")
            return
        self._session.add(
            ClassificationResultModel(
                id=result.id,
                candidate_revision_id=result.candidate_revision_id,
                rules_version=result.rules_version,
                fx_snapshot_id=result.fx_snapshot_id,
                verdict=result.verdict,
                readiness=result.readiness,
                evaluated_at=result.evaluated_at,
            )
        )
        self._session.flush()

    def for_revision(self, revision_id: UUID) -> tuple[ClassificationResult, ...]:
        return tuple(
            _classification(row)
            for row in self._session.scalars(
                select(ClassificationResultModel)
                .where(ClassificationResultModel.candidate_revision_id == revision_id)
                .order_by(
                    ClassificationResultModel.rules_version,
                    ClassificationResultModel.id,
                )
            )
        )

    def add_dependency(self, dependency: DependencyRecord) -> None:
        existing = self._session.get(DependencyRecordModel, dependency.id)
        if existing is not None:
            if not evidence_equal(_dependency(existing), dependency):
                raise ValueError("Dependency identity mismatch")
            return
        self._session.add(
            DependencyRecordModel(
                id=dependency.id,
                classification_id=dependency.classification_id,
                kind=dependency.kind,
                referenced_business_value=dependency.referenced_business_value,
                resolved_entity_id=dependency.resolved_entity_id,
                state=dependency.state,
            )
        )
        self._session.flush()

    def dependencies(self, classification_id: UUID) -> tuple[DependencyRecord, ...]:
        return tuple(
            _dependency(row)
            for row in self._session.scalars(
                select(DependencyRecordModel)
                .where(DependencyRecordModel.classification_id == classification_id)
                .order_by(DependencyRecordModel.id)
            )
        )

    def add_duplicate(self, duplicate: DuplicateRelation) -> None:
        existing = self._session.get(DuplicateRelationModel, duplicate.id)
        if existing is not None:
            if not evidence_equal(_duplicate(existing), duplicate):
                raise ValueError("Duplicate identity mismatch")
            return
        self._session.add(
            DuplicateRelationModel(
                id=duplicate.id,
                later_raw_id=duplicate.later_raw_id,
                earlier_raw_id=duplicate.earlier_raw_id,
                comparison_scope=duplicate.comparison_scope,
            )
        )
        self._session.flush()

    def duplicates(self, later_raw_id: UUID) -> tuple[DuplicateRelation, ...]:
        return tuple(
            _duplicate(row)
            for row in self._session.scalars(
                select(DuplicateRelationModel)
                .where(DuplicateRelationModel.later_raw_id == later_raw_id)
                .order_by(DuplicateRelationModel.id)
            )
        )


class SqlAlchemyReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, review: ReviewItem) -> None:
        review = replace(review, created_at=review.created_at.astimezone(UTC))
        existing = self._session.get(ReviewItemModel, review.id)
        if existing is not None:
            if not evidence_equal(_review(existing), review) or not evidence_equal(
                existing.reasons, array_json(review.reasons)
            ):
                raise ValueError("Review identity mismatch")
            return
        self._session.add(
            ReviewItemModel(
                id=review.id,
                run_id=review.run_id,
                raw_record_id=review.raw_record_id,
                classification_id=review.classification_id,
                reasons=array_json(review.reasons),
                created_at=review.created_at,
                display_state=review.display_state,
            )
        )
        self._session.flush()

    def for_run(self, run_id: UUID) -> tuple[ReviewItem, ...]:
        return tuple(
            _review(row)
            for row in self._session.scalars(
                select(ReviewItemModel)
                .where(ReviewItemModel.run_id == run_id)
                .order_by(ReviewItemModel.id)
            )
        )
