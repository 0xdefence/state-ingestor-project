"""SQLAlchemy 2 mappings for frozen evidence and resumable pipeline state."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from services.domain.raw import RawRecordKind
from services.domain.runs import RunState


def enum_values(enum_type: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_type]


class Base(DeclarativeBase):
    pass


class SourceFileModel(Base):
    __tablename__ = "source_file"
    __table_args__ = (
        Index("uq_source_file_sha256", "sha256", unique=True),
        CheckConstraint("byte_size >= 0", name="ck_source_file_byte_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_source_file_sha256"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    locator: Mapped[str]


class SourceOccurrenceModel(Base):
    __tablename__ = "source_occurrence"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_source_occurrence_idempotency_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_file_id: Mapped[UUID] = mapped_column(ForeignKey("source_file.id"))
    filename: Mapped[str]
    original_locator: Mapped[str]
    actor_label: Mapped[str]
    idempotency_key: Mapped[str | None]
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunModel(Base):
    __tablename__ = "run"
    __table_args__ = (
        Index(
            "uq_initial_run_per_source",
            "source_file_id",
            unique=True,
            postgresql_where=text("predecessor_run_id IS NULL"),
        ),
        UniqueConstraint("predecessor_run_id", name="uq_run_predecessor"),
        UniqueConstraint(
            "source_file_id", "reprocess_sequence", name="uq_run_sequence"
        ),
        CheckConstraint(
            "(predecessor_run_id IS NULL AND reprocess_sequence = 0) OR "
            "(predecessor_run_id IS NOT NULL AND reprocess_sequence > 0)",
            name="ck_run_reprocess_sequence",
        ),
        CheckConstraint("predecessor_run_id <> id", name="ck_run_not_own_predecessor"),
        CheckConstraint(
            "stage_failure IN ('parse_failed', 'normalise_failed', "
            "'classify_failed', 'load_failed')",
            name="ck_run_stage_failure",
        ),
        CheckConstraint("jsonb_typeof(counts) = 'object'", name="ck_run_counts"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_file_id: Mapped[UUID] = mapped_column(ForeignKey("source_file.id"))
    predecessor_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("run.id"))
    reprocess_sequence: Mapped[int]
    state: Mapped[RunState] = mapped_column(
        Enum(
            RunState,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="ck_run_state",
        )
    )
    stage_failure: Mapped[str | None]
    fx_snapshot_id: Mapped[UUID | None]
    requested_fx_snapshot_date: Mapped[date | None]
    rules_version: Mapped[str | None]
    build_revision: Mapped[str | None]
    counts: Mapped[dict[str, int] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunSourceOccurrenceModel(Base):
    __tablename__ = "run_source_occurrence"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "source_occurrence_id", name="uq_run_source_occurrence"
        ),
        CheckConstraint(
            "relation IN ('initiated', 'duplicate_upload')",
            name="ck_run_source_occurrence_relation",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"), primary_key=True)
    source_occurrence_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_occurrence.id"), primary_key=True
    )
    relation: Mapped[str]
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawRecordModel(Base):
    __tablename__ = "raw_record"
    __table_args__ = (
        CheckConstraint(
            "source_line_start >= 1 AND source_line_end >= source_line_start",
            name="ck_raw_record_source_lines",
        ),
        CheckConstraint(
            "jsonb_typeof(fields) = 'array' AND "
            "field_count = jsonb_array_length(fields)",
            name="ck_raw_record_fields",
        ),
        CheckConstraint(
            "jsonb_typeof(parse_metadata) = 'object'",
            name="ck_raw_record_parse_metadata",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"))
    source_line_start: Mapped[int]
    source_line_end: Mapped[int]
    kind: Mapped[RawRecordKind] = mapped_column(
        Enum(
            RawRecordKind,
            values_callable=enum_values,
            native_enum=False,
            create_constraint=True,
            name="ck_raw_record_kind",
        )
    )
    fields: Mapped[list[str]] = mapped_column(JSONB)
    field_count: Mapped[int]
    parse_metadata: Mapped[dict[str, object]] = mapped_column(JSONB)


class PipelineCheckpointModel(Base):
    __tablename__ = "pipeline_checkpoint"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('parse', 'normalise', 'classify', 'load')",
            name="ck_pipeline_checkpoint_stage",
        ),
        CheckConstraint(
            "batch_number >= 1 AND record_ordinal >= 1",
            name="ck_pipeline_checkpoint_position",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(primary_key=True)
    batch_number: Mapped[int]
    record_ordinal: Mapped[int]
    last_record_id: Mapped[UUID] = mapped_column(ForeignKey("raw_record.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineEventModel(Base):
    __tablename__ = "pipeline_event"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('parse', 'normalise', 'classify', 'load')",
            name="ck_pipeline_event_stage",
        ),
        CheckConstraint(
            "jsonb_typeof(facts) = 'object'", name="ck_pipeline_event_facts"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"))
    stage: Mapped[str]
    event_type: Mapped[str]
    facts: Mapped[dict[str, object]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateRevisionModel(Base):
    __tablename__ = "candidate_revision"
    __table_args__ = (
        UniqueConstraint(
            "raw_record_id", "revision_number", name="uq_candidate_raw_revision"
        ),
        CheckConstraint("revision_number >= 1", name="ck_candidate_revision_number"),
        CheckConstraint(
            "(revision_number = 1 AND parent_revision_id IS NULL AND origin = "
            "'normalise') OR (revision_number > 1 AND parent_revision_id IS "
            "NOT NULL AND origin <> 'normalise' AND origin <> '')",
            name="ck_candidate_parent",
        ),
        CheckConstraint("parent_revision_id <> id", name="ck_candidate_not_own_parent"),
        CheckConstraint(
            "(jsonb_typeof(payload) = 'object' AND payload ? 'entity_type' AND"
            " payload ? '$type' AND ((entity_type IN "
            "('customer','product','order') AND payload->>'entity_type' = "
            "entity_type AND payload->>'$type' = CASE entity_type WHEN "
            "'customer' THEN 'CustomerCandidate' WHEN 'product' THEN "
            "'ProductCandidate' WHEN 'order' THEN 'OrderCandidate' END) OR "
            "(entity_type IS NULL AND payload->'entity_type' = 'null'::jsonb "
            "AND payload->>'$type' = 'RejectedCandidateShell'))) IS TRUE",
            name="ck_candidate_payload",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    raw_record_id: Mapped[UUID] = mapped_column(ForeignKey("raw_record.id"))
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    entity_type: Mapped[str | None]
    revision_number: Mapped[int]
    origin: Mapped[str]
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransformationEventModel(Base):
    __tablename__ = "transformation_event"
    __table_args__ = (
        UniqueConstraint(
            "candidate_revision_id", "sequence", name="uq_transformation_sequence"
        ),
        CheckConstraint("sequence >= 1", name="ck_transformation_sequence"),
        CheckConstraint(
            "jsonb_typeof(before) = 'object' AND jsonb_typeof(after) = 'object'",
            name="ck_transformation_values",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    operation: Mapped[str]
    field_path: Mapped[str]
    before: Mapped[dict[str, object]] = mapped_column(JSONB)
    after: Mapped[dict[str, object]] = mapped_column(JSONB)
    sequence: Mapped[int]


class DataQualityIssueModel(Base):
    __tablename__ = "data_quality_issue"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info','warning','error')", name="ck_issue_severity"
        ),
        CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'", name="ck_issue_source_refs"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    code: Mapped[str]
    severity: Mapped[str]
    field_path: Mapped[str]
    summary: Mapped[str]
    source_refs: Mapped[list[object]] = mapped_column(JSONB)
    tentative_cause: Mapped[str | None]


class ClassificationResultModel(Base):
    __tablename__ = "classification_result"
    __table_args__ = (
        UniqueConstraint(
            "candidate_revision_id",
            "rules_version",
            "fx_snapshot_id",
            name="uq_classification_revision_rules_snapshot",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "verdict IN "
            "('CLEAN','AUTO_REPAIRED','NEEDS_REVIEW','REJECTED','DUPLICATE')",
            name="ck_classification_verdict",
        ),
        CheckConstraint(
            "readiness IN ('eligible','blocked_by_dependency','ineligible')",
            name="ck_classification_readiness",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    rules_version: Mapped[str]
    fx_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fx_snapshot.id", name="fk_classification_fx_snapshot")
    )
    verdict: Mapped[str]
    readiness: Mapped[str]
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DependencyRecordModel(Base):
    __tablename__ = "dependency_record"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('customer','product','referral')", name="ck_dependency_kind"
        ),
        CheckConstraint(
            "state IN ('resolved','unresolved','ambiguous','blocked')",
            name="ck_dependency_state",
        ),
        CheckConstraint(
            "resolved_entity_id IS NULL OR state = 'resolved'",
            name="ck_dependency_entity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    classification_id: Mapped[UUID] = mapped_column(
        ForeignKey("classification_result.id")
    )
    kind: Mapped[str]
    referenced_business_value: Mapped[str]
    resolved_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_identity.id", name="fk_dependency_resolved_entity")
    )
    target_candidate_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("candidate_revision.id", name="fk_dependency_target_candidate")
    )
    state: Mapped[str]


class DuplicateRelationModel(Base):
    __tablename__ = "duplicate_relation"
    __table_args__ = (
        UniqueConstraint(
            "later_raw_id",
            "earlier_raw_id",
            "comparison_scope",
            name="uq_duplicate_relation",
        ),
        CheckConstraint("later_raw_id <> earlier_raw_id", name="ck_duplicate_distinct"),
        CheckConstraint(
            "comparison_scope IN ('same_run','earlier_run')", name="ck_duplicate_scope"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    later_raw_id: Mapped[UUID] = mapped_column(ForeignKey("raw_record.id"))
    earlier_raw_id: Mapped[UUID] = mapped_column(ForeignKey("raw_record.id"))
    comparison_scope: Mapped[str]


class ReviewItemModel(Base):
    __tablename__ = "review_item"
    __table_args__ = (
        UniqueConstraint("classification_id", name="uq_review_classification"),
        CheckConstraint("display_state = 'open'", name="ck_review_state"),
        CheckConstraint(
            "jsonb_typeof(reasons) = 'array' AND jsonb_array_length(reasons) > 0",
            name="ck_review_reasons",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("run.id"))
    raw_record_id: Mapped[UUID] = mapped_column(ForeignKey("raw_record.id"))
    classification_id: Mapped[UUID] = mapped_column(
        ForeignKey("classification_result.id")
    )
    reasons: Mapped[list[object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_state: Mapped[str]


class FxSnapshotModel(Base):
    __tablename__ = "fx_snapshot"
    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_fx_snapshot_manifest_hash"),
        CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_fx_snapshot_hash"),
        CheckConstraint("source = 'ECB'", name="ck_fx_snapshot_source"),
        CheckConstraint("length(source_url) > 0", name="ck_fx_snapshot_url"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str]
    source_url: Mapped[str]


class FxRateModel(Base):
    __tablename__ = "fx_rate"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_fx_rate_currency"),
        CheckConstraint(
            "eur_reference_rate > 0 AND eur_reference_rate < 'Infinity'::numeric",
            name="ck_fx_rate_positive_finite",
        ),
        CheckConstraint(
            "currency <> 'EUR' OR eur_reference_rate = 1", name="ck_fx_rate_eur"
        ),
        CheckConstraint("length(source_url) > 0", name="ck_fx_rate_url"),
    )

    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("fx_snapshot.id"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    publication_date: Mapped[date] = mapped_column(primary_key=True)
    eur_reference_rate: Mapped[Decimal] = mapped_column(Numeric())
    source_url: Mapped[str]


class CanonicalIdentityModel(Base):
    __tablename__ = "canonical_identity"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('customer','product','order')",
            name="ck_canonical_entity_type",
        ),
        CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_canonical_identity_uuid4",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    entity_type: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CanonicalRevisionModel(Base):
    __tablename__ = "canonical_revision"
    __table_args__ = (
        UniqueConstraint(
            "identity_id", "revision_number", name="uq_canonical_identity_revision"
        ),
        UniqueConstraint("candidate_revision_id", name="uq_canonical_candidate"),
        UniqueConstraint("identity_id", "id", name="uq_canonical_revision_identity_id"),
        CheckConstraint("revision_number > 0", name="ck_canonical_revision_number"),
        CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_canonical_revision_uuid4",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_identity.id"))
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    revision_number: Mapped[int]
    staged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CanonicalBusinessKeyModel(Base):
    __tablename__ = "canonical_business_key"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "effective_revision"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_canonical_key_revision",
        ),
        CheckConstraint(
            "key_type IN ('customer','product','order')", name="ck_canonical_key_type"
        ),
        CheckConstraint("length(value) > 0", name="ck_canonical_key_value"),
    )
    identity_id: Mapped[UUID] = mapped_column(ForeignKey("canonical_identity.id"))
    key_type: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(primary_key=True)
    effective_revision: Mapped[UUID]


class ReobservationLinkModel(Base):
    __tablename__ = "reobservation_link"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_reobservation_canonical",
        ),
        UniqueConstraint("candidate_revision_id", name="uq_reobservation_candidate"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    identity_id: Mapped[UUID]
    canonical_revision_id: Mapped[UUID]


class ReviewDecisionModel(Base):
    __tablename__ = "review_decision"
    __table_args__ = (
        UniqueConstraint("review_item_id", "sequence", name="uq_decision_sequence"),
        UniqueConstraint("idempotency_key", name="uq_decision_idempotency"),
        UniqueConstraint("review_item_id", "id", name="uq_decision_review_id"),
        ForeignKeyConstraint(
            ["review_item_id", "supersedes_decision_id"],
            ["review_decision.review_item_id", "review_decision.id"],
            name="fk_decision_supersedes_same_review",
        ),
        CheckConstraint("sequence > 0", name="ck_decision_sequence"),
        CheckConstraint(
            "outcome IN ('approve','reject','acknowledge')", name="ck_decision_outcome"
        ),
        CheckConstraint("length(trim(operator_name)) > 0", name="ck_decision_operator"),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_decision_key"),
        CheckConstraint(
            "outcome <> 'reject' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_decision_reason",
        ),
        CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_decision_uuid4",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    review_item_id: Mapped[UUID] = mapped_column(ForeignKey("review_item.id"))
    candidate_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_revision.id")
    )
    sequence: Mapped[int]
    outcome: Mapped[str]
    operator_name: Mapped[str]
    reason: Mapped[str | None]
    idempotency_key: Mapped[str]
    supersedes_decision_id: Mapped[UUID | None]
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CanonicalPromotionEventModel(Base):
    __tablename__ = "canonical_promotion_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_promotion_revision",
        ),
        ForeignKeyConstraint(
            ["identity_id", "prior_current_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_promotion_prior",
        ),
        CheckConstraint(
            "action IN ('activate','withdraw')", name="ck_promotion_action"
        ),
        CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_promotion_uuid4",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    identity_id: Mapped[UUID]
    canonical_revision_id: Mapped[UUID]
    action: Mapped[str]
    decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("review_decision.id"))
    prior_current_revision_id: Mapped[UUID | None]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CanonicalCurrentModel(Base):
    __tablename__ = "canonical_current"
    __table_args__ = (
        ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_current_revision",
        ),
    )
    identity_id: Mapped[UUID] = mapped_column(primary_key=True)
    canonical_revision_id: Mapped[UUID]
