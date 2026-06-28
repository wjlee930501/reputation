"""unique ai_query_target per hospital + name

리비전 복구 마이그레이션 — prod DB의 alembic_version은 본 리비전
('0030_unique_ai_query_target_hospital_name')에 stamp 되어 있으나, 정작 마이그레이션
파일은 어떤 브랜치에도 커밋된 적이 없어(작업 트리에서만 prod에 적용된 뒤 유실) 모든
backend 배포의 `alembic upgrade head`가 "Can't locate revision" 으로 실패했다.

본 파일은 그 유실된 리비전을 동일한 revision id 로 복원해 체인을 잇는다.
- prod: 이미 0030 에 stamp 되어 있으므로 upgrade 는 no-op (본문 미실행).
- 신규/CI DB: 0029 → 0030 으로 올라오며 아래 UNIQUE 제약을 생성.

제약: ai_query_targets 의 병원별 쿼리 타깃 중복 방지 — UNIQUE (hospital_id, name).
제약 추가 전 기존 중복은 가장 오래된 행만 남기고 정리한다. 멱등 가드(pg_constraint 존재
검사)로 재실행/부분적용에도 안전하게 만든다.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0030_unique_ai_query_target_hospital_name"
down_revision: Union[str, None] = "0029_add_content_generation_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "uq_ai_query_targets_hospital_name"


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM ai_query_targets a
        USING ai_query_targets b
        WHERE a.hospital_id = b.hospital_id
          AND a.name = b.name
          AND a.created_at > b.created_at
        """
    )
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
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'ai_query_targets'::regclass
                  AND contype = 'u'
                  AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE ai_query_targets
                    ADD CONSTRAINT {_CONSTRAINT} UNIQUE (hospital_id, name);
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE ai_query_targets DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
