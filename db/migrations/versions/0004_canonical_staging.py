"""Canonical accepted history, governed keys and exact observation linkage."""

import sqlalchemy as sa
from alembic import op

revision: str = "0004_canonical_staging"
down_revision: str | None = "0003_fx_snapshots"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('customer','product','order')",
            name="ck_canonical_entity_type",
        ),
        sa.CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_canonical_identity_uuid4",
        ),
    )
    op.create_table(
        "canonical_revision",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "identity_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_identity.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "identity_id", "revision_number", name="uq_canonical_identity_revision"
        ),
        sa.UniqueConstraint("candidate_revision_id", name="uq_canonical_candidate"),
        sa.UniqueConstraint(
            "identity_id", "id", name="uq_canonical_revision_identity_id"
        ),
        sa.CheckConstraint("revision_number > 0", name="ck_canonical_revision_number"),
        sa.CheckConstraint(
            "substring(id::text, 15, 1) = '4' "
            "AND substring(id::text, 20, 1) IN ('8','9','a','b')",
            name="ck_canonical_revision_uuid4",
        ),
    )
    op.create_table(
        "canonical_business_key",
        sa.Column(
            "identity_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_identity.id"),
            nullable=False,
        ),
        sa.Column("key_type", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), primary_key=True),
        sa.Column("effective_revision", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id", "effective_revision"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_canonical_key_revision",
        ),
        sa.CheckConstraint(
            "key_type IN ('customer','product','order')", name="ck_canonical_key_type"
        ),
        sa.CheckConstraint("length(value) > 0", name="ck_canonical_key_value"),
    )
    op.create_table(
        "reobservation_link",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_revision.id"),
            nullable=False,
        ),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_revision_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id", "canonical_revision_id"],
            ["canonical_revision.identity_id", "canonical_revision.id"],
            name="fk_reobservation_canonical",
        ),
        sa.UniqueConstraint("candidate_revision_id", name="uq_reobservation_candidate"),
    )
    op.add_column(
        "dependency_record",
        sa.Column("target_candidate_revision_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_dependency_target_candidate",
        "dependency_record",
        "candidate_revision",
        ["target_candidate_revision_id"],
        ["id"],
    )
    # These validated FKs fail on dangling pre-existing values; no evidence erased.
    op.create_foreign_key(
        "fk_dependency_resolved_entity",
        "dependency_record",
        "canonical_identity",
        ["resolved_entity_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_dependency_resolved_entity", "dependency_record", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_dependency_target_candidate", "dependency_record", type_="foreignkey"
    )
    op.drop_column("dependency_record", "target_candidate_revision_id")
    op.drop_table("reobservation_link")
    op.drop_table("canonical_business_key")
    op.drop_table("canonical_revision")
    op.drop_table("canonical_identity")
