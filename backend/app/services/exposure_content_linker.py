"""Shared ownership for exposure-action/content-item links."""

import uuid
from typing import Any

from fastapi import HTTPException

from app.models.content import ContentItem, ContentStatus
from app.models.sov import ExposureAction

BRIEF_CAPABLE_ACTION_TYPES = {"CONTENT", "WEBBLOG_IA", "SOURCE"}


async def link_content_to_exposure_action(
    db: Any,
    *,
    action: ExposureAction,
    item: ContentItem,
    previous_action: ExposureAction | None = None,
) -> None:
    """Link one draft content item to one content-producing exposure action."""
    _ensure_brief_capable_action(action)
    if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=409,
            detail="Cannot link a published content item to an AI exposure work item",
        )

    action_id = _uuid_or_none(getattr(action, "id", None))
    item_action_id = _uuid_or_none(getattr(item, "exposure_action_id", None))
    previous_action_id = _uuid_or_none(getattr(previous_action, "id", None)) if previous_action else None
    if previous_action and previous_action_id == item_action_id and previous_action_id != action_id:
        await unlink_content_from_exposure_action(db, previous_action, content_id=item.id)
        item_action_id = None

    if item_action_id not in {None, action_id}:
        raise HTTPException(
            status_code=409,
            detail="Content item is already linked to another AI exposure work item",
        )

    if previous_action and previous_action_id != action_id:
        await unlink_content_from_exposure_action(db, previous_action, content_id=item.id)

    await unlink_content_from_exposure_action(db, action, replacement_content_id=item.id)
    item.exposure_action_id = action.id
    if getattr(action, "query_target_id", None):
        item.query_target_id = action.query_target_id
    action.linked_content_id = item.id
    action.linked_content = item


async def unlink_content_from_exposure_action(
    db: Any,
    action: ExposureAction,
    *,
    content_id: uuid.UUID | None = None,
    replacement_content_id: uuid.UUID | None = None,
) -> None:
    """Clear an action/content link from both sides when it still points at this action."""
    linked_content_id = content_id or getattr(action, "linked_content_id", None)
    if not linked_content_id or linked_content_id == replacement_content_id:
        return

    previous = await db.get(ContentItem, linked_content_id) if hasattr(db, "get") else None
    if previous and _uuid_or_none(getattr(previous, "exposure_action_id", None)) == _uuid_or_none(action.id):
        previous.exposure_action_id = None

    if getattr(action, "linked_content_id", None) == linked_content_id:
        action.linked_content_id = None
        action.linked_content = None


def ensure_brief_capable_action(action: ExposureAction) -> None:
    _ensure_brief_capable_action(action)


def _ensure_brief_capable_action(action: ExposureAction) -> None:
    action_type = str(_enum_value(action.action_type)).upper()
    if action_type not in BRIEF_CAPABLE_ACTION_TYPES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Content guide links are only available for content-producing AI exposure "
                "work items (CONTENT, WEBBLOG_IA, SOURCE). Measurement work should be "
                "handled by running the first AI mention-rate measurement."
            ),
        )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
