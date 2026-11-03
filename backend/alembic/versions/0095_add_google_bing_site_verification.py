"""Add per-hospital Google and Bing ownership verification tokens.

Gemini and AI Overviews read Google's index; ChatGPT search and Copilot draw on Bing among
other sources. A hospital served from its own domain cannot be registered in Google Search
Console or Bing Webmaster Tools with the single platform-wide Google token, so each hospital
row owns its own public proof values, the same way 0093 added Naver and Daum.

Both values are public HTML meta tokens (``google-site-verification`` and ``msvalidate.01``),
not secrets. The migration is additive and seeds nothing.

Revision ID: 0095_add_google_bing_site_verification
Revises: 0093_add_hospital_search_verification
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0095_add_google_bing_site_verification"
down_revision: str | None = "0093_add_hospital_search_verification"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(column: str) -> bool:
    return any(
        item["name"] == column
        for item in sa.inspect(op.get_bind()).get_columns("hospitals")
    )


def upgrade() -> None:
    if not _has_column("google_site_verification"):
        op.add_column(
            "hospitals",
            sa.Column("google_site_verification", sa.String(length=255), nullable=True),
        )
    if not _has_column("bing_site_verification"):
        op.add_column(
            "hospitals",
            sa.Column("bing_site_verification", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("hospitals", "bing_site_verification")
    op.drop_column("hospitals", "google_site_verification")
