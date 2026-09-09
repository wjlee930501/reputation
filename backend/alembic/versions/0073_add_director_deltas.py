"""Add director deltas independently of the stable base.

Apply before rolling out readers/writers. Old instances remain compatible; no
existing rows or enum types are modified. No expiration or automatic retirement.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0073_add_director_deltas"
down_revision = "0072_add_base_essence_flag"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "director_deltas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hospital_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hospitals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("avoid_messages", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("prefer_topics", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("prefer_messages", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "RETIRED",
                native_enum=False,
                create_constraint=True,
                name="director_delta_status",
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "ADMIN",
                "DIRECTOR",
                native_enum=False,
                create_constraint=True,
                name="director_delta_source",
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_director_deltas_hospital_status_created",
        "director_deltas",
        ["hospital_id", "status", "created_at"],
    )


def downgrade():
    op.drop_table("director_deltas")
