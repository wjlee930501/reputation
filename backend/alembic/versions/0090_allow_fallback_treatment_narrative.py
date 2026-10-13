"""Let the approved-revision function accept the generic (fallback) treatment narrative.

`content_brief._treatment_narrative` builds `source='fallback'` when the topic's treatment matches
neither an approved narrative nor a hospital treatment — a generic writing angle. The narrative
source is a writing hint, not approval evidence; the Python publish gate accepts it, but the 0089
function only accepted `approved_philosophy`/`hospital_profile`, so those drafts never received an
approved revision and every publish tick rolled back with `approved_revision_not_created`.

Approval evidence stays where it was: the approved BaseEssence join and ALIGNED status here, and
in Python the publish gate now also requires an independent review bound to the current candidate
hash for unpublished fallback drafts (`content_publication.fallback_narrative_requires_bound_review`).
The non-empty `angle` and object-type checks are unchanged.

Downgrade reinstalls the 0089 function.

Revision ID: 0090_allow_fallback_treatment_narrative
Revises: 0089_allow_optional_reference_revisions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision: str = "0090_allow_fallback_treatment_narrative"
down_revision: str | None = "0089_allow_optional_reference_revisions"
branch_labels: str | None = None
depends_on: str | None = None

_SQL_DIR = Path(__file__).resolve().parents[1] / "revision_sql"


def upgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0090.sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute((_SQL_DIR / "reconcile_content_revisions_0089.sql").read_text(encoding="utf-8"))
