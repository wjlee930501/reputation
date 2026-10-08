"""Storage boundary for the additive immutable content-revision schema."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class RevisionReconciliation:
    created_count: int
    cleared_count: int
    unchanged_count: int


async def reconcile_content_revisions(
    db: AsyncSession,
    *,
    content_item_id: uuid.UUID | None = None,
) -> RevisionReconciliation:
    """Append or clear immutable editions from the locked legacy mirror.

    Passing no id is reserved for the post-drain release reconciliation.  A
    revision-aware writer can pass one stable item id inside its existing transaction,
    after writing the legacy mirror, to perform the atomic dual-write step.
    """

    row = (
        await db.execute(
            text("SELECT * FROM reconcile_content_revisions(:content_item_id)"),
            {"content_item_id": content_item_id},
        )
    ).one()
    return RevisionReconciliation(
        created_count=row.created_count,
        cleared_count=row.cleared_count,
        unchanged_count=row.unchanged_count,
    )
