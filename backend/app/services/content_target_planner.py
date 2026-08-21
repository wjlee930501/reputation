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
from app.services.content_focus import (
    matching_content_focus_topic,
    normalize_content_focus_topics,
)
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
    scheduled_date = getattr(item, "scheduled_date", None)
    planned_publish_date = scheduled_date.isoformat() if scheduled_date else None
    allowed_topics = normalize_content_focus_topics(
        getattr(hospital, "content_focus_topics", [])
    )
    if item.brief_status == BRIEF_STATUS_APPROVED and isinstance(item.content_brief, dict):
        approved_focus = _brief_focus_topic(item.content_brief, allowed_topics)
        if not allowed_topics or approved_focus is not None:
            return {
                **item.content_brief,
                "planned_publish_date": planned_publish_date,
            }

    # Lightweight stubs and imported legacy rows may not expose the linkage columns.
    # They still receive a philosophy-backed generic brief without attempting DB planning.
    if not hasattr(item, "query_target_id"):
        brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            philosophy=philosophy,
        )
        _apply_focus_to_brief(brief, item=item, allowed_topics=allowed_topics)
        item.content_brief = brief
        item.brief_status = BRIEF_STATUS_APPROVED
        return brief

    target = _load_target(db, getattr(item, "query_target_id", None), hospital.id)
    if target is not None and allowed_topics and not _target_matches_focus(target, allowed_topics):
        target = None
        item.query_target_id = None
        item.exposure_action_id = None
    if target is None:
        target = _choose_target(
            db,
            item=item,
            hospital_id=hospital.id,
            allowed_topics=allowed_topics,
        )
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
    _apply_focus_to_brief(brief, item=item, allowed_topics=allowed_topics)
    brief["source"] = {
        **(brief.get("source") or {}),
        "mode": "automatic_exposure_plan",
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }
    brief["planned_publish_date"] = planned_publish_date
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


def _choose_target(
    db: Any,
    *,
    item: ContentItem,
    hospital_id: Any,
    allowed_topics: tuple[str, ...],
) -> AIQueryTarget | None:
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

    if allowed_topics:
        targets = [target for target in targets if _target_matches_focus(target, allowed_topics)]
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


def _target_matches_focus(target: AIQueryTarget, allowed_topics: tuple[str, ...]) -> bool:
    searchable = " ".join(
        str(value or "")
        for value in (
            target.name,
            target.treatment,
            target.condition_or_symptom,
            target.target_intent,
        )
    )
    return any(topic in searchable for topic in allowed_topics)


def _brief_focus_topic(
    brief: dict[str, Any],
    allowed_topics: tuple[str, ...],
) -> str | None:
    query_target = brief.get("query_target")
    exposure_action = brief.get("exposure_action")
    treatment_narrative = brief.get("treatment_narrative")
    values = [
        brief.get("target_query"),
        brief.get("patient_intent"),
    ]
    for nested in (query_target, exposure_action, treatment_narrative):
        if not isinstance(nested, dict):
            continue
        values.extend(
            nested.get(key)
            for key in (
                "name",
                "target_intent",
                "treatment",
                "condition_or_symptom",
                "title",
                "description",
                "angle",
            )
        )
    return matching_content_focus_topic(
        tuple(value if isinstance(value, str) else None for value in values),
        allowed_topics,
    )


def _apply_focus_to_brief(
    brief: dict[str, Any],
    *,
    item: ContentItem,
    allowed_topics: tuple[str, ...],
) -> None:
    if not allowed_topics:
        return
    selected = _brief_focus_topic(brief, allowed_topics)
    if selected is None:
        sequence_no = max(int(getattr(item, "sequence_no", 1) or 1), 1)
        selected = allowed_topics[(sequence_no - 1) % len(allowed_topics)]
        brief["target_query"] = f"{selected} 진료 전 확인할 점"
    brief["content_focus_topic"] = selected


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
