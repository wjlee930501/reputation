"""Drop the retired PLAN_8 tier from the plan enum and constrain content_schedules.plan

월 계약은 12/16/20편뿐이다. 0039가 남은 PLAN_8 행을 PLAN_12로 옮겼지만 enum 값 자체는
남아 있어 새 행이 다시 PLAN_8로 들어갈 수 있었다. PostgreSQL은 enum 값 삭제를 지원하지
않으므로 rename-swap으로 타입을 갈아끼운다. `plan` 타입을 쓰는 컬럼은 `hospitals.plan`
하나뿐이고, `content_schedules.plan`은 varchar라서 CHECK로 같은 계약을 강제한다.

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
    # 값 제거 전에 남은 행을 먼저 옮긴다. 0039가 이미 처리했더라도 재확인은 멱등하다.
    op.execute("UPDATE hospitals SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'")
    op.execute("UPDATE content_schedules SET plan = 'PLAN_12' WHERE plan = 'PLAN_8'")

    op.execute("ALTER TYPE plan RENAME TO plan_old")
    op.execute("CREATE TYPE plan AS ENUM ('PLAN_12', 'PLAN_16', 'PLAN_20')")
    op.execute(
        "ALTER TABLE hospitals ALTER COLUMN plan TYPE plan USING plan::text::plan"
    )
    op.execute("DROP TYPE plan_old")

    op.execute(
        "ALTER TABLE content_schedules ADD CONSTRAINT ck_content_schedules_plan "
        "CHECK (plan IN ('PLAN_12', 'PLAN_16', 'PLAN_20'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE content_schedules DROP CONSTRAINT ck_content_schedules_plan")

    op.execute("ALTER TYPE plan RENAME TO plan_new")
    op.execute("CREATE TYPE plan AS ENUM ('PLAN_16', 'PLAN_12', 'PLAN_8', 'PLAN_20')")
    op.execute(
        "ALTER TABLE hospitals ALTER COLUMN plan TYPE plan USING plan::text::plan"
    )
    op.execute("DROP TYPE plan_new")
