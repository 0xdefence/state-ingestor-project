"""Create frozen source, run, raw evidence and checkpoint tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_source_and_runs"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "source_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("locator", sa.String(), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_source_file_byte_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_source_file_sha256"),
    )
    op.create_index("uq_source_file_sha256", "source_file", ["sha256"], unique=True)
    op.create_table(
        "source_occurrence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_file_id", sa.Uuid(), sa.ForeignKey("source_file.id"), nullable=False
        ),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("original_locator", sa.String(), nullable=False),
        sa.Column("actor_label", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_source_occurrence_idempotency_key"
        ),
    )
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_file_id", sa.Uuid(), sa.ForeignKey("source_file.id"), nullable=False
        ),
        sa.Column(
            "predecessor_run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=True
        ),
        sa.Column("reprocess_sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(11), nullable=False),
        sa.Column("stage_failure", sa.String(), nullable=True),
        sa.Column("fx_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("requested_fx_snapshot_date", sa.Date(), nullable=True),
        sa.Column("rules_version", sa.String(), nullable=True),
        sa.Column("build_revision", sa.String(), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("predecessor_run_id", name="uq_run_predecessor"),
        sa.UniqueConstraint(
            "source_file_id", "reprocess_sequence", name="uq_run_sequence"
        ),
        sa.CheckConstraint(
            "(predecessor_run_id IS NULL AND reprocess_sequence = 0) OR "
            "(predecessor_run_id IS NOT NULL AND reprocess_sequence > 0)",
            name="ck_run_reprocess_sequence",
        ),
        sa.CheckConstraint(
            "predecessor_run_id <> id", name="ck_run_not_own_predecessor"
        ),
        sa.CheckConstraint(
            "state IN ('created', 'ingested', 'parsing', 'parsed', 'normalising', "
            "'normalised', 'classifying', 'classified', 'loading', 'staged')",
            name="ck_run_state",
        ),
        sa.CheckConstraint(
            "stage_failure IN ('parse_failed', 'normalise_failed', "
            "'classify_failed', 'load_failed')",
            name="ck_run_stage_failure",
        ),
        sa.CheckConstraint("jsonb_typeof(counts) = 'object'", name="ck_run_counts"),
    )
    op.create_index(
        "uq_initial_run_per_source",
        "run",
        ["source_file_id"],
        unique=True,
        postgresql_where=sa.text("predecessor_run_id IS NULL"),
    )
    op.create_table(
        "run_source_occurrence",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column(
            "source_occurrence_id",
            sa.Uuid(),
            sa.ForeignKey("source_occurrence.id"),
            primary_key=True,
        ),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relation IN ('initiated', 'duplicate_upload')",
            name="ck_run_source_occurrence_relation",
        ),
    )
    op.create_unique_constraint(
        "uq_run_source_occurrence",
        "run_source_occurrence",
        ["run_id", "source_occurrence_id"],
    )
    op.create_table(
        "raw_record",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("source_line_start", sa.Integer(), nullable=False),
        sa.Column("source_line_end", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("field_count", sa.Integer(), nullable=False),
        sa.Column("parse_metadata", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "source_line_start >= 1 AND source_line_end >= source_line_start",
            name="ck_raw_record_source_lines",
        ),
        sa.CheckConstraint(
            "kind IN ('data', 'blank', 'header', 'repeated_header')",
            name="ck_raw_record_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(fields) = 'array' AND "
            "field_count = jsonb_array_length(fields)",
            name="ck_raw_record_fields",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parse_metadata) = 'object'",
            name="ck_raw_record_parse_metadata",
        ),
    )
    op.create_table(
        "pipeline_checkpoint",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column("stage", sa.String(), primary_key=True),
        sa.Column("batch_number", sa.Integer(), nullable=False),
        sa.Column("record_ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "last_record_id", sa.Uuid(), sa.ForeignKey("raw_record.id"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('parse', 'normalise', 'classify', 'load')",
            name="ck_pipeline_checkpoint_stage",
        ),
        sa.CheckConstraint(
            "batch_number >= 1 AND record_ordinal >= 1",
            name="ck_pipeline_checkpoint_position",
        ),
    )
    op.create_table(
        "pipeline_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('parse', 'normalise', 'classify', 'load')",
            name="ck_pipeline_event_stage",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts) = 'object'", name="ck_pipeline_event_facts"
        ),
    )


def downgrade() -> None:
    op.drop_table("pipeline_event")
    op.drop_table("pipeline_checkpoint")
    op.drop_table("raw_record")
    op.drop_table("run_source_occurrence")
    op.drop_table("run")
    op.drop_table("source_occurrence")
    op.drop_table("source_file")
