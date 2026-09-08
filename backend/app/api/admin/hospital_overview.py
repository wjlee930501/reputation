"""Admin API — 병원 현황 한 번 호출 (설계 §4.3 "현황", §4.5).

현황 화면이 상태·예외·이번 달을 각각 부르면 카드마다 다른 시각의 병원을 보여주고,
호출 수가 화면 하나에 8~10개가 된다. 여기서 한 번에 조립한다 — 상태 판정은
`services/hospital_states.py`, 예외는 운영 센터의 인시던트 로더, 공개 여부는
`content_visibility`로, 각각 다른 화면과 같은 함수를 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import arrow
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.operations_center_incident_queries import load_operator_incident_groups
from app.api.public.site import is_public_serving_hospital
from app.core.database import get_db
from app.models.content import ContentItem, ContentSchedule, ContentStatus, monthly_quota_for_plan
from app.models.hospital import Hospital
from app.schemas.hospital_overview import (
    ExceptionCard,
    HospitalOverviewResponse,
    MonthSummary,
    RemainingCondition,
    StateCard,
)
from app.services.content_visibility import assess_sampled_visibility, visibility_load_only
from app.services.essence_readiness import EssenceReadinessState, get_essence_readiness_states
from app.services.hospital_states import content_state, domain_state, public_service_state
from app.services.sov_trend import latest_measured_week, weekly_mention_trend

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — 병원 현황"])

KST = "Asia/Seoul"

_PUBLIC_SERVICE_LABELS = {"live": "공개 중", "paused": "일시 정지", "not_live": "준비 중"}
_CONTENT_LABELS = {"auto": "자동 발행 중", "preparing": "준비 중", "exception": "예외 있음"}
_DOMAIN_LABELS = {
    "connected": "연결됨",
    "checking": "확인 중",
    "problem": "문제",
    "unused": "사용 안 함",
}

# 남은 조건 하나를 사람이 읽는 문구·주체·이동 경로로 바꾸는 유일한 표. 화면마다 이 매핑을
# 다시 쓰면 "자동 진행 중인 일"이 운영자의 할 일 링크로 새어 나간다 — 링크는 사람 몫에만
# 붙인다. (`{hospital_id}`는 아래에서 채운다.)
_CONDITIONS: dict[str, tuple[str, str, str | None]] = {
    "profile_complete": ("필수 병원 정보 입력", "human", "/hospitals/{hospital_id}/profile"),
    "site_built": ("공개 페이지 준비", "system", None),
    "schedule": ("발행 요일 설정", "human", "/hospitals/{hospital_id}/schedule"),
    "sources": ("근거 자료 처리 {count}건", "system", None),
    "essence_review": ("콘텐츠 운영 기준 자동 검수", "system", None),
    # 자료가 하나도 없으면 자동 검수가 기다리기만 한다 — 사람이 채워야 다음이 있다.
    "sources_required": ("공식 채널·근거 자료 등록", "human", "/hospitals/{hospital_id}/profile"),
    # 재개는 헤더의 버튼이 한다. 조건 줄에 링크를 붙이면 같은 일이 두 곳이 된다.
    "service_paused": ("서비스 재개", "human", None),
    "public_service": ("공개 서비스 시작 후 자동 발행", "system", None),
}

_ESCALATED_DRAFT_ACTIONS = ["re_review", "approve_with_override"]


def _remaining(hospital_id: uuid.UUID, keys: tuple[str, ...]) -> list[RemainingCondition]:
    """`hospital_states`가 낸 조건 키(`sources:3` 포함)를 화면 문구로."""
    conditions: list[RemainingCondition] = []
    for key in keys:
        name, _, count = key.partition(":")
        label, actor, href = _CONDITIONS[name]
        conditions.append(
            RemainingCondition(
                key=key,
                label=label.format(count=count),
                actor=actor,
                href=href.format(hospital_id=hospital_id) if href else None,
            )
        )
    return conditions


async def _get_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


async def _incident_cards(db: AsyncSession, hospital_id: uuid.UUID) -> list[ExceptionCard]:
    """운영 센터 인시던트 큐를 이 병원으로 좁혀 그대로 읽는다.

    현황 화면이 자기만의 인시던트 SQL을 쓰면 운영 센터와 다른 목록·다른 허용 행동을
    보여준다. 행동은 서버가 지금 허용한 것만 싣는다 — 비활성 행동을 버튼으로 만들면
    누르는 순간 실패한다.

    자동 복구 중인 건(`requires_operator_action=False`)은 카드로 만들지 않는다 —
    약속한 재시도 창이 남은 RETRYING은 AE의 할 일이 아니다. 병원 목록의 예외 수도
    같은 파이프라인(`count_operator_incidents`)의 묶음 수이므로 두 화면의 숫자가 같다.
    """
    rows = await load_operator_incident_groups(db, hospital_id, now=datetime.now(UTC))
    return [
        ExceptionCard(
            kind="incident",
            id=str(row.incident_id or row.id),
            title=row.impact,
            evidence=row.safe_cause or row.cause_message,
            next_action=row.next_action,
            allowed_actions=[row.action.kind] if row.action and row.action.enabled else [],
            href=row.action.path,
        )
        for row in rows
    ]


def _escalated_draft_card(
    hospital_id: uuid.UUID, readiness: EssenceReadinessState
) -> ExceptionCard | None:
    """자동 검수가 보류 사유를 남긴 초안. 사유 전부가 근거이며 하나라도 지우면 승인 게이트와
    화면이 어긋난다(`api/admin/essence.py`의 예외 승인 조건과 같은 목록).

    초안과 사유는 상태 판정과 같은 묶음 조회(`get_essence_readiness_states`)가 이미 읽었다 —
    여기서 다시 조회하면 같은 JSONB 필터가 두 벌이 되고 쿼리도 하나 더 나간다.
    """
    if readiness.escalated_draft_id is None or not readiness.escalated_draft_findings:
        return None
    return ExceptionCard(
        kind="escalated_draft",
        id=str(readiness.escalated_draft_id),
        title="콘텐츠 운영 기준 자동 검수 보류",
        evidence="\n".join(readiness.escalated_draft_findings),
        next_action="초안을 고쳐 재검수를 받거나, 확인한 근거를 적어 예외 승인하세요.",
        allowed_actions=list(_ESCALATED_DRAFT_ACTIONS),
        href=f"/hospitals/{hospital_id}/essence",
    )


async def _month_summary(db: AsyncSession, hospital: Hospital) -> MonthSummary:
    """이번 달(KST 계약 월) 발행 실적. 기간 경계는 `api/admin/content.py`의 월별 목록과 같다."""
    now = arrow.now(KST)
    period_start = arrow.Arrow(now.year, now.month, 1).date()
    period_end = arrow.Arrow(now.year, now.month, 1).ceil("month").date()

    items = list(
        (
            await db.execute(
                select(ContentItem)
                .options(visibility_load_only())
                .where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.scheduled_date >= period_start,
                    ContentItem.scheduled_date <= period_end,
                    ContentItem.status == ContentStatus.PUBLISHED,
                )
            )
        )
        .scalars()
        .all()
    )
    # DB PUBLISHED만으로 공개 성공을 선언하지 않는다 — 공개 API는 병원 게이트를 먼저 보고
    # 통과한 병원의 글만 내보낸다. 일시정지·준비 중 병원은 발행 글이 몇 편이든 공개 페이지가
    # 아무것도 내보내지 않으므로 전부 보류다.
    if is_public_serving_hospital(hospital):
        visibility = await assess_sampled_visibility(db, items)
        public_count = sum(1 for item in items if visibility[item.id].visible)
    else:
        public_count = 0

    # 이번 달 약정 편수의 정본은 계약 요금제다(PR-0C H-14: 일정의 plan은 계약에서 동기화된다).
    # 활성 일정만 보면 다음 달부터 적용될 교체 일정의 편수가 이번 달로 새어 든다.
    plan = hospital.plan
    if plan is None:
        # 요금제가 기록되기 전의 레거시 병원만 일정으로 되돌아간다 — 이번 달에 이미 시작한
        # 일정만 본다.
        plan = (
            await db.execute(
                select(ContentSchedule.plan)
                .where(
                    ContentSchedule.hospital_id == hospital.id,
                    ContentSchedule.is_active,
                    ContentSchedule.active_from <= period_end,
                )
                .order_by(ContentSchedule.active_from.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    measured = latest_measured_week(await weekly_mention_trend(db, hospital.id))
    next_month = now.shift(months=1)
    return MonthSummary(
        year=now.year,
        month=now.month,
        published_count=len(items),
        public_count=public_count,
        withheld_count=len(items) - public_count,
        planned_total=monthly_quota_for_plan(plan) or 0,
        mention_rate=measured[0] if measured else None,
        mention_rate_measured_at=measured[1] if measured else None,
        next_report_date=date(next_month.year, next_month.month, 1),
    )


@router.get("/{hospital_id}/overview", response_model=HospitalOverviewResponse)
async def get_hospital_overview(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HospitalOverviewResponse:
    """현황 화면 한 장을 채우는 유일한 호출."""
    hospital = await _get_or_404(db, hospital_id)
    readiness = (await get_essence_readiness_states(db, [hospital_id]))[hospital_id]

    public = public_service_state(hospital)
    content = content_state(
        hospital,
        essence_current=readiness.current,
        unprocessed_sources=readiness.unprocessed_sources,
        required_sources=readiness.required_sources,
        escalated_draft=readiness.escalated_draft,
    )
    domain = domain_state(hospital)

    exceptions = await _incident_cards(db, hospital_id)
    draft_card = _escalated_draft_card(hospital_id, readiness)
    if draft_card is not None:
        exceptions.append(draft_card)

    return HospitalOverviewResponse(
        hospital_id=hospital.id,
        public_service=StateCard(
            kind=public.kind,
            label=_PUBLIC_SERVICE_LABELS[public.kind],
            remaining=_remaining(hospital_id, public.remaining),
        ),
        content=StateCard(
            kind=content.kind,
            label=_CONTENT_LABELS[content.kind],
            remaining=_remaining(hospital_id, content.remaining),
        ),
        domain=StateCard(
            kind=domain.kind,
            label=_DOMAIN_LABELS[domain.kind],
            remaining=[],
            reason=domain.reason,
            last_checked_at=domain.last_checked_at,
            last_check_ok=domain.last_check_ok,
        ),
        exceptions=exceptions,
        month=await _month_summary(db, hospital),
    )
