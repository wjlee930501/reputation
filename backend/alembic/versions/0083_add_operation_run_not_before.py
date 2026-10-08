"""Add operation_runs.not_before_at — 미룬 실행을 다시 보낼 수 있는 가장 이른 시각.

Revision ID: 0083_add_operation_run_not_before
Revises: 0082_add_content_reference_checks

V0 비용 보류는 다음 KST 비용 창(최대 약 24시간 뒤)까지 Celery countdown으로 기다렸다. 그
메시지는 브로커 visibility_timeout·봉투 TTL·실행 lease보다 오래 살아 배포와 재배달 사이에서
만료·중복됐다. 이제 워커는 실행을 QUEUED로 돌려놓고 이 시각만 남긴다. 자율 복구가 이 시각이
지난 뒤에만 새 task id·새 봉투로 다시 보낸다(QUEUED 유실 판정 유예도 이 시각을 따른다).

Additive only. 기존 행은 NULL이며 "미룬 적 없음"으로 읽힌다. 재실행해도 같은 결과가 되도록
컬럼 존재를 먼저 확인한다.

배포 순서: **이 마이그레이션을 먼저 적용하고, 코드는 그 다음 배포한다.**
- 새 ORM은 `OperationRun`을 조회할 때마다 이 컬럼을 SELECT한다. 0082 스키마에서 새 코드를 돌리면
  실행 기록을 읽는 모든 경로가 `UndefinedColumn`으로 실패한다.
- 반대로 0083 스키마에서 도는 옛 코드는 안전하다(추가형·NULL 허용 컬럼을 읽지 않는다).

Downgrade는 컬럼만 지운다. 옛 코드는 이 컬럼을 읽지 않으며, 미뤄 둔 실행은 QUEUED 유실 판정으로
다시 보내진다(비용 창 전이면 워커가 다시 미룬다).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083_add_operation_run_not_before"
down_revision: str | None = "0082_add_content_reference_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operation_runs"
_COLUMN = "not_before_at"


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
