"""Add stable incident episode identity.

Revision ID: 0047_add_incident_episode_seq
Revises: 0046_add_sov_measurement_telemetry

Deploy note: apply this revision before the episode_seq application code.
If incidents.episode_seq is missing, Incident reads fail. This PR does not deploy.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0047_add_incident_episode_seq"
down_revision: str | None = "0046_add_sov_measurement_telemetry"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("episode_seq", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint("ck_incidents_episode_seq_positive", "incidents", "episode_seq >= 1")


def downgrade() -> None:
    op.drop_constraint("ck_incidents_episode_seq_positive", "incidents", type_="check")
    op.drop_column("incidents", "episode_seq")
