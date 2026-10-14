"""Add hospitals.profile_field_provenance and hospital_content_philosophies.synthesis_metadata.

Revision ID: 0091_add_profile_provenance_and_essence_metadata
Revises: 0090_allow_fallback_treatment_narrative

Phase 4(D30·D33) 준비 컬럼 두 개다.

- ``hospitals.profile_field_provenance`` — 프로필 필드마다 누가 값을 넣었는지(마케터 입력 OPERATOR,
  자동 입력 AUTOFILL, 출처 모름 LEGACY)와 자동 수집 값과의 충돌을 남기는 원장. 자동 적용이 마케터가
  입력·비운 칸을 덮지 않게 하는 판정 근거다.
- ``hospital_content_philosophies.synthesis_metadata`` — 자동 요약 판이 무엇을 입력으로 썼고 무엇을
  버렸는지(입력 hash·제외 입력·버린 주장·충돌) 남기는 메타데이터.

Additive only. 둘 다 ``NOT NULL DEFAULT '{}'::jsonb``라 기존 행은 빈 객체이고 옛 코드는 컬럼을 모른 채
그대로 동작한다(롤백 안전). enum 추가는 없다. 데이터 채우기는 마이그레이션이 아니라 백필 CLI가 한다.
재실행해도 같은 결과가 되도록 컬럼 존재를 먼저 확인한다.

배포 순서: **이 마이그레이션을 먼저 적용하고, 코드는 그 다음 배포한다.** 새 ORM은 병원·판 행을 읽을
때마다 이 컬럼을 SELECT한다.

Downgrade는 두 컬럼만 지운다(원장·메타데이터는 사라지지만 프로필 값과 판은 그대로다).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0091_add_profile_provenance_and_essence_metadata"
down_revision: str | None = "0090_allow_fallback_treatment_narrative"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("hospitals", "profile_field_provenance"),
    ("hospital_content_philosophies", "synthesis_metadata"),
)


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    for table, column in _COLUMNS:
        if _has_column(table, column):
            continue
        op.add_column(
            table,
            sa.Column(
                column,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        if _has_column(table, column):
            op.drop_column(table, column)
