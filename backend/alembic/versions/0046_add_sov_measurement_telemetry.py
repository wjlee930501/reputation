"""유료 측정에도 검색·모델·토큰 계측을 저장한다.

`search_calls`는 무료 진단 스키마에만 있었고 그마저 아무도 채우지 않았다. 그래서
`tool_choice`를 required→auto로 바꾼 뒤 "그래도 검색이 매번 도니까 언급률이 높다"는
설명이 나왔을 때, 확인할 수도 반박할 수도 없었다. 설명할 수 없는 숫자는 팔 수 없다.

`answer_model`은 컬럼이 이미 있다 — 0035가 PRD F3-2를 위해 만들었는데 **ORM 모델이
그 컬럼을 선언하지 않아** 한 번도 채워지지 않았다. 이번 변경에서 모델에 선언을 더해
비로소 기록되기 시작한다. 스키마 변경이 아니라 드리프트 해소라 여기서는 다루지 않는다.

기존 행은 NULL로 둔다. 계측 이전 측정에 0을 채우면 "검색을 안 썼다"는 없는 사실이
만들어진다 — NULL은 '모름'이고 0은 '안 씀'이다.

Revision ID: 0046_add_sov_measurement_telemetry
Revises: 0045_add_lead_measurement_config
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046_add_sov_measurement_telemetry"
down_revision: str | None = "0045_add_lead_measurement_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sov_records", sa.Column("search_calls", sa.Integer(), nullable=True))
    op.add_column("sov_records", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("sov_records", sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sov_records", "output_tokens")
    op.drop_column("sov_records", "input_tokens")
    op.drop_column("sov_records", "search_calls")
