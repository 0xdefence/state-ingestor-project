"""Pinned immutable ECB snapshots and classification snapshot lineage."""

import sqlalchemy as sa
from alembic import op

revision: str = "0003_fx_snapshots"
down_revision: str | None = "0002_candidates_classification"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "fx_snapshot",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.UniqueConstraint("manifest_hash", name="uq_fx_snapshot_manifest_hash"),
        sa.CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_fx_snapshot_hash"
        ),
        sa.CheckConstraint("source = 'ECB'", name="ck_fx_snapshot_source"),
        sa.CheckConstraint("length(source_url) > 0", name="ck_fx_snapshot_url"),
    )
    op.create_table(
        "fx_rate",
        sa.Column(
            "snapshot_id", sa.Uuid(), sa.ForeignKey("fx_snapshot.id"), primary_key=True
        ),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("publication_date", sa.Date(), primary_key=True),
        sa.Column("eur_reference_rate", sa.Numeric(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_fx_rate_currency"),
        sa.CheckConstraint(
            "eur_reference_rate > 0 AND eur_reference_rate < 'Infinity'::numeric",
            name="ck_fx_rate_positive_finite",
        ),
        sa.CheckConstraint(
            "currency <> 'EUR' OR eur_reference_rate = 1", name="ck_fx_rate_eur"
        ),
        sa.CheckConstraint("length(source_url) > 0", name="ck_fx_rate_url"),
    )
    # A validated FK fails closed on any pre-existing dangling classification.
    # Never erase old evidence to make a migration succeed.
    op.create_foreign_key(
        "fk_classification_fx_snapshot",
        "classification_result",
        "fx_snapshot",
        ["fx_snapshot_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_classification_fx_snapshot", "classification_result", type_="foreignkey"
    )
    op.drop_table("fx_rate")
    op.drop_table("fx_snapshot")
