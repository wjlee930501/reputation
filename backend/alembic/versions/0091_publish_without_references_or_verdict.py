"""Let the approved-revision function honour D36 (no references) and D37 (review deferred).

D36 (2026-10-14) lets every post publish without references when none is verified; references
that are present still need a passing check (the structural clause is unchanged). The 0089/0090
reference-count clause (`NOTICE OR references > 0 OR optional-reference marker`) is removed.

D37 (2026-10-14) lets a post whose independent review produced no verdict (UNAVAILABLE —
provider error/timeout) publish on schedule and be reviewed after publication. The function
accepts UNAVAILABLE only when the publish path stamped `essence_check_summary.ai_review_deferred`
for the same candidate hash as the stored review and for the current public text hash (recomputed
here), and the body carries no automatic-correction record. Every other eligibility check is
unchanged.

Downgrade reinstalls the 0090 function. The publish path keeps stamping the 0089 reference marker
(`block_when_empty=false`), so D36 posts still receive revisions under the 0090 function.

Revision ID: 0091_publish_without_references_or_verdict
Revises: 0090_allow_fallback_treatment_narrative
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision: str = "0091_publish_without_references_or_verdict"
down_revision: str | None = "0090_allow_fallback_treatment_narrative"
branch_labels: str | None = None
depends_on: str | None = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "revision_sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0091.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0090.sql").read_text(encoding="utf-8"))
