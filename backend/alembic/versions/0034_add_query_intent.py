"""add query_intent to query_matrix

언급률 분모에서 '이길 수 없는 질문'을 빼기 위한 유형 구분.

지역이 없는 의학 설명 질문("무릎 통증 초기 증상이 뭔지 알려줘")은 AI가 특정 의원
이름을 댈 이유가 없다 — 실측 시 Mayo Clinic·대학병원을 인용한다. 전체 질문의 22%가
여기 해당하는데 언급률 분모에 함께 들어 있어, 병원이 무엇을 하든 그만큼 희석됐다.

백필: 기존 행은 INFO 템플릿의 고정부가 텍스트에 있으면 INFO, 아니면 LOCAL.
판별 못 하면 LOCAL(분모 포함) — 잘못 INFO로 빼면 실제 성과가 리포트에서 조용히
사라지지만, 잘못 LOCAL로 두면 기존과 같은 희석일 뿐이라 두 오류의 무게가 다르다.

Revision ID: 0034_add_query_intent
Revises: 0033_add_cancelled_content_status
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0034_add_query_intent"
down_revision: Union[str, None] = "0033_add_cancelled_content_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# sov_engine._TEMPLATE_SPECS의 INFO 템플릿 고정부와 일치해야 한다.
# 여기 하드코딩하는 이유: 마이그레이션은 과거 시점의 스키마를 다루므로 애플리케이션
# 코드를 import하면 미래의 변경에 끌려간다(0034가 0040의 템플릿으로 백필하게 된다).
_INFO_MARKERS = (
    "초기 증상이 뭔지 알려줘",
    "치료하려면 어떤 전문의한테 가야 해?",
    "치료 비용이 얼마나 드는지 알려줘",
    "수술 후 회복 기간 얼마나 돼?",
)


def upgrade() -> None:
    op.add_column(
        "query_matrix",
        sa.Column(
            "query_intent",
            sa.String(length=20),
            nullable=False,
            server_default="LOCAL",
        ),
    )

    query_matrix = sa.table(
        "query_matrix",
        sa.column("query_text", sa.String),
        sa.column("query_intent", sa.String),
    )
    for marker in _INFO_MARKERS:
        op.execute(
            query_matrix.update()
            .where(query_matrix.c.query_text.like(f"%{marker}%"))
            .values(query_intent="INFO")
        )

    # server_default는 백필 전용이었다. 이후 삽입은 모델의 default="LOCAL"이 채운다
    # (fail-open — 유형을 놓쳐도 분모에 남을 뿐, 실제 성과가 사라지지는 않는다).
    op.alter_column("query_matrix", "query_intent", server_default=None)


def downgrade() -> None:
    op.drop_column("query_matrix", "query_intent")
