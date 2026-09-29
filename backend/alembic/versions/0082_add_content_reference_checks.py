"""Add content_items.reference_checks — 참고자료 URL 실제 검증 기록.

Revision ID: 0082_add_content_reference_checks
Revises: 0081_add_withheld_content_status

참고자료마다 한 건씩, 실제로 GET한 결과(url·url 지문·final_url·status·page_title·text_len·
verdict·reason·checked_at·verified_at)를 남긴다. 발행 직전 게이트는 **같은 URL**(지문)의
**신선한 통과** 기록이 모든 참고자료에 있을 때만 공개하고, 없거나 오래됐으면 워커가 다시
검증한다(2026-09-29 참고자료 전수 점검 — 빈 페이지·죽은 링크·주제 불일치 63건).

Additive only. 기존 행은 NULL이며 "아직 검증 기록 없음"으로 읽힌다. 공개된 글의 공개
판정과 공개 표면은 이 값을 보지 않는다(발행 직전 게이트만 본다). 재실행해도 같은 결과가
되도록 컬럼 존재를 먼저 확인한다.

배포 순서: **이 마이그레이션을 먼저 적용하고, 코드는 그 다음 배포한다.**
- 새 ORM은 `ContentItem`을 조회할 때마다 이 컬럼을 SELECT한다. 0081 스키마에서 새 코드를 돌리면
  콘텐츠를 읽는 모든 경로(공개 사이트 포함)가 `UndefinedColumn`으로 실패한다.
- 반대로 0082 스키마에서 도는 옛 코드는 안전하다(추가형·NULL 허용 컬럼을 읽지 않는다).
- [배포 안내](../../../docs/ops/deployment-runbook.md)의 10/1 배치 항목을 본다.

Downgrade는 컬럼만 지운다. 검증 기록은 파생 데이터라 다시 검증하면 복원되며, 옛 코드는
이 컬럼을 읽지 않는다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0082_add_content_reference_checks"
down_revision: str | None = "0081_add_withheld_content_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "content_items"
_COLUMN = "reference_checks"


def _has_column(table: str, column: str) -> bool:
    return any(
        item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table)
    )


def upgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    if _has_column(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
