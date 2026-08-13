"""3값 언급 판정을 유료 측정에도 도입한다 (PRD F3-7).

무료 진단은 `lead_diagnosis_results.mention_verdict`와 nullable `is_mentioned`를
이미 갖고 있었지만, 유료 `sov_records`는 non-null boolean 하나뿐이라 "확정하지 못함"을
기록할 자리가 없었다. 자리가 없으면 판정기의 불확실성이 True/False 중 하나로 접히고,
gpt-4o-mini는 애매할 때 True로 기운다 — 그 편향이 그대로 언급률이 된다.

기존 행은 백필하지 않는다. 이진 판정으로 만들어진 값을 MATCHED/NOT_MATCHED로 소급
표기하면 "동일 기관임을 확정했다"는 없는 사실을 만들어내기 때문이다. verdict가 NULL인
행은 3값 도입 이전 측정이라는 뜻이고, is_mentioned는 그대로 유효하다.

Revision ID: 0044_add_sov_mention_verdict
Revises: 0043_allow_system_report_artifact_validation
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044_add_sov_mention_verdict"
down_revision: str | None = "0043_allow_system_report_artifact_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sov_records", sa.Column("mention_verdict", sa.String(length=20), nullable=True))
    # AMBIGUOUS를 기록하려면 is_mentioned가 NULL을 받아야 한다.
    op.alter_column("sov_records", "is_mentioned", existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    # NULL(판정 보류)을 되돌릴 참값이 없다. non-null로 좁히기 전에 미언급으로 확정한다 —
    # 다운그레이드는 스키마 복구용이고, 이 손실은 복구 불가임을 명시한다.
    op.execute("UPDATE sov_records SET is_mentioned = false WHERE is_mentioned IS NULL")
    op.alter_column("sov_records", "is_mentioned", existing_type=sa.Boolean(), nullable=False)
    op.drop_column("sov_records", "mention_verdict")
