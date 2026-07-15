"""
Admin API — 콘텐츠 스케줄 + 발행
POST   /admin/hospitals/{id}/schedule               — 스케줄 설정
GET    /admin/hospitals/{id}/content                — 콘텐츠 목록 (월별)
GET    /admin/hospitals/{id}/content/{cid}          — 상세 조회
PATCH  /admin/hospitals/{id}/content/{cid}          — 제목/본문/meta 수정
PATCH  /admin/hospitals/{id}/content/{cid}/brief    — 타깃 질의/액션/brief 수정
POST   /admin/hospitals/{id}/content/{cid}/publish  — 발행
POST   /admin/hospitals/{id}/content/{cid}/reject   — 반려
"""

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import arrow
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.content import ContentItem, ContentSchedule, ContentStatus
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.sov import AIQueryTarget, ExposureAction
from app.schemas.content import ContentBriefUpdate, ContentItemDetail, ContentItemResponse
from app.services import notifier
from app.services.audit_log import default_actor, write_audit_log
from app.services.content_engine import (
    FORBIDDEN_CHECK_FIELDS,
    _normalize_references,
    forbidden_check_text,
)
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_content_site_revalidate_safe,
)
from app.services.content_brief import (
    BRIEF_STATUS_APPROVED,
    BRIEF_STATUS_DRAFT,
    BRIEF_STATUSES,
    build_content_brief,
)
from app.services.content_calendar import generate_monthly_slots
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, screen_content_against_philosophy
from app.services.essence_readiness import get_current_approved_philosophy
from app.services.exposure_content_linker import (
    ensure_brief_capable_action,
    link_content_to_exposure_action,
    unlink_content_from_exposure_action,
)
from app.services.gcs_utils import get_signed_url
from app.utils.medical_filter import check_forbidden
from app.workers.tasks import regenerate_content_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Content"])

CONTENT_TYPE_DISPLAY_LABELS = {
    "FAQ": "자주 묻는 질문",
    "DISEASE": "질환 가이드",
    "TREATMENT": "시술 안내",
    "COLUMN": "원장 칼럼",
    "HEALTH": "건강 정보",
    "LOCAL": "지역 특화",
    "NOTICE": "공지",
}

CONTENT_STATUS_DISPLAY_LABELS = {
    "DRAFT": "초안",
    "READY": "발행 준비",
    "PUBLISHED": "발행 완료",
    "REJECTED": "반려",
}

BRIEF_STATUS_DISPLAY_LABELS = {
    "DRAFT": "콘텐츠 가이드 작성중",
    "APPROVED": "콘텐츠 가이드 승인",
    "NEEDS_REVIEW": "콘텐츠 가이드 재검토",
}

ESSENCE_STATUS_DISPLAY_LABELS = {
    "ALIGNED": "운영 기준 통과",
    "NEEDS_ESSENCE_REVIEW": "운영 기준 재검토",
    "MISSING_APPROVED_PHILOSOPHY": "운영 기준 없음",
}


class ScheduleCreate(BaseModel):
    plan: str = Field(pattern=r"^PLAN_(16|12|8)$")  # PLAN_16 | PLAN_12 | PLAN_8
    publish_days: list[int]  # [1, 4] = 화·금
    active_from: date

    @field_validator("publish_days")
    @classmethod
    def validate_publish_days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("publish_days must not be empty")
        for day in v:
            if day not in range(7):
                raise ValueError(f"Invalid day: {day}. Must be 0-6 (월-일)")
        return list(set(v))  # 중복 제거


class ReferencePatchItem(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=500)


class ContentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = None
    # DB 컬럼이 VARCHAR(300) — 더 길게 허용하면 검증을 통과한 뒤 commit에서 DataError 500.
    meta_description: str | None = Field(default=None, max_length=300)
    # FAQ 분리 필드 — 공개 표면(FAQPage rich result)에 노출되므로 본문과 동일하게
    # 금지 표현 검사를 거친다. 위반 시 AE가 직접 수정할 수 있는 유일한 경로 (P1-2).
    faq_question: str | None = Field(default=None, max_length=300)
    faq_answer_summary: str | None = Field(default=None, max_length=600)
    # 참고 자료 — 발행 게이트가 유효 references ≥1을 요구하므로, 생성 단계에서 전부
    # 탈락한 아이템의 발행 차단을 풀 수 있는 보정 경로 (A1).
    references: list[ReferencePatchItem] | None = None


class PublishBody(BaseModel):
    published_by: str = Field(min_length=1, max_length=100)

    @field_validator("published_by", mode="before")
    @classmethod
    def normalize_published_by(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("published_by is required")
        return cleaned


@router.post("/{hospital_id}/schedule", status_code=status.HTTP_201_CREATED)
async def set_schedule(
    hospital_id: uuid.UUID,
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    콘텐츠 스케줄 설정.
    저장 즉시 해당 월의 ContentItem 슬롯을 자동 생성.
    """
    hospital = await _get_hospital(db, hospital_id)

    # active_from이 과거면 첫날부터 야간 배치가 이미 지나간 슬롯이 무더기로 생기고,
    # 아래 즉시 생성 큐잉이 상한 없이 폭주한다 (R2). 과거 시작일은 운영상 의미도 없다.
    today_kst = arrow.now("Asia/Seoul").date()
    if body.active_from < today_kst:
        raise HTTPException(
            status_code=422,
            detail=f"시작일(active_from)은 오늘({today_kst}) 이후여야 합니다. 과거 날짜로는 스케줄을 시작할 수 없습니다.",
        )

    # 기존 스케줄 비활성화
    old_stmt = select(ContentSchedule).where(
        ContentSchedule.hospital_id == hospital_id,
        ContentSchedule.is_active,
    )
    old_result = await db.execute(old_stmt)
    old_schedules = old_result.scalars().all()
    old_schedule_ids = [old.id for old in old_schedules]
    for old in old_schedules:
        old.is_active = False
    if old_schedule_ids:
        # 재설정 시 구 스케줄의 미발행 미래 슬롯을 body 유무와 무관하게 정리한다.
        # 이미 본문이 생성된(body 있음) 구 슬롯이 남으면 새 스케줄 슬롯과 같은 날짜에
        # 중복 발행/중복 Slack/중복 생성 비용이 발생한다. PUBLISHED 슬롯은 발행 이력이므로
        # 절대 삭제하지 않고, 과거 슬롯도 이력 보존을 위해 남긴다 (오늘 이후만 정리).
        # 이월(carried_over_from IS NOT NULL) 미발행 슬롯도 보존한다: 월말 반려로 이월된
        # 슬롯을 스케줄 재설정만으로 지우면 아직 발행 못 한 이월 콘텐츠가 유실된다.
        await db.execute(
            delete(ContentItem).where(
                ContentItem.schedule_id.in_(old_schedule_ids),
                ContentItem.status != ContentStatus.PUBLISHED,
                ContentItem.scheduled_date >= today_kst,
                ContentItem.carried_over_from.is_(None),
            )
        )

    schedule = ContentSchedule(
        hospital_id=hospital_id,
        plan=body.plan,
        publish_days=body.publish_days,
        active_from=body.active_from,
    )
    db.add(schedule)
    await db.flush()

    # 이번 달 콘텐츠 슬롯 자동 생성
    target_month = arrow.get(body.active_from).floor("month")
    try:
        slots = generate_monthly_slots(
            body.plan,
            body.publish_days,
            target_month,
            start_date=body.active_from,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created_items: list[ContentItem] = []
    for slot_date, ctype, seq_no, total in slots:
        item = ContentItem(
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ctype,
            sequence_no=seq_no,
            total_count=total,
            scheduled_date=slot_date,
            status=ContentStatus.DRAFT,
        )
        db.add(item)
        created_items.append(item)

    hospital.schedule_set = True
    # 병원 헤더/목록의 plan이 실제 운영 스케줄과 어긋나지 않도록 동기화 (A3).
    hospital.plan = Plan(body.plan)
    if hospital.site_live:
        hospital.status = HospitalStatus.ACTIVE

    # 순서 규약: write_audit_log → db.commit() → external side-effect(apply_async).
    await write_audit_log(
        db,
        action="set_schedule",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="content_schedule",
        target_id=schedule.id,
        detail={
            "plan": body.plan,
            "publish_days": body.publish_days,
            "active_from": str(body.active_from),
            "slots_created": len(slots),
            "old_schedule_ids": [str(sid) for sid in old_schedule_ids],
        },
    )

    await db.commit()
    await db.refresh(schedule)

    # active_from이 오늘/내일인 스케줄의 첫 슬롯은 어젯밤 야간 배치를 이미 놓쳤으므로
    # 즉시 생성 태스크를 큐잉한다 (P2-9). 대상은 [오늘, 내일]로 한정한다 (R2 —
    # active_from 검증과 함께 과거 슬롯 무제한 큐잉 방지).
    # 큐잉 실패(브로커 장애 등)는 raise하지 않는다: 스케줄 저장은 이미 커밋됐고,
    # 해당 슬롯은 야간 catch-up 윈도우가 어차피 다시 집는다. 로그 + Slack 운영 알림으로 강등.
    tomorrow_kst = arrow.now("Asia/Seoul").shift(days=1).date()
    try:
        for item in created_items:
            if today_kst <= item.scheduled_date <= tomorrow_kst:
                regenerate_content_item.apply_async(args=[str(item.id)], queue="content")
    except Exception as exc:
        logger.warning(
            "immediate content generation enqueue failed for hospital %s: %s", hospital_id, exc
        )
        try:
            await notifier.notify_ops_alert(
                title="콘텐츠 즉시 생성 큐잉 실패",
                message=(
                    f"병원: {hospital.name}\n"
                    f"스케줄 저장은 완료됐지만 오늘/내일 슬롯의 즉시 생성 태스크 큐잉에 실패했습니다.\n"
                    f"오류: `{str(exc)[:200]}`\n"
                    f"해당 슬롯은 오늘 밤 자동 생성에서 재시도됩니다. 급한 경우 Admin에서 수동 재생성해 주세요."
                ),
            )
        except Exception:
            logger.exception("enqueue-failure ops alert delivery failed (non-fatal)")

    return {
        "schedule_id": str(schedule.id),
        "plan": body.plan,
        "publish_days": body.publish_days,
        "slots_created": len(slots),
        "first_publish_date": str(slots[0][0]) if slots else None,
    }


@router.get("/{hospital_id}/schedule")
async def get_schedule(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """현재 활성 콘텐츠 스케줄 조회 — 없으면 404 (A2)."""
    await _get_hospital(db, hospital_id)

    result = await db.execute(
        select(ContentSchedule)
        .where(
            ContentSchedule.hospital_id == hospital_id,
            ContentSchedule.is_active,
        )
        .order_by(ContentSchedule.created_at.desc())
        .limit(1)
    )
    schedule = result.scalars().first()
    if not schedule:
        raise HTTPException(status_code=404, detail="활성 콘텐츠 스케줄이 없습니다.")

    return {
        "schedule_id": str(schedule.id),
        "hospital_id": str(schedule.hospital_id),
        "plan": _enum_value(schedule.plan),
        "publish_days": schedule.publish_days,
        "active_from": str(schedule.active_from),
        "is_active": bool(schedule.is_active),
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
    }


@router.get("/{hospital_id}/content", response_model=list[ContentItemResponse])
async def list_content(
    hospital_id: uuid.UUID,
    # month=13 같은 값이 arrow.Arrow까지 내려가면 ValueError → 500 (P2-11). 경계는 여기서.
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    status_filter: Optional[ContentStatus] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """월별 콘텐츠 목록"""
    now = arrow.now("Asia/Seoul")
    year = year or now.year
    month = month or now.month

    period_start = arrow.Arrow(year, month, 1).date()
    period_end = arrow.Arrow(year, month, 1).ceil("month").date()

    stmt = (
        select(ContentItem)
        .where(
            ContentItem.hospital_id == hospital_id,
            ContentItem.scheduled_date >= period_start,
            ContentItem.scheduled_date <= period_end,
        )
        .order_by(ContentItem.scheduled_date)
    )

    if status_filter:
        stmt = stmt.where(ContentItem.status == status_filter)

    result = await db.execute(stmt)
    items = result.scalars().all()

    return [_serialize_item(i) for i in items]


@router.get("/{hospital_id}/content/{content_id}", response_model=ContentItemDetail)
async def get_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """콘텐츠 상세 (본문 포함)"""
    item = await _get_content(db, content_id, hospital_id)
    return _serialize_item(item, full=True)


@router.patch("/{hospital_id}/content/{content_id}", response_model=ContentItemDetail)
async def update_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: ContentPatch,
    db: AsyncSession = Depends(get_db),
):
    """
    제목/본문/meta/FAQ/참고자료 수정.
    저장 시 의료광고 금지표현 검사 → 위반 시 400 + 위반 목록 반환.
    """
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)
    was_published = item.status == ContentStatus.PUBLISHED
    should_revalidate = was_published and _has_public_site(hospital)
    if should_revalidate:
        ensure_site_revalidate_configured()

    # 금지 표현 검사 — 수정 결과로 남게 될 전체 공개 텍스트 필드. 필드 목록은 생성
    # 엔진의 FORBIDDEN_CHECK_FIELDS를 그대로 재사용한다 (R3): 공개 텍스트 필드가 추가될
    # 때 생성 경로와 수정 경로가 어긋나는 P1-2류 회귀를 차단.
    effective_values = {
        field: (
            getattr(body, field)
            if getattr(body, field, None) is not None
            else getattr(item, field, None)
        )
        for field in FORBIDDEN_CHECK_FIELDS
    }
    violations = list(dict.fromkeys(check_forbidden(forbidden_check_text(effective_values))))

    if violations:
        raise HTTPException(
            status_code=400,
            detail={"message": "의료광고 금지 표현이 포함되어 있습니다.", "violations": violations},
        )

    # 참고 자료 (A1) — 생성 경로와 동일한 정규화/화이트리스트 검증을 거친다.
    # 일부가 탈락하면 운영자가 모르는 채 저장되는 것보다 명시적으로 거절하는 편이 안전.
    if body.references is not None:
        raw_refs = [ref.model_dump() for ref in body.references]
        normalized_refs = _normalize_references(raw_refs)
        if len(normalized_refs) < len(raw_refs):
            raise HTTPException(
                status_code=400,
                detail={
                    "message": (
                        "참고 자료 중 사용할 수 없는 항목이 있습니다. "
                        "허용된 공신력 있는 출처의 http(s) URL과 제목을 입력해 주세요. (최대 5개)"
                    ),
                    "accepted_count": len(normalized_refs),
                    "submitted_count": len(raw_refs),
                },
            )
        item.references_list = normalized_refs

    body_changed = False
    if body.title is not None:
        item.title = body.title
    if body.body is not None and body.body != item.body:
        item.body = body.body
        body_changed = True
    if body.meta_description is not None:
        item.meta_description = body.meta_description
    if body.faq_question is not None:
        item.faq_question = body.faq_question
    if body.faq_answer_summary is not None:
        item.faq_answer_summary = body.faq_answer_summary

    if body_changed:
        item.body_updated_at = datetime.now(timezone.utc)

    philosophy = await _get_approved_philosophy(db, hospital_id)
    screening = screen_content_against_philosophy(item, philosophy)
    item.content_philosophy_id = philosophy.id if philosophy else None
    item.essence_status = screening.status
    item.essence_check_summary = screening.summary

    await db.commit()
    await db.refresh(item)
    if should_revalidate:
        await trigger_content_site_revalidate_safe(
            hospital.slug, item.id, hospital_name=hospital.name, treatments=hospital.treatments
        )
    return _serialize_item(item, full=True)


@router.patch("/{hospital_id}/content/{content_id}/brief", response_model=ContentItemDetail)
async def update_content_brief(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: ContentBriefUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update query/action links and the operator-editable content brief."""
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)
    await _apply_content_brief_update(db, hospital, item, body)

    await db.commit()
    await db.refresh(item)
    return _serialize_item(item, full=True)


async def _lock_content_status(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    fallback: ContentStatus,
) -> ContentStatus:
    """Row-lock the content item and return its authoritative status (API-1).

    Serializes concurrent publish attempts so the not-PUBLISHED check is decided
    under the lock, not on a stale read. No-op (returns the in-memory status) for
    unit-test fakes that don't implement ``execute``.
    """
    if not hasattr(db, "execute"):
        return fallback
    result = await db.execute(
        select(ContentItem.status)
        .where(ContentItem.id == content_id, ContentItem.hospital_id == hospital_id)
        .with_for_update()
    )
    locked = result.scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=404, detail="Content not found")
    return locked


@router.post("/{hospital_id}/content/{content_id}/publish")
async def publish_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    body: PublishBody,
    db: AsyncSession = Depends(get_db),
):
    """
    AE가 검토 후 [발행] 클릭.
    병원 정보·콘텐츠 허브 공개 표면에 즉시 게재.
    """
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)

    # 동시 발행 경합 차단: 행 잠금 후 권위 있는 상태로 재확인.
    current_status = await _lock_content_status(db, hospital_id, content_id, item.status)
    if current_status == ContentStatus.PUBLISHED:
        raise HTTPException(status_code=400, detail="Already published")
    if not item.body:
        raise HTTPException(status_code=400, detail="Content not generated yet")
    if not _has_required_references(item):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "권위 있는 참고 자료가 1개 이상 필요합니다.",
                "missing": "references",
            },
        )
    should_revalidate = _has_public_site(hospital)
    if should_revalidate:
        ensure_site_revalidate_configured()

    full_text = _forbidden_check_text(item)
    violations = check_forbidden(full_text)
    if violations:
        item.essence_status = "NEEDS_ESSENCE_REVIEW"
        item.essence_check_summary = {
            "blocking": True,
            "findings": [f"의료광고 금지 표현: {', '.join(violations)}"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "의료광고 금지 표현이 포함되어 있어 발행할 수 없습니다.",
                "violations": violations,
            },
        )

    philosophy = await _get_approved_philosophy(db, hospital_id)
    screening = screen_content_against_philosophy(item, philosophy)
    item.content_philosophy_id = philosophy.id if philosophy else None
    item.essence_status = screening.status
    item.essence_check_summary = screening.summary
    if screening.status != ESSENCE_STATUS_ALIGNED:
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "병원답게 콘텐츠를 쓰기 위한 기준 검수 상태 때문에 발행할 수 없습니다.",
                "essence_status": screening.status,
                "essence_check_summary": screening.summary,
            },
        )

    item.status = ContentStatus.PUBLISHED
    item.published_at = datetime.now(timezone.utc)
    item.published_by = body.published_by
    await write_audit_log(
        db,
        action="publish_content",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="content_item",
        target_id=item.id,
        detail={
            "title": item.title,
            "content_type": _enum_value(item.content_type),
            "scheduled_date": str(item.scheduled_date) if item.scheduled_date else None,
            "claimed_by": body.published_by,
            "essence_status": item.essence_status,
        },
    )
    await db.commit()

    # Slack 알림
    await notifier.notify_content_published(hospital.name, item.title or "")

    # 사이트 캐시 무효화 — 새 콘텐츠가 sitemap/hub/library/관련 풀페이지에 즉시 반영되도록.
    # 커밋 이후이므로 실패해도 raise하지 않는다 (P2-9b): 발행은 이미 성공했는데 500을
    # 돌려주면 AE가 재시도하다 "Already published"를 만난다. 경고 로그 + Slack 운영 알림.
    if should_revalidate:
        await trigger_content_site_revalidate_safe(
            hospital.slug,
            item.id,
            hospital_name=hospital.name,
            treatments=hospital.treatments,
        )

    return {"detail": "Published", "published_at": item.published_at.isoformat()}


@router.post("/{hospital_id}/content/{content_id}/reject")
async def reject_content(
    hospital_id: uuid.UUID,
    content_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """반려 — 야간 재생성 큐에 다시 들어감"""
    item = await _get_content(db, content_id, hospital_id)
    hospital = await _get_hospital(db, hospital_id)
    previous_title = item.title
    previous_status = _enum_value(item.status)
    should_revalidate = previous_status == ContentStatus.PUBLISHED.value and _has_public_site(
        hospital
    )
    if should_revalidate:
        ensure_site_revalidate_configured()
    item.status = ContentStatus.REJECTED
    item.body = None  # 초기화 → 야간 생성 태스크가 다시 처리
    item.title = None
    item.image_url = None
    # 발행됐던 아이템을 반려하면 발행 메타도 초기화 — 재생성·재발행 시 이전 발행 기록이
    # 새 본문에 잘못 남는 것 방지.
    item.published_at = None
    item.published_by = None
    item.generated_at = None
    # 야간 생성은 scheduled_date == 내일 인 슬롯만 집는다. 발행일 당일(또는 그 후) 반려된
    # 아이템은 그대로 두면 영원히 재생성되지 않으므로 내일로 재스케줄한다.
    today_seoul = arrow.now("Asia/Seoul").date()
    original_scheduled_date = item.scheduled_date
    if item.scheduled_date and item.scheduled_date <= today_seoul:
        item.scheduled_date = arrow.now("Asia/Seoul").shift(days=1).date()
        # 월말 반려 carry-over: 재스케줄이 원래 발행 예정일과 다른 달로 넘어가면
        # 원래 날짜를 기록한다 — 야간 생성 우선 처리 + 아침 Slack '전월 이월' 표시 근거.
        # 재반려 시 최초 이월 기준일을 덮어쓰지 않는다.
        crossed_month = (item.scheduled_date.year, item.scheduled_date.month) != (
            original_scheduled_date.year,
            original_scheduled_date.month,
        )
        if crossed_month and item.carried_over_from is None:
            item.carried_over_from = original_scheduled_date
    await write_audit_log(
        db,
        action="reject_content",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="content_item",
        target_id=content_id,
        detail={
            "previous_title": previous_title,
            "previous_status": previous_status,
            "scheduled_date": str(item.scheduled_date) if item.scheduled_date else None,
            "carried_over_from": str(item.carried_over_from) if item.carried_over_from else None,
        },
    )
    await db.commit()
    if should_revalidate:
        await trigger_content_site_revalidate_safe(
            hospital.slug, item.id, hospital_name=hospital.name, treatments=hospital.treatments
        )
    return {"detail": "Rejected. Will be regenerated tonight."}


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital(db, hospital_id) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _has_public_site(hospital: Hospital) -> bool:
    return hospital.status == HospitalStatus.ACTIVE and bool(hospital.site_live)


def _has_required_references(item: ContentItem) -> bool:
    references = item.references_list or []
    return any(isinstance(ref, dict) and ref.get("title") and ref.get("url") for ref in references)


async def _get_content(db, content_id, hospital_id) -> ContentItem:
    item = await db.get(ContentItem, content_id)
    if not item or item.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Content not found")
    return item


async def _get_query_target_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    query_target_id: uuid.UUID,
) -> AIQueryTarget:
    result = await db.execute(
        select(AIQueryTarget)
        .options(selectinload(AIQueryTarget.variants))
        .where(
            AIQueryTarget.id == query_target_id,
            AIQueryTarget.hospital_id == hospital_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(
            status_code=400, detail="query_target_id does not belong to this hospital"
        )
    return target


async def _get_exposure_action_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    exposure_action_id: uuid.UUID,
) -> ExposureAction:
    result = await db.execute(
        select(ExposureAction)
        .options(selectinload(ExposureAction.query_target))
        .where(
            ExposureAction.id == exposure_action_id,
            ExposureAction.hospital_id == hospital_id,
        )
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(
            status_code=400, detail="exposure_action_id does not belong to this hospital"
        )
    return action


async def _get_approved_philosophy(db: AsyncSession, hospital_id: uuid.UUID):
    """Compatibility seam backed by the current-source resolver.

    Keeping this helper also gives focused API tests a stable monkeypatch target.
    """

    return await get_current_approved_philosophy(db, hospital_id)


async def _apply_content_brief_update(
    db: AsyncSession,
    hospital: Hospital,
    item: ContentItem,
    body: ContentBriefUpdate,
) -> None:
    fields = body.model_fields_set
    if body.brief_status is not None and body.brief_status not in BRIEF_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid brief_status")

    query_target: AIQueryTarget | None = None
    exposure_action: ExposureAction | None = None
    link_changed = False
    previous_exposure_action_id = item.exposure_action_id

    if "exposure_action_id" in fields:
        link_changed = True
        if body.exposure_action_id:
            if _enum_value(item.status) == ContentStatus.PUBLISHED.value:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot link a published content item to an AI exposure work item",
                )
            exposure_action = await _get_exposure_action_or_404(
                db,
                hospital.id,
                body.exposure_action_id,
            )
            previous_action = None
            if previous_exposure_action_id and previous_exposure_action_id != exposure_action.id:
                previous_action = await _get_exposure_action_or_404(
                    db,
                    hospital.id,
                    previous_exposure_action_id,
                )
            await link_content_to_exposure_action(
                db,
                action=exposure_action,
                item=item,
                previous_action=previous_action,
            )
        else:
            if previous_exposure_action_id:
                previous_action = await _get_exposure_action_or_404(
                    db,
                    hospital.id,
                    previous_exposure_action_id,
                )
                await unlink_content_from_exposure_action(db, previous_action, content_id=item.id)
            item.exposure_action_id = None
    elif item.exposure_action_id:
        exposure_action = await _get_exposure_action_or_404(
            db, hospital.id, item.exposure_action_id
        )

    if "query_target_id" in fields:
        link_changed = True
        item.query_target_id = body.query_target_id
        if body.query_target_id:
            query_target = await _get_query_target_or_404(db, hospital.id, body.query_target_id)
    elif item.query_target_id:
        query_target = await _get_query_target_or_404(db, hospital.id, item.query_target_id)

    if query_target is None and exposure_action and exposure_action.query_target_id:
        query_target = await _get_query_target_or_404(
            db, hospital.id, exposure_action.query_target_id
        )
        if item.query_target_id is None:
            item.query_target_id = query_target.id

    if "content_brief" in fields:
        item.content_brief = body.content_brief

    should_regenerate = body.regenerate_brief or (
        link_changed
        and "content_brief" not in fields
        and (query_target is not None or exposure_action is not None)
    )
    if should_regenerate:
        philosophy = await _get_approved_philosophy(db, hospital.id)
        item.content_brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            query_target=query_target,
            exposure_action=exposure_action,
            philosophy=philosophy,
        )
        if "brief_status" not in fields:
            item.brief_status = BRIEF_STATUS_DRAFT
            item.brief_approved_at = None
            item.brief_approved_by = None

    if "brief_status" in fields:
        if body.brief_status == BRIEF_STATUS_APPROVED:
            if not item.content_brief:
                raise HTTPException(status_code=400, detail="Cannot approve an empty content guide")
            philosophy = await _get_approved_philosophy(db, hospital.id)
            if philosophy is None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot approve a content guide without an approved clinic writing standard",
                )
            item.content_philosophy_id = philosophy.id
            item.brief_status = body.brief_status
            item.brief_approved_at = datetime.now(timezone.utc)
            item.brief_approved_by = body.brief_approved_by or item.brief_approved_by or "AE"
        else:
            item.brief_status = body.brief_status
            item.brief_approved_at = None
            item.brief_approved_by = None
    elif "brief_approved_by" in fields and item.brief_status == BRIEF_STATUS_APPROVED:
        item.brief_approved_by = body.brief_approved_by


def _ensure_brief_capable_exposure_action(exposure_action: ExposureAction) -> None:
    ensure_brief_capable_action(exposure_action)


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _display_label(labels: dict[str, str], value) -> str | None:
    normalized = _enum_value(value)
    if normalized is None:
        return None
    text = str(normalized)
    return labels.get(text, text)


def _content_review_display(
    item: ContentItem, status_value: str | None
) -> dict[str, str | bool | None]:
    if status_value == ContentStatus.PUBLISHED.value:
        return {"label": "발행 완료", "reason": None, "publishable": False}
    if status_value == ContentStatus.REJECTED.value:
        return {"label": "반려됨", "reason": "야간 재생성 대기", "publishable": False}
    if not item.title or not item.body:
        return {"label": "생성 전", "reason": "야간 자동 생성 대기", "publishable": False}
    if item.essence_status != ESSENCE_STATUS_ALIGNED:
        reason = (
            "운영 기준 재검토 필요"
            if item.essence_status == "NEEDS_ESSENCE_REVIEW"
            else "승인된 운영 기준 없음"
            if item.essence_status == "MISSING_APPROVED_PHILOSOPHY"
            else "운영 기준 미검수"
        )
        return {"label": "검토 필요", "reason": reason, "publishable": False}
    return {"label": "발행 가능", "reason": None, "publishable": True}


def _serialize_item_display(
    item: ContentItem, content_type: str | None, status_value: str | None
) -> dict:
    return {
        "content_type_label": _display_label(CONTENT_TYPE_DISPLAY_LABELS, content_type),
        "status_label": _display_label(CONTENT_STATUS_DISPLAY_LABELS, status_value),
        "brief_status_label": _display_label(BRIEF_STATUS_DISPLAY_LABELS, item.brief_status),
        "essence_status_label": _display_label(ESSENCE_STATUS_DISPLAY_LABELS, item.essence_status),
        "review": _content_review_display(item, status_value),
    }


def _forbidden_check_text(item: ContentItem) -> str:
    """발행/검수 게이트용 금지 표현 검사 텍스트 — 필드 목록은 생성 엔진과 단일 진실 (R3)."""
    return forbidden_check_text(
        {field: getattr(item, field, None) for field in FORBIDDEN_CHECK_FIELDS}
    )


def _build_compliance_summary(item: ContentItem, status_value: str | None) -> dict:
    full_text = _forbidden_check_text(item)
    forbidden_violations = list(dict.fromkeys(check_forbidden(full_text))) if full_text else []
    blockers: list[str] = []
    if status_value == ContentStatus.PUBLISHED.value:
        blockers.append("이미 발행된 콘텐츠입니다.")
    if not item.title or not item.body:
        blockers.append("본문 생성이 필요합니다.")
    if forbidden_violations:
        blockers.append("의료광고 금지 표현이 포함되어 있습니다.")
    if item.title and item.body and not _has_required_references(item):
        blockers.append("권위 있는 참고 자료가 1개 이상 필요합니다.")
    if item.essence_status != ESSENCE_STATUS_ALIGNED:
        blockers.append("승인된 콘텐츠 운영 기준 검수를 통과해야 합니다.")

    return {
        "status": "PASS" if not blockers else "BLOCKED",
        "publishable": not blockers,
        "blockers": blockers,
        "forbidden_violations": forbidden_violations,
        "references_count": len(item.references_list or []),
        "essence_status": item.essence_status,
        "essence_check_summary": item.essence_check_summary,
    }


def _serialize_item(item: ContentItem, full: bool = False) -> dict:
    content_type = _enum_value(item.content_type)
    status_value = _enum_value(item.status)
    d = {
        "id": str(item.id),
        "content_type": content_type,
        "sequence_no": item.sequence_no,
        "total_count": item.total_count,
        "title": item.title,
        "meta_description": item.meta_description,
        "image_url": get_signed_url(item.image_url) if item.image_url else None,
        "scheduled_date": str(item.scheduled_date),
        # 전월 이월 기준일 (내부 운영 데이터 — 공개 /site 직렬화에는 미포함)
        "carried_over_from": (
            str(item.carried_over_from) if getattr(item, "carried_over_from", None) else None
        ),
        "status": status_value,
        "display": _serialize_item_display(item, content_type, status_value),
        "generated_at": item.generated_at.isoformat() if item.generated_at else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "published_by": item.published_by,
        "body_updated_at": item.body_updated_at.isoformat() if item.body_updated_at else None,
        "references": item.references_list or [],
        "faq_question": item.faq_question,
        "faq_answer_summary": item.faq_answer_summary,
        "content_philosophy_id": str(item.content_philosophy_id)
        if item.content_philosophy_id
        else None,
        "query_target_id": str(item.query_target_id) if item.query_target_id else None,
        "exposure_action_id": str(item.exposure_action_id) if item.exposure_action_id else None,
        "content_brief": item.content_brief,
        "brief_status": item.brief_status,
        "brief_approved_at": item.brief_approved_at.isoformat() if item.brief_approved_at else None,
        "brief_approved_by": item.brief_approved_by,
        "essence_status": item.essence_status,
        "essence_check_summary": item.essence_check_summary,
        "compliance": _build_compliance_summary(item, status_value),
    }
    if full:
        d["body"] = item.body
        d["image_prompt"] = item.image_prompt
    return d
