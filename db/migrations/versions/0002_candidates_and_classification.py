"""Append-only candidate interpretations, assessments, and review evidence.

FX and canonical entity FKs are added when their tables arrive in 0003/0004.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_candidates_classification"
down_revision: str | None = "0001_source_and_runs"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_revision",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "raw_record_id", sa.Uuid(), sa.ForeignKey("raw_record.id"), nullable=False
        ),
        sa.Column(
            "parent_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "raw_record_id", "revision_number", name="uq_candidate_raw_revision"
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_candidate_revision_number"),
        sa.CheckConstraint(
            "(revision_number = 1 AND parent_revision_id IS NULL AND origin = "
            "'normalise') OR (revision_number > 1 AND parent_revision_id IS "
            "NOT NULL AND origin <> 'normalise' AND origin <> '')",
            name="ck_candidate_parent",
        ),
        sa.CheckConstraint(
            "parent_revision_id <> id", name="ck_candidate_not_own_parent"
        ),
        sa.CheckConstraint(
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
    op.create_table(
        "transformation_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("field_path", sa.String(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "candidate_revision_id", "sequence", name="uq_transformation_sequence"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_transformation_sequence"),
        sa.CheckConstraint(
            "jsonb_typeof(before) = 'object' AND jsonb_typeof(after) = 'object'",
            name="ck_transformation_values",
        ),
    )
    op.create_table(
        "data_quality_issue",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("field_path", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False),
        sa.Column("tentative_cause", sa.String(), nullable=True),
        sa.CheckConstraint(
            "severity IN ('info','warning','error')", name="ck_issue_severity"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'", name="ck_issue_source_refs"
        ),
    )
    op.create_table(
        "classification_result",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("rules_version", sa.String(), nullable=False),
        sa.Column("fx_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("readiness", sa.String(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "candidate_revision_id",
            "rules_version",
            "fx_snapshot_id",
            name="uq_classification_revision_rules_snapshot",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "verdict IN "
            "('CLEAN','AUTO_REPAIRED','NEEDS_REVIEW','REJECTED','DUPLICATE')",
            name="ck_classification_verdict",
        ),
        sa.CheckConstraint(
            "readiness IN ('eligible','blocked_by_dependency','ineligible')",
            name="ck_classification_readiness",
        ),
    )
    op.create_table(
        "dependency_record",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "classification_id",
            sa.Uuid(),
            sa.ForeignKey("classification_result.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("referenced_business_value", sa.String(), nullable=False),
        sa.Column("resolved_entity_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('customer','product','referral')", name="ck_dependency_kind"
        ),
        sa.CheckConstraint(
            "state IN ('resolved','unresolved','ambiguous','blocked')",
            name="ck_dependency_state",
        ),
        sa.CheckConstraint(
            "resolved_entity_id IS NULL OR state = 'resolved'",
            name="ck_dependency_entity",
        ),
    )
    op.create_table(
        "duplicate_relation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "later_raw_id", sa.Uuid(), sa.ForeignKey("raw_record.id"), nullable=False
        ),
        sa.Column(
            "earlier_raw_id", sa.Uuid(), sa.ForeignKey("raw_record.id"), nullable=False
        ),
        sa.Column("comparison_scope", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "later_raw_id",
            "earlier_raw_id",
            "comparison_scope",
            name="uq_duplicate_relation",
        ),
        sa.CheckConstraint(
            "later_raw_id <> earlier_raw_id", name="ck_duplicate_distinct"
        ),
        sa.CheckConstraint(
            "comparison_scope IN ('same_run','earlier_run')", name="ck_duplicate_scope"
        ),
    )
    op.create_table(
        "review_item",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("run.id"), nullable=False),
        sa.Column(
            "raw_record_id", sa.Uuid(), sa.ForeignKey("raw_record.id"), nullable=False
        ),
        sa.Column(
            "classification_id",
            sa.Uuid(),
            sa.ForeignKey("classification_result.id"),
            nullable=False,
        ),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_state", sa.String(), nullable=False),
        sa.UniqueConstraint("classification_id", name="uq_review_classification"),
        sa.CheckConstraint("display_state = 'open'", name="ck_review_state"),
        sa.CheckConstraint(
            "jsonb_typeof(reasons) = 'array' AND jsonb_array_length(reasons) > 0",
            name="ck_review_reasons",
        ),
    )


def downgrade() -> None:
    op.drop_table("review_item")
    op.drop_table("duplicate_relation")
    op.drop_table("dependency_record")
    op.drop_table("classification_result")
    op.drop_table("data_quality_issue")
    op.drop_table("transformation_event")
    op.drop_table("candidate_revision")
