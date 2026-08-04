"""add monthly_reports.doctor_pdf_path

원장용 월간 리포트는 AE용과 같은 데이터를 다른 편집으로 렌더한 별도 파일이다.
기존 행은 NULL로 남고, 화면은 그 경우 원장용 다운로드를 감춘다.

Revision ID: 0038_add_doctor_report_path
Revises: 0037_add_admin_session_invalidation
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0038_add_doctor_report_path"
down_revision: Union[str, None] = "0037_add_admin_session_invalidation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "monthly_reports",
        sa.Column("doctor_pdf_path", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monthly_reports", "doctor_pdf_path")
