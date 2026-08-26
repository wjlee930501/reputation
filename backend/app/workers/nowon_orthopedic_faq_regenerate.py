"""Dispatch the locked Nowon orthopedic FAQ item for regeneration."""

import logging
import uuid

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem
from app.workers.dispatch_auth import build_dispatch_headers
from app.workers.nowon_august_backfill import NOWON_HOSPITAL_ID

logger = logging.getLogger(__name__)

ORTHOPEDIC_FAQ_ITEM_ID = uuid.UUID("64882bde-925d-46f9-8c40-7e396c92d9b1")


def regenerate_nowon_orthopedic_faq() -> int:
    """Enqueue only the locked FAQ item when it belongs to the locked hospital."""
    with SyncSessionLocal() as db:
        item = db.execute(
            select(ContentItem).where(ContentItem.id == ORTHOPEDIC_FAQ_ITEM_ID).limit(1)
        ).scalars().first()
        if item is None or item.hospital_id != NOWON_HOSPITAL_ID:
            return 0

        item_id = str(item.id)

    from app.workers.tasks import regenerate_content_item

    try:
        regenerate_content_item.apply_async(
            args=[item_id],
            queue="content",
            headers=build_dispatch_headers("regenerate-content", item_id),
        )
    except Exception:
        logger.exception("Nowon orthopedic FAQ regeneration enqueue failed: item=%s", item_id)
    return 1
