"""Add lead_diagnoses.superseded_at and lead_diagnoses.superseded_by_id.

Revision ID: 0080_lead_diagnosis_supersede
Revises: 0079_topic_swap_fallback

도입문의 폼에 진료과·지역·키워드를 **틀리게** 적는 원장이 있다. 값이 비어 있으면
자동 생성이 거절돼 AE가 Admin에서 채우면 되지만, 틀린 값은 자동 생성을 통과한다 —
잘못된 질의로 측정이 끝나고 콜용 보고서까지 만들어진다. 그 뒤에는 손댈 길이 없었다.
`다시 측정`은 저장된 그 질의를 다시 묻고, `보고서 다시 만들기`는 같은 측정 결과로
PDF만 다시 만든다.

고쳐서 다시 만들려면 한 리드에 진단이 둘 이상 생긴다. 그래서 '리드당 1건'을
'리드당 활성 1건'으로 바꾸고, 옛 진단은 지우지 않고 갈음 관계로 남긴다 —
실제로 지출한 공급자 호출과 그때 무엇을 쟀는지가 기록으로 남아야 한다.

- `superseded_at`: 갈음된 시각. NULL이 활성이다.
- `superseded_by_id`: 갈음한 진단. 같은 테이블을 가리킨다.

Additive only. 기존 행은 전부 NULL이므로 활성으로 읽히고, 이는 지금 동작과 같다.
재실행해도 같은 결과가 되도록 컬럼 존재를 먼저 확인한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0080_lead_diagnosis_supersede"
down_revision: str | None = "0079_topic_swap_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in _inspector().get_columns(table))


def _has_index(table: str, name: str) -> bool:
    return any(item["name"] == name for item in _inspector().get_indexes(table))


def upgrade() -> None:
    if not _has_column("lead_diagnoses", "superseded_at"):
        op.add_column(
            "lead_diagnoses",
            sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("lead_diagnoses", "superseded_by_id"):
        op.add_column(
            "lead_diagnoses",
            sa.Column(
                "superseded_by_id",
                sa.UUID(as_uuid=True),
                # 갈음한 진단이 나중에 지워져도 갈음됐다는 사실 자체는 남긴다.
                sa.ForeignKey("lead_diagnoses.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    # 활성 진단 조회(리드당 1건 판정)가 매번 전체를 훑지 않도록 부분 인덱스를 둔다.
    if not _has_index("lead_diagnoses", "ix_lead_diagnoses_active_lead"):
        op.create_index(
            "ix_lead_diagnoses_active_lead",
            "lead_diagnoses",
            ["lead_id"],
            postgresql_where=sa.text("superseded_at IS NULL"),
        )


def downgrade() -> None:
    if _has_index("lead_diagnoses", "ix_lead_diagnoses_active_lead"):
        op.drop_index("ix_lead_diagnoses_active_lead", table_name="lead_diagnoses")
    if _has_column("lead_diagnoses", "superseded_by_id"):
        op.drop_column("lead_diagnoses", "superseded_by_id")
    if _has_column("lead_diagnoses", "superseded_at"):
        op.drop_column("lead_diagnoses", "superseded_at")
