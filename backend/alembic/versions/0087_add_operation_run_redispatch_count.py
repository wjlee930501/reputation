"""Add operation_runs.redispatch_count — 자율 복구가 이 실행을 다시 보낸 횟수.

Revision ID: 0087_add_operation_run_redispatch_count
Revises: 0086_preserve_historical_publications

QUEUED 유실(브로커에 있던 사본이 소비되지 않음)과 lease 만료 RUNNING 인수로 자율 복구가 새 task id로
다시 보낸 횟수다. `attempt_count`(claim 횟수, V0 정상 이어가기 포함)와 다르다. 유형별 예산(3회)을 다
쓰면 실행을 FAILED로 끝내고 원인별 사고 하나에 묶는다 — poison 메시지가 3시간마다 영원히 재배달되던
것을 막는다. publish 실패로 남은 REQUESTED 재시도와 미룬 실행(`not_before_at`)의 정시 재배달은 세지
않는다.

Additive only. 기존 행은 0이다(서버 기본값). 재실행해도 같은 결과가 되도록 컬럼 존재를 먼저 확인한다.

배포 순서: **이 마이그레이션을 먼저 적용하고, 코드는 그 다음 배포한다.** 새 ORM은 OperationRun을
읽을 때마다 이 컬럼을 SELECT한다. 0087 위의 옛 코드는 안전하다(NOT NULL이지만 서버 기본값이 있어 옛
INSERT도 통과한다).

Downgrade는 컬럼만 지운다. 옛 코드에는 예산이 없어 종전처럼 무한 재배달로 돌아간다.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0087_add_operation_run_redispatch_count"
down_revision: str | None = "0086_preserve_historical_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operation_runs"
_COLUMN = "redispatch_count"


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
