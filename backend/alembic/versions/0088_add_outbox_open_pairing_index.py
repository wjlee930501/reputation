"""Add a partial index for pairing a recovery notice with its open notice.

Revision ID: 0088_add_outbox_open_pairing_index
Revises: 0087_add_operation_run_redispatch_count

발송기는 매분 claim과 발송 전 종결에서 복구 알림(INCIDENT_RECOVERED)마다 같은 사고·같은 에피소드의
열림 알림(INCIDENT_OPEN)이 나갔는지(SENT·SENDING·보낸 뒤 결과 불명 HOLD), 아직 나갈 수 있는지,
모두 FAILED인지를 상관 서브쿼리로 묻는다. 그 짝 조회가 outbox 전체를 훑지 않도록
`(incident_id, state, created_at) WHERE notification_type = 'INCIDENT_OPEN'` 부분 인덱스를 둔다
(`created_at`은 에피소드 없는 옛 키의 시간 순서 규칙이 쓴다).

Additive only. 지금 outbox 크기에서는 CONCURRENTLY 없이 만들어도 잠금이 짧다(다른 마이그레이션과 같은
방식). 재실행해도 같은 결과가 되도록 인덱스 존재를 먼저 확인한다. 배포 순서는 상관없다 — 옛 코드도
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
