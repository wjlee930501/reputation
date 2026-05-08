"""
Admin API — Exposure actions
GET   /admin/hospitals/{id}/exposure-actions — deterministic TOP actions
GET   /admin/hospitals/{id}/exposure-actions/{action_id}
PATCH /admin/hospitals/{id}/exposure-actions/{action_id}
POST  /admin/hospitals/{id}/exposure-actions/{action_id}/create-brief
"""
import uuid
from datetime import date, datetime, timezone
from typing import Any

import arrow
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, ExposureAction
from app.services.audit_log import default_actor, write_audit_log
from app.services.content_brief import BRIEF_STATUS_DRAFT, build_content_brief
from app.services.exposure_content_linker import (
    ensure_brief_capable_action,
    link_content_to_exposure_action,
    unlink_content_from_exposure_action,
)
from app.services.exposure_action_engine import (
    ensure_hospital_exposure_actions,
    list_top_exposure_actions,
)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — AI Exposure Work Queue"])

ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED", "ARCHIVED"}


class ExposureActionPatch(BaseModel):
    status: str | None = None
    owner: str | None = Field(default=None, max_length=100)
    due_month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    linked_content_id: uuid.UUID | None = None


class CreateBriefBody(BaseModel):
    content_id: uuid.UUID | None = None
    content_type: ContentType = ContentType.FAQ
    scheduled_date: date | None = None
    sequence_no: int | None = Field(default=None, ge=1)
    total_count: int | None = Field(default=None, ge=1)
    brief_approved_by: str | None = Field(default=None, max_length=100)


@router.get("/{hospital_id}/exposure-actions")
async def get_exposure_actions(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=3, ge=1, le=20),
):
    """Return deterministic top exposure actions for a hospital."""
    await _get_hospital_or_404(db, hospital_id)

    await ensure_hospital_exposure_actions(db, hospital_id)
    actions = await list_top_exposure_actions(db, hospital_id, limit=limit)
    return [_serialize_action(action) for action in actions]


@router.get("/{hospital_id}/exposure-actions/{action_id}")
async def get_exposure_action(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return one exposure action in the work queue."""
    await _get_hospital_or_404(db, hospital_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    return _serialize_action(action)


@router.patch("/{hospital_id}/exposure-actions/{action_id}")
async def update_exposure_action(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ExposureActionPatch,
    db: AsyncSession = Depends(get_db),
):
    """Update operator-managed work queue fields for an exposure action."""
    await _get_hospital_or_404(db, hospital_id)
    await _lock_action_for_update(db, hospital_id, action_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    fields = body.model_fields_set

    before = {
        "status": action.status,
        "owner": action.owner,
        "due_month": action.due_month,
        "linked_content_id": str(action.linked_content_id) if action.linked_content_id else None,
    }
    changes: dict[str, dict[str, Any]] = {}

    if "status" in fields:
        if body.status is None or body.status not in ACTION_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid AI exposure work status")
        if action.status != body.status:
            changes["status"] = {"from": before["status"], "to": body.status}
        action.status = body.status
        if body.status == "COMPLETED":
            if action.completed_at is None:
                action.completed_at = _utcnow()
        else:
            action.completed_at = None

    if "owner" in fields and action.owner != body.owner:
        changes["owner"] = {"from": before["owner"], "to": body.owner}
        action.owner = body.owner

    if "due_month" in fields:
        if body.due_month is not None:
            _validate_due_month(body.due_month)
        if action.due_month != body.due_month:
            changes["due_month"] = {"from": before["due_month"], "to": body.due_month}
        action.due_month = body.due_month

    if "linked_content_id" in fields:
        await _apply_linked_content_update(db, hospital_id, action, body.linked_content_id)
        new_linked = str(action.linked_content_id) if action.linked_content_id else None
        if before["linked_content_id"] != new_linked:
            changes["linked_content_id"] = {"from": before["linked_content_id"], "to": new_linked}

    if changes:
        await write_audit_log(
            db,
            action="update_exposure_action",
            hospital_id=hospital_id,
            actor=default_actor(),
            target_type="exposure_action",
            target_id=action_id,
            detail={"changes": changes},
        )

    await db.commit()
    action = await _get_action_or_404(db, hospital_id, action_id)
    return _serialize_action(action)


@router.post("/{hospital_id}/exposure-actions/{action_id}/create-brief")
async def create_exposure_action_brief(
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
    body: CreateBriefBody | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Create or attach a query-linked content brief for an exposure action."""
    body = body or CreateBriefBody()
    hospital = await _get_hospital_or_404(db, hospital_id)
    await _lock_action_for_update(db, hospital_id, action_id)
    action = await _get_action_or_404(db, hospital_id, action_id)
    ensure_brief_capable_action(action)

    item = await _resolve_content_slot_for_brief(db, hospital_id, action, body)
    if item is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No available non-published content slot was found for the work month, "
                "and no active content schedule exists to create one."
            ),
        )

    if _uuid_or_none(getattr(item, "exposure_action_id", None)) not in {None, action.id}:
        raise HTTPException(
            status_code=409,
            detail="Content item is already linked to another AI exposure work item",
        )

    philosophy = await _get_approved_philosophy(db, hospital_id)
    query_target = getattr(action, "query_target", None)

    has_existing_linked_brief = _has_existing_linked_brief(action, item)

    await link_content_to_exposure_action(db, action=action, item=item)
    if has_existing_linked_brief:
        if item.brief_status is None:
            item.brief_status = BRIEF_STATUS_DRAFT
            item.brief_approved_at = None
            item.brief_approved_by = None
    else:
        item.content_brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            query_target=query_target,
            exposure_action=action,
            philosophy=philosophy,
        )
        item.brief_status = BRIEF_STATUS_DRAFT
        item.brief_approved_at = None
        item.brief_approved_by = None
    await db.commit()
    await db.refresh(item)
    action = await _get_action_or_404(db, hospital_id, action_id)

    return {
        "action": _serialize_action(action),
        "content_item": _serialize_content_summary(item),
        "philosophy_gate": _serialize_philosophy_gate(philosophy),
    }


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _get_action_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
) -> ExposureAction:
    result = await db.execute(
        select(ExposureAction)
        .options(
            selectinload(ExposureAction.query_target).selectinload(AIQueryTarget.variants),
            selectinload(ExposureAction.gap),
            selectinload(ExposureAction.linked_content),
        )
        .where(
            ExposureAction.id == action_id,
            ExposureAction.hospital_id == hospital_id,
        )
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="AI exposure work item not found")
    return action


async def _lock_action_for_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action_id: uuid.UUID,
) -> None:
    """Serialize mutating operations that relink one exposure action to content."""
    if not hasattr(db, "execute"):
        return
    result = await db.execute(
        select(ExposureAction.id)
        .where(
            ExposureAction.id == action_id,
            ExposureAction.hospital_id == hospital_id,
        )
        .with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="AI exposure work item not found")


async def _get_content_item_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
) -> ContentItem:
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Content item not found")
    return item


async def _lock_content_item_for_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
) -> None:
    """Serialize explicit/existing content slot claims across exposure actions."""
    if not hasattr(db, "execute"):
        return
    result = await db.execute(
        select(ContentItem.id)
        .where(
            ContentItem.id == content_id,
            ContentItem.hospital_id == hospital_id,
        )
        .with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Content item not found")


async def _apply_linked_content_update(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action: ExposureAction,
    linked_content_id: uuid.UUID | None,
) -> None:
    if linked_content_id is None:
        await unlink_content_from_exposure_action(db, action)
        return

    ensure_brief_capable_action(action)
    await _lock_content_item_for_update(db, hospital_id, linked_content_id)
    item = await _get_content_item_or_404(db, hospital_id, linked_content_id)
    await link_content_to_exposure_action(db, action=action, item=item)


def _has_existing_linked_brief(action: ExposureAction, item: ContentItem) -> bool:
    if not getattr(item, "content_brief", None):
        return False
    action_id = _uuid_or_none(getattr(action, "id", None))
    item_id = _uuid_or_none(getattr(item, "id", None))
    action_content_id = _uuid_or_none(getattr(action, "linked_content_id", None))
    item_action_id = _uuid_or_none(getattr(item, "exposure_action_id", None))
    return item_action_id == action_id or (item_id is not None and action_content_id == item_id)


async def _resolve_content_slot_for_brief(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    action: ExposureAction,
    body: CreateBriefBody,
) -> ContentItem | None:
    if body.content_id:
        await _lock_content_item_for_update(db, hospital_id, body.content_id)
        item = await _get_content_item_or_404(db, hospital_id, body.content_id)
        if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=409,
                detail="Cannot create a draft content guide on published content",
            )
        if _enum_value(item.content_type) != body.content_type.value:
            raise HTTPException(
                status_code=409,
                detail="content_type must match the selected content item",
            )
        return item

    if action.linked_content_id:
        await _lock_content_item_for_update(db, hospital_id, action.linked_content_id)
        item = await _get_content_item_or_404(db, hospital_id, action.linked_content_id)
        if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=409,
                detail="Cannot regenerate a draft content guide on published linked content",
            )
        return item

    period_start, period_end = _action_month_bounds(action.due_month)
    item = await _find_available_content_slot(
        db,
        hospital_id,
        period_start,
        period_end,
        body.content_type,
    )
    if item:
        return item

    schedule = await _get_active_content_schedule(db, hospital_id)
    if schedule is None:
        return None

    return await _create_content_slot(db, hospital_id, schedule, period_start, period_end, body)


async def _find_available_content_slot(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    period_start: date,
    period_end: date,
    content_type: ContentType,
) -> ContentItem | None:
    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= period_start,
            ContentItem.scheduled_date <= period_end,
            ContentItem.content_type == content_type,
            ContentItem.status != ContentStatus.PUBLISHED,
            ContentItem.exposure_action_id.is_(None),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return result.scalars().first()


async def _get_active_content_schedule(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> ContentSchedule | None:
    result = await db.execute(
        select(ContentSchedule)
        .where(
            ContentSchedule.hospital_id == hospital_id,
            ContentSchedule.is_active,
        )
        .order_by(ContentSchedule.active_from.desc())
        .with_for_update()
        .limit(1)
    )
    return result.scalars().first()


async def _create_content_slot(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    schedule: ContentSchedule,
    period_start: date,
    period_end: date,
    body: CreateBriefBody,
) -> ContentItem:
    scheduled_date = body.scheduled_date or period_start
    if scheduled_date < period_start or scheduled_date > period_end:
        raise HTTPException(
            status_code=400,
            detail="scheduled_date must be within the AI exposure work month",
        )

    sequence_no = body.sequence_no or await _next_sequence_no(
        db,
        hospital_id,
        period_start,
        period_end,
    )
    total_count = body.total_count or max(sequence_no, _plan_total(schedule.plan))
    item = ContentItem(
        hospital_id=hospital_id,
        schedule_id=schedule.id,
        content_type=body.content_type,
        sequence_no=sequence_no,
        total_count=total_count,
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
    )
    db.add(item)
    await db.flush()
    return item


async def _next_sequence_no(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> int:
    result = await db.execute(
        select(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= period_start,
            ContentItem.scheduled_date <= period_end,
        )
        .order_by(ContentItem.sequence_no.desc())
        .limit(1)
    )
    last = result.scalars().first()
    return (last.sequence_no + 1) if last else 1


async def _get_approved_philosophy(
    db: AsyncSession,
    hospital_id: uuid.UUID,
) -> HospitalContentPhilosophy | None:
    result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    return result.scalar_one_or_none()


def _action_month_bounds(due_month: str | None) -> tuple[date, date]:
    if due_month:
        start = _validate_due_month(due_month)
    else:
        start = arrow.now("Asia/Seoul").floor("month").date()
    end = arrow.get(start).ceil("month").date()
    return start, end


def _validate_due_month(due_month: str) -> date:
    try:
        year, month = (int(part) for part in due_month.split("-", 1))
        return arrow.Arrow(year, month, 1).date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid AI exposure work due_month")


def _plan_total(plan: str | None) -> int:
    if not plan:
        return 1
    try:
        return int(str(plan).split("_", 1)[1])
    except (IndexError, ValueError):
        return 1


STALE_OPERATOR_COPY_REPLACEMENTS = {
    f"타{'깃'} 질의": "환자 질문",
    f"타{'겟'} 질의": "환자 질문",
    f"타{'깃'} 질문": "환자 질문",
    f"타{'겟'} 질문": "환자 질문",
    f"웹{'블로그'}": "병원 정보·콘텐츠 허브",
    "Webblog": "병원 정보·콘텐츠 허브",
    "IA": "정보 구조",
    "FAQ/질환/치료 콘텐츠": "자주 묻는 질문/질환/치료 안내 콘텐츠",
}

ACTION_TYPE_DISPLAY_LABELS = {
    "MEASUREMENT": "측정",
    "CONTENT": "콘텐츠",
    "SOURCE": "근거 자료",
    "WEBBLOG_IA": "정보 구조",
}

ACTION_STATUS_DISPLAY_LABELS = {
    "OPEN": "대기",
    "IN_PROGRESS": "진행중",
    "BLOCKED": "확인필요",
    "COMPLETED": "완료",
    "CANCELLED": "취소",
    "ARCHIVED": "보관",
}

GAP_TYPE_DISPLAY_LABELS = {
    "NO_SUCCESSFUL_MEASUREMENT": "측정값 없음",
    "MISSING_MENTION": "병원 미언급",
    "LOW_MENTION_RATE": "낮은 AI 언급률",
    "MENTIONS_COMPETITOR_ONLY": "경쟁 병원만 언급",
    "COMPETITOR_VISIBILITY": "경쟁 병원이 더 많이 노출",
    "COMPETITOR_DOMINANCE": "경쟁 병원이 더 많이 노출",
    "NO_PUBLIC_CONTENT": "대응 콘텐츠 없음",
    "WEAK_ENTITY_FACTS": "병원 기본 정보 부족",
    "TECHNICAL_CRAWL_GAP": "검색 반영 보강",
    "SOURCE_GAP": "AI가 참고할 근거 자료 부족",
    "SOURCE_SIGNAL_GAP": "AI가 참고할 근거 자료 부족",
    "SOURCE_AUTHORITY_GAP": "근거 자료의 권위 부족",
    "CONTENT_STALE": "콘텐츠 신선도 낮음",
    "MEDICAL_RISK_BLOCKED": "의료광고 리스크 차단",
}

SEVERITY_DISPLAY_LABELS = {
    "CRITICAL": "심각",
    "HIGH": "높음",
    "MEDIUM": "중간",
    "LOW": "낮음",
}
QUERY_TARGET_PRIORITY_DISPLAY_LABELS = {
    "HIGH": "높음",
    "NORMAL": "보통",
    "LOW": "낮음",
}
QUERY_TARGET_STATUS_DISPLAY_LABELS = {
    "ACTIVE": "운영중",
    "PAUSED": "일시정지",
    "ARCHIVED": "보관됨",
}

EVIDENCE_KEY_DISPLAY_LABELS = {
    "share_of_voice": "AI 언급률",
    "sov": "AI 언급률",
    "sov_pct": "AI 언급률",
    "sov_percent": "AI 언급률",
    "mention_rate": "AI 언급률",
    "mentioned_rate": "AI 언급률",
    "mentioned_count": "언급 횟수",
    "mention_count": "언급 횟수",
    "successful_count": "성공 측정 수",
    "success_count": "성공 측정 수",
    "failed_count": "실패 측정 수",
    "total_count": "전체 측정 수",
    "total_queries": "전체 질문 수",
    "query_count": "질문 수",
    "measured_count": "측정 수",
    "total_measurements": "전체 측정 수",
    "successful_measurements": "성공 측정 수",
    "failed_measurements": "실패 측정 수",
    "source_missing_count": "근거 URL 부족 수",
    "competitor_mention_count": "경쟁 병원 언급 수",
    "competitor_names": "경쟁 병원",
    "competitors": "경쟁 병원",
    "competitor": "경쟁 병원",
    "competitor_share": "경쟁 점유율",
    "competitor_mentions": "경쟁 병원 언급",
    "competitor_mention_rate": "경쟁 병원 언급률",
    "missing_topics": "누락 토픽",
    "topics": "토픽",
    "keyword": "키워드",
    "keywords": "키워드",
    "query": "환자 질문",
    "query_text": "환자 질문",
    "query_name": "환자 질문",
    "query_target": "환자 질문",
    "query_target_name": "환자 질문",
    "target_priority": "질문 우선순위",
    "rule": "진단 규칙",
    "ai_platform": "AI 답변 서비스",
    "platform": "AI 답변 서비스",
    "platforms": "AI 답변 서비스",
    "source_count": "참고 자료 수",
    "source_total": "참고 자료 수",
    "sources": "참고 자료",
    "source_urls": "참고 URL",
    "source_types": "참고 자료 유형",
    "authority_score": "권위 점수",
    "freshness_days": "경과 일수",
    "last_published_at": "최근 발행",
    "last_measured_at": "최근 측정",
    "latest_measured_at": "최근 측정",
    "measured_at": "측정 시각",
    "observed_at": "관측 시각",
    "severity": "심각도",
    "threshold": "기준값",
    "gap_id": "진단 ID",
    "reason": "사유",
    "note": "메모",
    "notes": "메모",
    "message": "메시지",
}

EVIDENCE_VALUE_DISPLAY_LABELS = {
    "chatgpt": "ChatGPT",
    "gemini": "Gemini",
    "claude": "Claude",
    "positive": "긍정",
    "neutral": "중립",
    "negative": "부정",
    "no_successful_measurements": "성공 측정 없음",
    "missing_mention": "병원 미언급",
    "competitor_visibility": "경쟁 병원이 더 많이 노출",
    "source_signal_gap": "AI가 참고할 근거 자료 부족",
    "zero_hospital_mentions": "병원 미언급",
    "mention_rate_below_threshold": "AI 언급률 기준 미달",
    "competitor_mentions_match_or_exceed_hospital_mentions": "경쟁 병원 언급 우세",
    "source_urls_missing_for_majority_of_successful_measurements": "참고 URL 부족",
    "HIGH": "높음",
    "NORMAL": "보통",
    "LOW": "낮음",
    "high": "높음",
    "normal": "보통",
    "low": "낮음",
}

PERCENT_EVIDENCE_KEY_PARTS = ("rate", "share_of_voice", "sov", "percent", "pct")


def _wash_stale_operator_copy(value: str | None) -> str | None:
    if value is None:
        return None
    washed = value
    for old, new in STALE_OPERATOR_COPY_REPLACEMENTS.items():
        washed = washed.replace(old, new)
    return washed


def _serialize_action(action: ExposureAction) -> dict[str, Any]:
    gap = getattr(action, "gap", None)
    target = getattr(action, "query_target", None)
    return {
        "id": str(action.id),
        "hospital_id": str(action.hospital_id),
        "query_target_id": str(action.query_target_id) if action.query_target_id else None,
        "gap_id": str(action.gap_id) if action.gap_id else None,
        "gap_type": getattr(gap, "gap_type", None),
        "severity": getattr(gap, "severity", None),
        "evidence": getattr(gap, "evidence", None) or {},
        "action_type": action.action_type,
        "display": _serialize_action_display(action, gap),
        "title": _wash_stale_operator_copy(action.title),
        "description": _wash_stale_operator_copy(action.description),
        "owner": action.owner,
        "due_month": action.due_month,
        "status": action.status,
        "linked_content_id": str(action.linked_content_id) if action.linked_content_id else None,
        "linked_content": _serialize_content_summary(action.linked_content)
        if getattr(action, "linked_content", None)
        else None,
        "linked_report_id": str(action.linked_report_id) if action.linked_report_id else None,
        "completed_at": _iso_or_none(action.completed_at),
        "created_at": _iso_or_none(action.created_at),
        "updated_at": _iso_or_none(action.updated_at),
        "query_target": _serialize_target(target),
    }


def _serialize_action_display(action: ExposureAction, gap: Any) -> dict[str, Any]:
    action_type = str(_enum_value(action.action_type)) if action.action_type else None
    status = str(_enum_value(action.status)) if action.status else None
    gap_type = str(getattr(gap, "gap_type", "")) or None
    severity = str(getattr(gap, "severity", "")) or None
    evidence_items = _serialize_evidence_items(getattr(gap, "evidence", None) or {})
    return {
        "action_type_label": ACTION_TYPE_DISPLAY_LABELS.get(action_type or "", action_type),
        "status_label": ACTION_STATUS_DISPLAY_LABELS.get(status or "", status),
        "gap_type_label": GAP_TYPE_DISPLAY_LABELS.get(gap_type or "", gap_type),
        "severity_label": SEVERITY_DISPLAY_LABELS.get(severity or "", severity),
        "evidence_summary": _summarize_evidence_items(evidence_items),
        "evidence_items": evidence_items,
    }


def _serialize_evidence_items(evidence: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"key": str(key), "label": _format_evidence_key(str(key)), "value": _format_evidence_value(value, str(key))}
        for key, value in evidence.items()
        if not _is_empty_evidence_value(value)
    ]


def _summarize_evidence_items(items: list[dict[str, str]]) -> str:
    if not items:
        return "근거 없음"
    return " · ".join(f"{item['label']}: {item['value']}" for item in items[:2])


def _format_evidence_key(key: str) -> str:
    return EVIDENCE_KEY_DISPLAY_LABELS.get(key) or EVIDENCE_KEY_DISPLAY_LABELS.get(key.lower()) or key.replace("_", " ")


def _format_evidence_value(value: Any, key: str | None = None) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float)):
        return _format_evidence_number(value, key)
    if isinstance(value, datetime):
        return _iso_or_none(value) or "-"
    if isinstance(value, str):
        return EVIDENCE_VALUE_DISPLAY_LABELS.get(value) or EVIDENCE_VALUE_DISPLAY_LABELS.get(value.lower()) or value
    if isinstance(value, (list, tuple)):
        if not value:
            return "-"
        items = [_format_evidence_value(item, key) for item in value[:5]]
        more = f" 외 {len(value) - len(items)}건" if len(value) > len(items) else ""
        return f"{', '.join(items)}{more}"
    if isinstance(value, dict):
        entries = [(k, v) for k, v in value.items() if not _is_empty_evidence_value(v)]
        if not entries:
            return "-"
        return ", ".join(
            f"{_format_evidence_key(str(k))}: {_format_evidence_value(v, str(k))}"
            for k, v in entries[:4]
        )
    return str(value)


def _format_evidence_number(value: int | float, key: str | None = None) -> str:
    if key and any(part in key.lower() for part in PERCENT_EVIDENCE_KEY_PARTS):
        pct = value * 100 if 0 < value <= 1 else value
        rounded = round(pct, 1)
        return f"{rounded:g}%"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{round(float(value), 2):g}"


def _is_empty_evidence_value(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _serialize_content_summary(item: ContentItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "content_type": _enum_value(item.content_type),
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "scheduled_date": str(item.scheduled_date),
        "status": _enum_value(item.status),
        "title": item.title,
        "query_target_id": str(item.query_target_id) if item.query_target_id else None,
        "exposure_action_id": str(item.exposure_action_id) if item.exposure_action_id else None,
        "brief_status": item.brief_status,
        "brief_approved_at": _iso_or_none(item.brief_approved_at),
        "brief_approved_by": item.brief_approved_by,
        "content_brief": item.content_brief,
    }


def _serialize_philosophy_gate(philosophy: HospitalContentPhilosophy | None) -> dict[str, Any]:
    if philosophy:
        return {"has_approved_philosophy": True, "message": None}
    return {
        "has_approved_philosophy": False,
        "message": (
            "Approved clinic writing standard is missing; keep this content guide in review "
            "before approval or publishing."
        ),
    }


def _serialize_target(target: Any) -> dict[str, Any] | None:
    if target is None:
        return None
    priority = str(_enum_value(target.priority)) if getattr(target, "priority", None) else None
    status = str(_enum_value(target.status)) if getattr(target, "status", None) else None
    return {
        "id": str(target.id),
        "name": target.name,
        "target_intent": _wash_stale_operator_copy(target.target_intent),
        "priority": target.priority,
        "status": target.status,
        "display": {
            "priority_label": QUERY_TARGET_PRIORITY_DISPLAY_LABELS.get(priority or "", priority),
            "status_label": QUERY_TARGET_STATUS_DISPLAY_LABELS.get(status or "", status),
        },
        "target_month": target.target_month,
    }


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
