"""Restore the durable hospital operating-change schema already applied in production.

Production was stamped with this revision after the schema had been applied, but the
migration file was never committed.  The existence guards are intentional: they let a
database whose schema already contains these objects be reconciled without trying to
create them a second time, while a fresh database still receives the complete schema.

Revision ID: 0094_add_hospital_operating_changes
Revises: 0092_publish_without_references_or_verdict
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0094_add_hospital_operating_changes"
down_revision: str | None = "0092_publish_without_references_or_verdict"
branch_labels: str | None = None
depends_on: str | None = None

_CHANGES = "hospital_operating_changes"
_ADJUSTMENTS = "operating_change_adjustments"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in _inspector().get_columns(table))


def _has_index(table: str, columns: tuple[str, ...]) -> bool:
    return any(
        tuple(item.get("column_names") or ()) == columns
        and not bool(item.get("unique"))
        for item in _inspector().get_indexes(table)
    )


def upgrade() -> None:
    if not _has_column("hospitals", "operating_policy_version"):
        op.add_column(
            "hospitals",
            sa.Column(
                "operating_policy_version",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )

    if not _inspector().has_table(_CHANGES):
        op.create_table(
            _CHANGES,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("request_sha256", sa.String(length=64), nullable=False),
            sa.Column("request_text", sa.Text(), nullable=False),
            sa.Column("conversation_reference", sa.String(length=500), nullable=True),
            sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("requested_by_label", sa.String(length=255), nullable=False),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("operation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "version > 0",
                name="ck_hospital_operating_changes_version_positive",
            ),
            sa.ForeignKeyConstraint(
                ["hospital_id"],
                ["hospitals.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["requested_by_id"],
                ["admin_users.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["operation_run_id"],
                ["operation_runs.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "hospital_id",
                "idempotency_key",
                name="uq_hospital_operating_changes_hospital_id_idempotency_key",
            ),
            sa.UniqueConstraint(
                "hospital_id",
                "version",
                name="uq_hospital_operating_changes_hospital_id_version",
            ),
        )
    if not _has_index(_CHANGES, ("hospital_id",)):
        op.create_index(
            "ix_hospital_operating_changes_hospital_id",
            _CHANGES,
            ["hospital_id"],
        )

    if not _inspector().has_table(_ADJUSTMENTS):
        op.create_table(
            _ADJUSTMENTS,
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("change_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_revision", sa.Integer(), nullable=False),
            sa.Column("before_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("desired_brief", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("candidate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "state",
                sa.String(length=24),
                server_default=sa.text("'PENDING'"),
                nullable=False,
            ),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "source_revision >= 1",
                name="ck_operating_change_adjustments_source_revision_positive",
            ),
            sa.CheckConstraint(
                "state IN ("
                "'PENDING', 'UNCHANGED', 'REPLANNED', 'GENERATING', "
                "'NEEDS_OPERATOR', 'COMPLETE', 'SUPERSEDED', 'RETAINED', 'FAILED'"
                ")",
                name="ck_operating_change_adjustments_state",
            ),
            sa.ForeignKeyConstraint(
                ["hospital_id"],
                ["hospitals.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["change_id"],
                ["hospital_operating_changes.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["content_id"],
                ["content_items.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "change_id",
                "content_id",
                name="uq_operating_change_adjustments_change_id_content_id",
            ),
        )
    if not _has_index(_ADJUSTMENTS, ("hospital_id", "state")):
        op.create_index(
            "ix_operating_change_adjustments_hospital_state",
            _ADJUSTMENTS,
            ["hospital_id", "state"],
        )


def downgrade() -> None:
    if _inspector().has_table(_ADJUSTMENTS):
        op.drop_table(_ADJUSTMENTS)
    if _inspector().has_table(_CHANGES):
        op.drop_table(_CHANGES)
    if _has_column("hospitals", "operating_policy_version"):
        op.drop_column("hospitals", "operating_policy_version")
