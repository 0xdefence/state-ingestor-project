"""SQLAlchemy 2 mappings for frozen evidence and resumable pipeline state."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
