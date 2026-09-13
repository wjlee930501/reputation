"""Certify the hospital hero image as a cover-image fallback of last resort.

새 병원의 첫 글에는 빌려올 인증 이미지가 없다. 그 글은 종전에 `CONTENT_IMAGE_NOT_READY`로
막혔다. 병원의 히어로 이미지를 **실제 바이트로** 검수해 content-addressed 사본으로 저장하면
합성 인증값 없이 그 글을 내보낼 수 있다. 인증 결과는 병원 행에 캐시하고, `hero_image_url`이
바뀌면(=저장된 원본 URL과 달라지면) 다시 검수한다.

Additive only. 기존 행은 모두 NULL이며, 그 의미는 "히어로 대체 이미지 인증 없음"이다.
`content_items.image_fallback_source`는 이 판의 대표 이미지가 글 자신의 주제로 생성된 것도,
다른 글에서 빌려온 것도 아니라는 marker다. 교체 스윕이 주제 이미지를 붙이면 NULL로 돌아간다.
"""

import sqlalchemy as sa

from alembic import op

revision = "0075_add_hospital_fallback_image"
down_revision = "0074_add_image_reuse_marker"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hospitals", sa.Column("fallback_image_url", sa.String(500), nullable=True))
    op.add_column(
        "hospitals", sa.Column("fallback_image_source_url", sa.String(500), nullable=True)
    )
    op.add_column(
        "hospitals", sa.Column("fallback_image_content_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "hospitals", sa.Column("fallback_image_policy_version", sa.String(40), nullable=True)
    )
    op.add_column(
        "hospitals",
        sa.Column("fallback_image_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "content_items", sa.Column("image_fallback_source", sa.String(32), nullable=True)
    )


def downgrade():
    op.drop_column("content_items", "image_fallback_source")
    op.drop_column("hospitals", "fallback_image_verified_at")
    op.drop_column("hospitals", "fallback_image_policy_version")
    op.drop_column("hospitals", "fallback_image_content_hash")
    op.drop_column("hospitals", "fallback_image_source_url")
    op.drop_column("hospitals", "fallback_image_url")
