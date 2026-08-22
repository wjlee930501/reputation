"""Add hospital-specific hero and editorial scope controls.

Revision ID: 0054_add_hospital_content_customization
Revises: 0053_add_content_image_policy_verification
"""

import json

import sqlalchemy as sa

from alembic import op

revision: str = "0054_add_hospital_content_customization"
down_revision: str | None = "0053_add_content_image_policy_verification"
branch_labels: str | None = None
depends_on: str | None = None

NOWON_HERO_SPECIALTIES = ["정형외과", "통증의학과", "외상치료"]
NOWON_CONTENT_FOCUS_TOPICS = ["정형외과", "신경외과", "통증의학과", "외상"]


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column(
            "hero_specialties",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "hospitals",
        sa.Column(
            "content_focus_topics",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "content_items",
        sa.Column("content_focus_topic", sa.String(length=40), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE hospitals SET hero_specialties = CAST(:hero_specialties AS json), "
            "content_focus_topics = CAST(:content_focus_topics AS json), "
            "hero_description = :hero_description WHERE slug = :slug"
        ).bindparams(
            hero_specialties=json.dumps(NOWON_HERO_SPECIALTIES, ensure_ascii=False),
            content_focus_topics=json.dumps(NOWON_CONTENT_FOCUS_TOPICS, ensure_ascii=False),
            hero_description="매일 365 야간진료",
            slug="noweontab365yiweon",
        )
    )


def downgrade() -> None:
    op.drop_column("content_items", "content_focus_topic")
    op.drop_column("hospitals", "content_focus_topics")
    op.drop_column("hospitals", "hero_specialties")
