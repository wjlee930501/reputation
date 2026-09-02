"""Allow the system to acknowledge an automatically recovered incident.

An incident that the machine opened, retried and recovered on its own must not
wait for a person to click "확인 완료". The system closes it with
``acknowledged_by_id`` NULL; a non-NULL value keeps meaning "a person closed this".

Revision ID: 0061_allow_system_incident_acknowledgement
Revises: 0060_add_ai_query_target_tracking_set
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_allow_system_incident_acknowledgement"
down_revision: str | None = "0060_add_ai_query_target_tracking_set"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_incidents_acknowledgement_fact", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_acknowledgement_fact",
        "incidents",
        sa.text(
            "(state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL) OR "
            "(state <> 'ACKNOWLEDGED' AND acknowledged_at IS NULL "
            "AND acknowledged_by_id IS NULL)"
        ),
    )


def downgrade() -> None:
    system_acknowledged = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM incidents "
            "WHERE state = 'ACKNOWLEDGED' AND acknowledged_by_id IS NULL"
        )
    )
    if system_acknowledged:
        raise RuntimeError(
            "시스템이 자동으로 종료한 인시던트가 있어 이 버전으로 되돌릴 수 없습니다. "
            "데이터를 임의로 바꾸지 말고 현재 버전으로 다시 올린 뒤 개발팀에 문의해 주세요."
        )
    op.drop_constraint("ck_incidents_acknowledgement_fact", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incidents_acknowledgement_fact",
        "incidents",
        sa.text(
            "(state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL "
            "AND acknowledged_by_id IS NOT NULL) OR "
            "(state <> 'ACKNOWLEDGED' AND acknowledged_at IS NULL "
            "AND acknowledged_by_id IS NULL)"
        ),
    )
