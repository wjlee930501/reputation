"""Let the approved-revision function honour D23 optional references.

D23 (2026-10-13) made references optional for cost / provider-choice posts, decided in Python
by title (`reference_requirement.references_block_when_empty`). The 0086 function still required
`NOTICE OR references > 0`, so those posts never received an approved revision and the publish
transaction rolled back every tick. The publish path now stamps its verdict into
`essence_check_summary.reference_requirement` (bound to the title hash and content type) and the
function accepts that marker. Every other eligibility check is unchanged.

Downgrade reinstalls the 0086 function (the strict clause).

Revision ID: 0089_allow_optional_reference_revisions
Revises: 0088_add_outbox_open_pairing_index
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision: str = "0089_allow_optional_reference_revisions"
down_revision: str | None = "0088_add_outbox_open_pairing_index"
branch_labels: str | None = None
depends_on: str | None = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "revision_sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0089.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0086.sql").read_text(encoding="utf-8"))
