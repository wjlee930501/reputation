"""Constrain hospitals.plan and content_schedules.plan to the 12/16/20 contract

월 계약은 12/16/20편뿐이다. 0039가 남은 PLAN_8 행을 PLAN_12로 옮겼지만 값 자체를 막는
제약이 없어 새 행이 다시 PLAN_8로 들어갈 수 있었다.

PostgreSQL은 enum 값 삭제를 지원하지 않으므로 값을 없애려면 타입을 rename-swap해야
하는데, 그러면 `plan` 타입의 OID가 바뀐다. 배포는 롤링이라 이 마이그레이션은 옛
API·Worker 리비전이 아직 도는 동안 실행된다. 그 프로세스들은 asyncpg/psycopg2 풀의
연결을 계속 들고 있고, 연결마다 타입 OID 캐시와 prepared statement 캐시가 옛 OID를
가리킨다. 타입을 갈아끼우면 `plan`을 건드리는 병원·공개 질의가 연결이 모두 재활용될
때까지 stale type OID / InvalidCachedStatementError로 깨진다. 그래서 타입은 손대지 않고
CHECK 제약으로만 계약을 강제한다 — 제약 추가는 타입 정체성을 바꾸지 않는다.

폐기된 `PLAN_8` label은 enum 타입에 남지만 두 컬럼 모두 CHECK가 막으므로 어떤 행도 그
값을 가질 수 없다. `plan` 타입을 쓰는 컬럼은 `hospitals.plan` 하나뿐이고,
`content_schedules.plan`은 varchar라서 같은 CHECK를 그대로 쓴다.

Revision ID: 0071_plan_enum_cleanup
Revises: 0070_essence_evidence_noise_hash
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0071_plan_enum_cleanup"
down_revision: str | None = "0070_essence_evidence_noise_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 제약을 걸기 전에 남은 행을 먼저 옮긴다. 0039가 이미 처리했더라도 재확인은 멱등하다.
    op.execute("UPDATE hospitals SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'")
    op.execute("UPDATE content_schedules SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'")

    # hospitals.plan은 enum 컬럼이라 text로 캐스팅해 비교한다.
    op.execute(
        "ALTER TABLE hospitals ADD CONSTRAINT ck_hospitals_plan "
        "CHECK (plan::text IN ('PLAN_12', 'PLAN_16', 'PLAN_20'))"
    )
    op.execute(
        "ALTER TABLE content_schedules ADD CONSTRAINT ck_content_schedules_plan "
        "CHECK (plan IN ('PLAN_12', 'PLAN_16', 'PLAN_20'))"
    )


def downgrade() -> None:
    # 타입을 건드리지 않았으므로 되돌릴 것은 제약뿐이다. 0039와 마찬가지로 PLAN_12로
    # 옮긴 행의 원래 값은 복원하지 않는다(roll-forward only).
    op.execute("ALTER TABLE content_schedules DROP CONSTRAINT ck_content_schedules_plan")
    op.execute("ALTER TABLE hospitals DROP CONSTRAINT ck_hospitals_plan")
