"""add admin_users.sessions_invalid_before

발급된 세션 토큰은 서버가 열거할 수 없어 개별 폐기가 불가능하다. 계정 단위 기준선을
두면 "이 시각 이전에 발급된 세션은 전부 무효"를 한 컬럼으로 표현할 수 있다.

비밀번호 재설정(탈취 대응)과 계정 재활성화(정지 전 세션 부활 차단)에서 갱신한다.
NULL은 "무효화한 적 없음"이라 기존 세션은 배포만으로 끊기지 않는다.

Revision ID: 0037_add_admin_session_invalidation
Revises: 0036_lead_diagnosis_hardening
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0037_add_admin_session_invalidation"
down_revision: Union[str, None] = "0036_lead_diagnosis_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("sessions_invalid_before", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "sessions_invalid_before")
