"""unique constraint on ai_query_targets(hospital_id, name)

Revision ID: 0030_unique_ai_query_target_hospital_name
Revises: 0029_add_content_generation_claims
Create Date: 2026-06-21 00:00:00.000000

H1: V0 QueryMatrix에서 AIQueryTarget을 시드할 때 동시 실행(자동 시드 vs 수동 재시드,
Celery 재시도) 레이스로 (hospital_id, name) 중복이 생길 수 있었다. 인메모리 존재 체크만으로는
동시성에 안전하지 않으므로 DB 유니크 제약으로 막는다. 제약 추가 전 기존 중복은
가장 오래된 행만 남기고 정리한다(운영 데이터엔 보통 중복이 없어 no-op).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0030_unique_ai_query_target_hospital_name"
down_revision: Union[str, None] = "0029_add_content_generation_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) (hospital_id, name)별로 가장 오래된 created_at 행만 남기고 나머지 제거.
    op.execute(
        """
        DELETE FROM ai_query_targets a
        USING ai_query_targets b
        WHERE a.hospital_id = b.hospital_id
          AND a.name = b.name
          AND a.created_at > b.created_at
        """
    )
    # 2) created_at 동률 tie-break — id가 큰 쪽 제거.
    op.execute(
        """
        DELETE FROM ai_query_targets a
        USING ai_query_targets b
        WHERE a.hospital_id = b.hospital_id
          AND a.name = b.name
          AND a.created_at = b.created_at
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_ai_query_targets_hospital_name",
        "ai_query_targets",
        ["hospital_id", "name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ai_query_targets_hospital_name",
        "ai_query_targets",
        type_="unique",
    )
