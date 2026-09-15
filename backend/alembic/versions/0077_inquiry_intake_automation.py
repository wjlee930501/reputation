"""Public inquiry intake: specialty for auto diagnosis and acknowledgement SMS outcome.

Revision ID: 0077_inquiry_intake_automation
Revises: 0076_inquiry_internal_diagnosis
"""

import sqlalchemy as sa

from alembic import op

revision = "0077_inquiry_intake_automation"
down_revision = "0076_inquiry_internal_diagnosis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 진료과는 clinic_type과 분리한다 — clinic_type은 도입문의 표식이 차지하고 있어
    # 그 칸에 진료과를 쓰면 Admin 배지와 고객 발송 방어선이 이 리드를 놓친다.
    op.add_column("sales_leads", sa.Column("specialty", sa.String(length=100), nullable=True))
    op.add_column("sales_leads", sa.Column("ack_sms_status", sa.String(length=20), nullable=True))
    op.add_column("sales_leads", sa.Column("ack_sms_error", sa.Text(), nullable=True))
    op.add_column(
        "sales_leads",
        sa.Column("ack_sms_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_leads", "ack_sms_sent_at")
    op.drop_column("sales_leads", "ack_sms_error")
    op.drop_column("sales_leads", "ack_sms_status")
    op.drop_column("sales_leads", "specialty")
