"""add hospital entity identifiers + director credentials JSON

Revision ID: 0019_add_hospital_entity_identifiers
Revises: 0018_add_asset_storage_and_photo_types
Create Date: 2026-05-13

P2 GEO 후속:
- AI 답변의 가장 강력한 권위 신호는 백링크가 아닌 "엔티티 합의(entity consensus)" — 같은 병원이 Wikidata/GBP/Naver Place/Kakao Map/HIRA에서 동일하게 식별되는 것이 핵심.
- 본 마이그레이션은 외부 식별자 5종을 hospitals에 추가해 sameAs JSON-LD로 풀스택 출력 가능하게 한다.
- director_credentials JSON: Physician.hasCredential / knowsAbout / alumniOf 매핑용. 구조: {medical_school, board_certifications[], society_memberships[], license_number}.
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_add_hospital_entity_identifiers"
down_revision = "0018_add_asset_storage_and_photo_types"
branch_labels = None
depends_on = None


NEW_STRING_COLUMNS = (
    ("wikidata_qid", 50),
    ("gbp_place_id", 255),
    ("naver_place_id", 100),
    ("kakao_place_id", 100),
    ("hira_org_id", 50),
)


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("hospitals"):
        return

    for name, length in NEW_STRING_COLUMNS:
        if not _has_column("hospitals", name):
            op.add_column(
                "hospitals",
                sa.Column(name, sa.String(length=length), nullable=True),
            )

    if not _has_column("hospitals", "director_credentials"):
        op.add_column(
            "hospitals",
            sa.Column("director_credentials", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("hospitals"):
        return

    if _has_column("hospitals", "director_credentials"):
        op.drop_column("hospitals", "director_credentials")

    for name, _ in NEW_STRING_COLUMNS:
        if _has_column("hospitals", name):
            op.drop_column("hospitals", name)
