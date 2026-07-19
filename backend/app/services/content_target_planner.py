"""Connect measured patient questions to the next ungenerated content slot."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

import arrow
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentType
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction
from app.services.content_brief import BRIEF_STATUS_APPROVED, build_content_brief
from app.services.exposure_content_linker import BRIEF_CAPABLE_ACTION_TYPES

ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS"}
PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}


def prepare_automatic_content_brief_sync(
    db: Any,
    *,
    item: ContentItem,
    hospital: Hospital,
    philosophy: HospitalContentPhilosophy,
) -> dict:
    """Create and approve a deterministic brief from the latest exposure state.

    The approved clinic philosophy remains the safety gate. The system only chooses
    which measured patient question the already-scheduled slot should answer; it does
    not invent new hospital facts or bypass publication screening.
    """
    if item.brief_status == BRIEF_STATUS_APPROVED and isinstance(item.content_brief, dict):
        return item.content_brief

    # Lightweight stubs and imported legacy rows may not expose the linkage columns.
    # They still receive a philosophy-backed generic brief without attempting DB planning.
    if not hasattr(item, "query_target_id"):
        brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            philosophy=philosophy,
        )
        item.content_brief = brief
        item.brief_status = BRIEF_STATUS_APPROVED
        return brief

    target = _load_target(db, getattr(item, "query_target_id", None), hospital.id)
    if target is None:
        target = _choose_target(db, item=item, hospital_id=hospital.id)
        if target is not None:
            item.query_target_id = target.id

    action = _load_or_choose_action(db, item=item, target=target, hospital_id=hospital.id)
    if action is not None:
        item.exposure_action_id = action.id
        action.linked_content_id = item.id

    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=target,
        exposure_action=action,
        philosophy=philosophy,
    )
    brief["source"] = {
        **(brief.get("source") or {}),
        "mode": "automatic_exposure_plan",
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }
    item.content_brief = brief
    item.brief_status = BRIEF_STATUS_APPROVED
    item.brief_approved_at = datetime.now(timezone.utc)
    item.brief_approved_by = "SYSTEM_EXPOSURE_PLANNER"
    return brief


def _load_target(db: Any, target_id: Any, hospital_id: Any) -> AIQueryTarget | None:
    if not target_id:
        return None
    return db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.id == target_id,
            AIQueryTarget.hospital_id == hospital_id,
            AIQueryTarget.status == "ACTIVE",
        )
    ).scalar_one_or_none()


def _choose_target(db: Any, *, item: ContentItem, hospital_id: Any) -> AIQueryTarget | None:
    targets = list(
        db.execute(
            select(AIQueryTarget)
            .options(selectinload(AIQueryTarget.variants))
            .where(
                AIQueryTarget.hospital_id == hospital_id,
                AIQueryTarget.status == "ACTIVE",
            )
        )
        .scalars()
        .all()
    )
    if not targets:
        return None

    slot_date = item.scheduled_date or date.today()
    month_start = slot_date.replace(day=1)
    month_end = arrow.get(month_start).ceil("month").date()
    linked_ids = list(
        db.execute(
            select(ContentItem.query_target_id).where(
                ContentItem.hospital_id == hospital_id,
                ContentItem.id != item.id,
                ContentItem.scheduled_date >= month_start,
                ContentItem.scheduled_date <= month_end,
                ContentItem.query_target_id.is_not(None),
            )
        ).scalars()
    )
    usage = Counter(str(value) for value in linked_ids if value)

    action_target_ids = set(
        str(value)
        for value in db.execute(
            select(ExposureAction.query_target_id).where(
                ExposureAction.hospital_id == hospital_id,
                ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
                ExposureAction.action_type.in_(BRIEF_CAPABLE_ACTION_TYPES),
                ExposureAction.linked_content_id.is_(None),
                ExposureAction.query_target_id.is_not(None),
            )
        ).scalars()
        if value
    )
    slot_month = slot_date.strftime("%Y-%m")
    return min(
        targets,
        key=lambda target: (
            0 if str(target.id) in action_target_ids else 1,
            PRIORITY_RANK.get(str(target.priority or "NORMAL").upper(), 9),
            usage[str(target.id)],
            _content_type_affinity(target, item.content_type),
            0 if target.target_month == slot_month else 1,
            str(target.name or ""),
            str(target.id),
        ),
    )


def _load_or_choose_action(
    db: Any,
    *,
    item: ContentItem,
    target: AIQueryTarget | None,
    hospital_id: Any,
) -> ExposureAction | None:
    exposure_action_id = getattr(item, "exposure_action_id", None)
    if exposure_action_id:
        existing = db.get(ExposureAction, exposure_action_id)
        if existing is not None:
            return existing
    if target is None:
        return None
    actions = list(
        db.execute(
            select(ExposureAction).where(
                ExposureAction.hospital_id == hospital_id,
                ExposureAction.query_target_id == target.id,
                ExposureAction.status.in_(ACTIVE_ACTION_STATUSES),
                ExposureAction.action_type.in_(BRIEF_CAPABLE_ACTION_TYPES),
                ExposureAction.linked_content_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    if not actions:
        return None
    return sorted(
        actions,
        key=lambda action: (
            str(action.due_month or "9999-99"),
            str(action.created_at or ""),
            str(action.id),
        ),
    )[0]


def _content_type_affinity(target: AIQueryTarget, content_type: ContentType | str) -> int:
    value = content_type.value if hasattr(content_type, "value") else str(content_type)
    if value == ContentType.TREATMENT.value:
        return 0 if target.treatment else 2
    if value == ContentType.DISEASE.value:
        return 0 if target.condition_or_symptom else 2
    if value == ContentType.LOCAL.value:
        return 0 if target.region_terms else 2
    if value == ContentType.FAQ.value:
        return 0
    if value == ContentType.HEALTH.value:
        return 0 if target.condition_or_symptom else 1
    if value == ContentType.COLUMN.value:
        return 1
    return 3
