"""add legacy-safe hospital handoffs

Revision ID: 0040_add_hospital_handoffs
Revises: 0039_update_content_plan_tiers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040_add_hospital_handoffs"
down_revision: str | None = "0039_update_content_plan_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "hospital_handoffs"
IMMUTABLE_FUNCTION = "prevent_hospital_handoff_acceptance_change"
IMMUTABLE_TRIGGER = "trg_hospital_handoffs_acceptance_immutable"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state", sa.String(length=32), server_default="CONTRACT_PENDING", nullable=False
        ),
        sa.Column("sales_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ae_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contract_reference", sa.String(length=200), nullable=True),
        sa.Column("contract_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", sa.String(length=16), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acceptance_source", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('CONTRACT_PENDING', 'CONTRACTED', 'HANDOFF_ACCEPTED')",
            name="ck_hospital_handoffs_state",
        ),
        sa.CheckConstraint(
            "acceptance_source IN ('DIRECT_CREATE', 'LEAD_CONVERSION', 'LEGACY_BACKFILL')",
            name="ck_hospital_handoffs_acceptance_source",
        ),
        sa.CheckConstraint(
            "acceptance_source <> 'LEGACY_BACKFILL' OR state = 'HANDOFF_ACCEPTED'",
            name="ck_hospital_handoffs_legacy_is_accepted",
        ),
        sa.CheckConstraint(
            "(state = 'CONTRACT_PENDING' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NULL AND contract_effective_at IS NULL "
            "AND plan IS NULL AND sla_due_at IS NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NULL) "
            "OR (state = 'CONTRACTED' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NOT NULL AND contract_effective_at IS NOT NULL "
            "AND plan IS NOT NULL AND sla_due_at IS NOT NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NULL) "
            "OR (state = 'HANDOFF_ACCEPTED' AND acceptance_source = 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NULL AND ae_owner_id IS NULL "
            "AND contract_reference IS NULL AND contract_effective_at IS NULL "
            "AND plan IS NULL AND sla_due_at IS NULL "
            "AND accepted_by_id IS NULL AND accepted_at IS NOT NULL) "
            "OR (state = 'HANDOFF_ACCEPTED' AND acceptance_source <> 'LEGACY_BACKFILL' "
            "AND sales_owner_id IS NOT NULL AND ae_owner_id IS NOT NULL "
            "AND contract_reference IS NOT NULL AND contract_effective_at IS NOT NULL "
            "AND plan IS NOT NULL AND sla_due_at IS NOT NULL AND accepted_by_id IS NOT NULL "
            "AND accepted_at IS NOT NULL)",
            name="ck_hospital_handoffs_state_facts",
        ),
        sa.CheckConstraint(
            "plan IS NULL OR plan IN ('PLAN_12', 'PLAN_16', 'PLAN_20')",
            name="ck_hospital_handoffs_plan",
        ),
        sa.CheckConstraint("version >= 1", name="ck_hospital_handoffs_version_positive"),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospitals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sales_owner_id"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["ae_owner_id"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["accepted_by_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hospital_id", name="uq_hospital_handoffs_hospital_id"),
    )

    op.execute(
        sa.text(
            "INSERT INTO hospital_handoffs ("
            "id, hospital_id, state, accepted_at, acceptance_source, version"
            ") SELECT id, id, 'HANDOFF_ACCEPTED', now(), 'LEGACY_BACKFILL', 1 FROM hospitals"
        )
    )
    op.execute(
        sa.text(
            f"CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger AS $$ "
            "BEGIN "
            "IF (OLD.accepted_by_id IS NOT NULL "
            "AND NEW.accepted_by_id IS DISTINCT FROM OLD.accepted_by_id) "
            "OR (OLD.accepted_at IS NOT NULL AND NEW.accepted_at IS DISTINCT FROM OLD.accepted_at) "
            "THEN RAISE EXCEPTION 'accepted_by_id and accepted_at are immutable once set'; "
            "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            f"CREATE TRIGGER {IMMUTABLE_TRIGGER} BEFORE UPDATE ON {TABLE_NAME} "
            f"FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {IMMUTABLE_TRIGGER} ON {TABLE_NAME}"))
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {IMMUTABLE_FUNCTION}()"))
    op.drop_table(TABLE_NAME)
