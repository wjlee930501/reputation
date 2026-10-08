"""Allow acknowledged supersession without fabricating recovery.

Revision ID: 0085_allow_acknowledged_incident_supersession
Revises: 0084_add_content_revisions
"""

from __future__ import annotations

from alembic import op

revision: str = "0085_allow_acknowledged_incident_supersession"
down_revision: str | None = "0084_add_content_revisions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_incidents_recovery_fact", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_recovery_fact",
        "incidents",
        "(state = 'RECOVERED' AND recovered_at IS NOT NULL) OR "
        "(state IN ('OPEN', 'RETRYING') AND recovered_at IS NULL) OR "
        "state = 'ACKNOWLEDGED'",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM incidents "
        "WHERE state = 'ACKNOWLEDGED' AND recovered_at IS NULL) THEN "
        "RAISE EXCEPTION 'cannot downgrade: acknowledged supersession incidents exist; "
        "restore the newer application version or explicitly resolve those incidents'; "
        "END IF; END $$"
    )
    op.drop_constraint("ck_incidents_recovery_fact", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_recovery_fact",
        "incidents",
        "(state IN ('RECOVERED', 'ACKNOWLEDGED') AND recovered_at IS NOT NULL) OR "
        "(state IN ('OPEN', 'RETRYING') AND recovered_at IS NULL)",
    )
