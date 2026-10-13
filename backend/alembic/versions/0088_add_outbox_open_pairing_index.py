"""Add a partial index for pairing a recovery notice with its open notice.

Revision ID: 0088_add_outbox_open_pairing_index
Revises: 0087_add_operation_run_redispatch_count

발송기는 매분 claim과 발송 전 종결에서 복구 알림(INCIDENT_RECOVERED)마다 같은 사고·같은 에피소드의
열림 알림(INCIDENT_OPEN)이 나갔는지(SENT·보낸 뒤 결과 불명 HOLD), 아직 나갈 수 있는지(SENDING 포함),
모두 FAILED인지를 상관 서브쿼리로 묻는다. 그 짝 조회가 outbox 전체를 훑지 않도록
`(incident_id, state, created_at) WHERE notification_type = 'INCIDENT_OPEN'` 부분 인덱스를 둔다
(`created_at`은 에피소드 없는 옛 키의 시간 순서 규칙이 쓴다).

Additive only. CONCURRENTLY 없이 만든다(다른 마이그레이션과 같은 방식) — 그동안 `notification_outbox`에
쓰기 잠금(SHARE)이 걸린다. 근거: outbox는 운영 알림 행만 쌓이는 작은 표다(병원 9곳, 하루 수십 건 규모의 알림
→ 수천 행 단위). 그 크기에서 인덱스 생성은 1초 안팎이라 매분 발송기·도입문의 접수의 enqueue가 잠깐 기다릴 뿐이다.
행 수를 이 파일에서 재지 않는다(로컬 DB 수치는 운영과 무관하다) — 배포 전에 운영 DB의 행 수를 읽기 전용으로
확인한다(`ops_sql_report --only=q7`, `docs/ops/deployment-runbook.md`의 Phase 1 배포 전 확인).
**약 100만 행을 넘으면** 이 마이그레이션에 맡기지 말고 배포 전에 Alembic 밖에서
`CREATE INDEX CONCURRENTLY ix_notification_outbox_open_pairing ON notification_outbox
(incident_id, state, created_at) WHERE notification_type = 'INCIDENT_OPEN'`로 먼저 만든다. 그러면 아래의
존재 확인이 생성을 건너뛴다. 재실행해도 같은 결과가 되도록 인덱스 존재를 먼저 확인한다. 배포 순서는 상관없다 — 옛 코드도
새 코드도 인덱스 없이 같은 결과를 낸다(느릴 뿐이다).

Downgrade는 인덱스만 지운다.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0088_add_outbox_open_pairing_index"
down_revision: str | None = "0087_add_operation_run_redispatch_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "notification_outbox"
_INDEX = "ix_notification_outbox_open_pairing"


def _has_index(table: str, name: str) -> bool:
    return any(item["name"] == name for item in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if _has_index(_TABLE, _INDEX):
        return
    op.create_index(
        _INDEX,
        _TABLE,
        ["incident_id", "state", "created_at"],
        postgresql_where=sa.text("notification_type = 'INCIDENT_OPEN'"),
    )


def downgrade() -> None:
    if _has_index(_TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
