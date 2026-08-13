"""무료 진단 접수 시점의 측정 정책 스냅샷 (측정 정책 v2).

리포트는 질의·모델·지시문을 공개하고 "직접 재현하실 수 있습니다"라고 판다. 그런데
지시문을 렌더 시점의 전역 상수에서 읽으면, 프롬프트를 바꾼 뒤 리포트를 재생성했을 때
공개된 조건과 실제 측정 조건이 어긋난다. 접수 시점에 조건을 붙잡아 둘 자리를 만든다.

기존 행은 NULL로 두고 v1(스냅샷 도입 이전 정책)으로 취급한다 — 실제로 어떤 조건이었는지
행마다 확인할 방법이 없으므로, 아는 척 백필하지 않는다.

Revision ID: 0045_add_lead_measurement_config
Revises: 0044_add_sov_mention_verdict
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0045_add_lead_measurement_config"
down_revision: str | None = "0044_add_sov_mention_verdict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lead_diagnoses",
        sa.Column("measurement_config", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lead_diagnoses", "measurement_config")
