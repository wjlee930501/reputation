"""Add stable hospital membership for monthly visibility measurement.

Revision ID: 0063_add_monthly_sov_cohort
Revises: 0062_allow_monthly_manifest_protocol_supersede
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063_add_monthly_sov_cohort"
down_revision: str | None = "0062_allow_monthly_manifest_protocol_supersede"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_LEGACY_COHORT_NAME_TOKENS = (
    "강심장내과",
    "행복드림",
    "장편한외과",
    "마포성모탑",
    "노원탑365",
    "서울W내과의원 위례점",
    "연세속시원",
)


def upgrade() -> None:
    op.add_column(
        "hospitals",
        sa.Column(
            "monthly_sov_cohort",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    bind = op.get_bind()
    for token in _LEGACY_COHORT_NAME_TOKENS:
        matches = list(
            bind.execute(
                sa.text(
                    "SELECT id, name FROM hospitals "
                    "WHERE status = 'ACTIVE' AND position(lower(:token) in lower(name)) > 0 "
                    "ORDER BY created_at, id"
                ),
                {"token": token},
            ).mappings()
        )
        if len(matches) != 1:
            logger.error(
                "monthly cohort token migration requires exactly one ACTIVE hospital: "
                "token=%r matches=%d",
                token,
                len(matches),
            )
            continue
        bind.execute(
            sa.text("UPDATE hospitals SET monthly_sov_cohort = true WHERE id = :id"),
            {"id": matches[0]["id"]},
        )


def downgrade() -> None:
    op.drop_column("hospitals", "monthly_sov_cohort")
