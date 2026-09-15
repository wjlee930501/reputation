"""Add content_items.topic_swap_history and content_items.human_edited_at.

Revision ID: 0079_topic_swap_fallback
Revises: 0078_add_hospital_physicians

본문 표본 실패의 마지막 폴백 계단(주제 교체 1회)이 읽고 쓰는 두 값이다.

- `topic_swap_history`: 교체 이력. 비어 있음이 "아직 한 번도 교체하지 않았다"이고,
  길이가 생성 인시던트의 epoch가 된다. 코드에서는 NULL과 `[]`를 같게 읽는다.
- `human_edited_at`: 사람이 편집 가능한 필드를 **실제로** 바꾼 마지막 시각. 값이 있으면
  그 슬롯은 자동 교체 대상에서 빠진다.

Backfill은 보수적이다. admin 콘텐츠 PATCH가 지금까지 감사 기록을 남기지 않았으므로,
편집 사실을 단정할 수 있는 두 흔적만 쓴다 — 생성 뒤에 공개 텍스트가 바뀐 행
(`body_updated_at > generated_at`)과 시스템이 아닌 actor가 brief를 승인한 행.
배포 전에 제목·meta·FAQ만 고친 레거시 글은 식별할 수 없으며, 그 위험은 교체 조건의
`first_published_at IS NULL`(공개된 적 없는 글만 교체)이 제한한다.

Additive only. 재실행해도 같은 결과가 되도록 컬럼 존재를 먼저 확인한다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0079_topic_swap_fallback"
down_revision: str | None = "0078_add_hospital_physicians"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `content_target_planner`가 자동 brief에 남기는 actor. 사람이 승인한 brief와 구분한다.
SYSTEM_BRIEF_ACTORS = ("SYSTEM_EXPOSURE_PLANNER",)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in _inspector().get_columns(table))


def upgrade() -> None:
    if not _has_column("content_items", "topic_swap_history"):
        op.add_column(
            "content_items",
            sa.Column("topic_swap_history", postgresql.JSONB(), nullable=True),
        )
    if _has_column("content_items", "human_edited_at"):
        return

    op.add_column(
        "content_items",
        sa.Column("human_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE content_items SET human_edited_at ="
            " COALESCE(body_updated_at, brief_approved_at, created_at)"
            " WHERE human_edited_at IS NULL AND ("
            "   (body_updated_at IS NOT NULL AND generated_at IS NOT NULL"
            "    AND body_updated_at > generated_at)"
            "   OR (brief_approved_by IS NOT NULL AND btrim(brief_approved_by) <> ''"
            "       AND brief_approved_by NOT IN :system_actors)"
            " )"
        ).bindparams(sa.bindparam("system_actors", SYSTEM_BRIEF_ACTORS, expanding=True))
    )


def downgrade() -> None:
    if _has_column("content_items", "human_edited_at"):
        op.drop_column("content_items", "human_edited_at")
    if _has_column("content_items", "topic_swap_history"):
        op.drop_column("content_items", "topic_swap_history")
