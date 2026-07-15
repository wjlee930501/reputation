"""add hospital visual theme fields

Revision ID: 0031_add_hospital_visual_theme
Revises: 0030_unique_ai_query_target_hospital_name
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_add_hospital_visual_theme"
down_revision: Union[str, None] = "0030_unique_ai_query_target_hospital_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column("brand_primary_color", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "hospitals",
        sa.Column("brand_accent_color", sa.String(length=7), nullable=True),
    )
    op.add_column(
        "hospitals", sa.Column("logo_url", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "hospitals",
        sa.Column("hero_image_url", sa.String(length=500), nullable=True),
    )

    # 장편한외과 기존 도메인에는 선택한 네이비·골드 팔레트를 즉시 적용한다.
    op.execute(
        """
        UPDATE hospitals
        SET brand_primary_color = '#17365D',
            brand_accent_color = '#B79045'
        WHERE lower(aeo_domain) = 'jangclinic.kr'
        """
    )


def downgrade() -> None:
    op.drop_column("hospitals", "hero_image_url")
    op.drop_column("hospitals", "logo_url")
    op.drop_column("hospitals", "brand_accent_color")
    op.drop_column("hospitals", "brand_primary_color")
