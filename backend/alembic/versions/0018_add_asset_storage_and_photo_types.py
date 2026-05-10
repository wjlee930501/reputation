"""add file storage columns + photo SourceType enum values + is_public

Revision ID: 0018_add_asset_storage_and_photo_types
Revises: 0017_add_faq_split_fields
Create Date: 2026-05-09

AE 온보딩 자산 인입 인프라:
- file_url: GCS 또는 로컬 업로드된 파일의 URL (이미지/PDF/DOCX)
- mime_type: 처리 분기 + UI 미리보기용
- file_size_bytes: 운영 모니터링·비용 추적용
- is_public: AE가 검수한 사진을 /site 공개 표면에 노출할지 결정 (default false)
- SourceType 사진 카테고리 4종 추가 — DoctorIntro/ContactCard 자동 매핑용
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_add_asset_storage_and_photo_types"
down_revision = "0017_add_faq_split_fields"
branch_labels = None
depends_on = None


NEW_PHOTO_VALUES = (
    "PHOTO_DOCTOR",
    "PHOTO_CLINIC_EXTERIOR",
    "PHOTO_CLINIC_INTERIOR",
    "PHOTO_TREATMENT_ROOM",
)
ORIGINAL_SOURCE_VALUES = (
    "NAVER_BLOG",
    "YOUTUBE",
    "HOMEPAGE",
    "INTERVIEW",
    "LANDING_PAGE",
    "BROCHURE",
    "INTERNAL_NOTE",
    "OTHER",
)


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("hospital_source_assets"):
        return

    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 새 enum 값 추가 (Postgres만 — SQLite 테스트는 Enum이 String으로 컴파일되어 자동 호환)
    if is_postgres:
        for value in NEW_PHOTO_VALUES:
            bind.execute(
                sa.text(
                    f"ALTER TYPE hospital_source_type ADD VALUE IF NOT EXISTS '{value}'"
                )
            )

    if not _has_column("hospital_source_assets", "file_url"):
        op.add_column(
            "hospital_source_assets",
            sa.Column("file_url", sa.String(length=500), nullable=True),
        )
    if not _has_column("hospital_source_assets", "mime_type"):
        op.add_column(
            "hospital_source_assets",
            sa.Column("mime_type", sa.String(length=100), nullable=True),
        )
    if not _has_column("hospital_source_assets", "file_size_bytes"):
        op.add_column(
            "hospital_source_assets",
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        )
    if not _has_column("hospital_source_assets", "is_public"):
        op.add_column(
            "hospital_source_assets",
            sa.Column(
                "is_public",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if not _has_table("hospital_source_assets"):
        return
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        photo_values_sql = ", ".join(f"'{value}'" for value in NEW_PHOTO_VALUES)
        bind.execute(
            sa.text(
                "UPDATE hospital_source_assets SET source_type = 'OTHER' "
                f"WHERE source_type::text IN ({photo_values_sql})"
            )
        )
    for column in ("is_public", "file_size_bytes", "mime_type", "file_url"):
        if _has_column("hospital_source_assets", column):
            op.drop_column("hospital_source_assets", column)
    if is_postgres:
        original_values_sql = ", ".join(f"'{value}'" for value in ORIGINAL_SOURCE_VALUES)
        bind.execute(sa.text("ALTER TYPE hospital_source_type RENAME TO hospital_source_type_with_photos"))
        bind.execute(sa.text(f"CREATE TYPE hospital_source_type AS ENUM ({original_values_sql})"))
        bind.execute(
            sa.text(
                "ALTER TABLE hospital_source_assets "
                "ALTER COLUMN source_type TYPE hospital_source_type "
                "USING source_type::text::hospital_source_type"
            )
        )
        bind.execute(sa.text("DROP TYPE hospital_source_type_with_photos"))
