"""Add reusable per-hospital search-engine ownership verification.

Naver verifies ownership with a per-hospital HTML meta token. Daum verifies it with a
``#DaumWebMasterTool:...`` line at the bottom of that hospital's robots.txt. Both are public
proof values, not secrets, and belong to the hospital row rather than a Site image or environment
variable.

The data update restores the Naver token already observed on daonneuro.co.kr and seeds the Daum
phrase requested for that same hospital. Future hospitals use the Admin profile path.

Revision ID: 0093_add_hospital_search_verification
Revises: 0094_add_hospital_operating_changes
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0093_add_hospital_search_verification"
down_revision: str | None = "0094_add_hospital_operating_changes"
branch_labels: str | None = None
depends_on: str | None = None

_SLUG = "sejongdaonsingyeonggwayiweon"
_NAVER_TOKEN = "bb6f7e8f49eb178c06769cb81c16174c8a07a69a"
_DAUM_PHRASE = (
    "#DaumWebMasterTool:"
    "ec0bfd6a69d68c57e1f24bfe8b935880a1f5d4d1cecb8c30e23ffab73ddb9c47:"
    "WoYFtMaR84eZ7MaX1Xjw9A=="
)


def _has_column(column: str) -> bool:
    return any(
        item["name"] == column
        for item in sa.inspect(op.get_bind()).get_columns("hospitals")
    )


def upgrade() -> None:
    if not _has_column("naver_site_verification"):
        op.add_column(
            "hospitals",
            sa.Column("naver_site_verification", sa.String(length=255), nullable=True),
        )
    if not _has_column("daum_webmaster_verification"):
        op.add_column(
            "hospitals",
            sa.Column("daum_webmaster_verification", sa.String(length=500), nullable=True),
        )
    op.get_bind().execute(
        sa.text(
            "UPDATE hospitals "
            "SET naver_site_verification = :naver, daum_webmaster_verification = :daum "
            "WHERE slug = :slug"
        ),
        {"slug": _SLUG, "naver": _NAVER_TOKEN, "daum": _DAUM_PHRASE},
    )


def downgrade() -> None:
    op.drop_column("hospitals", "daum_webmaster_verification")
    op.drop_column("hospitals", "naver_site_verification")
