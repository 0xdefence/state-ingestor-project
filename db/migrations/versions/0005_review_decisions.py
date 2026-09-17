"""Append-only review decisions, promotion events and replaceable current state."""

import sqlalchemy as sa
from alembic import op

revision = "0005_review_decisions"
down_revision = "0004_canonical_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_decision",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "review_item_id", sa.Uuid(), sa.ForeignKey("review_item.id"), nullable=False
        ),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("supersedes_decision_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("review_item_id", "sequence", name="uq_decision_sequence"),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_idempotency"),
        sa.UniqueConstraint("review_item_id", "id", name="uq_decision_review_id"),
        sa.ForeignKeyConstraint(
            ["review_item_id", "supersedes_decision_id"],
            ["review_decision.review_item_id", "review_decision.id"],
            name="fk_decision_supersedes_same_review",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_decision_sequence"),
        sa.CheckConstraint(
            "outcome IN ('approve','reject','acknowledge')", name="ck_decision_outcome"
        ),
        sa.CheckConstraint(
            "length(trim(operator_name)) > 0", name="ck_decision_operator"
        ),
        sa.CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_decision_key"),
        sa.CheckConstraint(
            "outcome <> 'reject' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_decision_reason",
        ),
        sa.CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_decision_uuid4",
        ),
    )
    op.create_table(
        "canonical_promotion_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_revision_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column(
            "decision_id", sa.Uuid(), sa.ForeignKey("review_decision.id"), nullable=True
        ),
        sa.Column("prior_current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_promotion_revision",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id", "prior_current_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_promotion_prior",
        ),
        sa.CheckConstraint(
            "action IN ('activate','withdraw')", name="ck_promotion_action"
        ),
        sa.CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_promotion_uuid4",
        ),
    )
    op.create_table(
        "canonical_current",
        sa.Column("identity_id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_revision_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_current_revision",
        ),
    )
    op.execute("""INSERT INTO canonical_current (identity_id, canonical_revision_id)
                  SELECT DISTINCT ON (identity_id) identity_id, id
                  FROM canonical_revision ORDER BY identity_id, revision_number DESC""")
    op.execute("""INSERT INTO canonical_promotion_event
                  (id, identity_id, canonical_revision_id, action, decision_id,
                   prior_current_revision_id, occurred_at)
                  SELECT r.id, r.identity_id, r.id, 'activate', NULL, NULL, r.staged_at
                  FROM canonical_revision r JOIN canonical_current c
                  ON c.canonical_revision_id=r.id""")


def downgrade() -> None:
    op.drop_table("canonical_current")
    op.drop_table("canonical_promotion_event")
    op.drop_table("review_decision")
