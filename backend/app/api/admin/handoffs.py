"""Explicit, audited customer handoff transitions."""

import uuid
from datetime import UTC, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.admin.accounts import require_active_account, require_owner_account
from app.api.admin.hospitals import (
    allocate_hospital_slug,
    serialize_handoff_summary,
    serialize_hospital_detail,
)
from app.core.database import get_db
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.content import ContentSchedule
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, Plan
from app.schemas.handoff import (
    ContractRegistration,
    HandoffAccept,
    HandoffContract,
    HandoffResponse,
)
from app.services.audit_log import verified_request_actor, write_audit_log
from app.services.hospital_duplicates import find_duplicate_hospitals

router = APIRouter(prefix="/admin/handoffs", tags=["Admin — Handoffs"])

#: 계약 등록은 `/admin/hospitals` 아래에 산다 — 운영자에게는 "병원을 만드는 일"이고,
#: 상태 전이 코드는 이 파일이 정본이라 두 라우터를 한 모듈에서 유지한다.
hospitals_router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Handoffs"])

KST = timezone(timedelta(hours=9))


class HandoffCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)
    contract_reference: str = Field(min_length=1, max_length=200)
    contract_effective_at: AwareDatetime
    plan: Plan
    sla_due_at: AwareDatetime


def stale_handoff_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "HANDOFF_VERSION_CONFLICT",
            "message": "다른 운영자가 먼저 변경했습니다. 최신 상태를 다시 불러와 주세요.",
            "reload": True,
        },
    )


def _hospital_exists(hospital_id: uuid.UUID | None) -> dict[str, object]:
    """중복 병원 409 본문. 화면은 재시도 대신 기존 병원을 여는 선택지를 그린다."""
    detail: dict[str, object] = {
        "code": "HOSPITAL_EXISTS",
        "message": "이미 등록된 병원입니다.",
    }
    if hospital_id is not None:
        detail["hospital_id"] = str(hospital_id)
    return detail


async def _get_or_404(db: AsyncSession, handoff_id: uuid.UUID) -> HospitalHandoff:
    handoff = await db.get(HospitalHandoff, handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="고객 인수 기록을 찾을 수 없습니다.")
    return handoff


async def _active_owner(db: AsyncSession, owner_id: uuid.UUID) -> AdminUser:
    owner = await db.get(AdminUser, owner_id)
    if owner is None or not owner.is_active:
        raise HTTPException(
            status_code=422,
            detail={"code": "ACTIVE_OWNER_REQUIRED", "owner_id": str(owner_id)},
        )
    return owner


async def _commit_transition(db: AsyncSession) -> None:
    try:
        await db.commit()
    except StaleDataError as exc:
        await db.rollback()
        raise stale_handoff_error() from exc


def _assert_version(handoff: HospitalHandoff, version: int) -> None:
    if handoff.version != version:
        raise stale_handoff_error()


async def _sync_active_schedule_plan(
    db: AsyncSession, hospital_id: uuid.UUID, plan: Plan
) -> list[dict[str, str]]:
    """계약 요금제를 활성 발행 일정에 반영하고, 바뀐 일정 기록을 돌려준다.

    월 약정 편수와 격차 배분은 `ContentSchedule.plan`을 읽는다(workers/monthly_slots.py).
    일정 화면은 더 이상 요금제를 바꿀 수 없으므로(H-14), 계약 정정이 일정을 함께 고치지
    않으면 정정된 병원은 옛 편수로 계속 다음 달 슬롯을 만든다. 이미 만들어진 이번 달
    슬롯은 계약 월 보존을 위해 건드리지 않는다 — 재생성은 일정 재설정(교체) 경로가 한다.
    """
    result = await db.execute(
        select(ContentSchedule).where(
            ContentSchedule.hospital_id == hospital_id,
            ContentSchedule.is_active,
        )
    )
    synced: list[dict[str, str]] = []
    for schedule in result.scalars().all():
        if schedule.plan == plan.value:
            continue
        synced.append(
            {"schedule_id": str(schedule.id), "from": schedule.plan, "to": plan.value}
        )
        schedule.plan = plan.value
    return synced


def _payload_from_lookups(
    handoff: HospitalHandoff,
    *,
    hospital: Hospital | None,
    sales: AdminUser | None,
    ae: AdminUser | None,
    accepted: AdminUser | None,
) -> dict[str, object]:
    """Pure projection shared by the single-row and batched payload paths."""
    return {
        **HandoffResponse.model_validate(handoff).model_dump(),
        "hospital_name": hospital.name if hospital else None,
        "sales_owner_name": sales.name if sales else None,
        "ae_owner_name": ae.name if ae else None,
        "accepted_by_name": accepted.name if accepted else None,
        "next_action": {
            HandoffState.CONTRACT_PENDING: "계약 정보 입력",
            HandoffState.CONTRACTED: "AE 고객 인수 승인",
            HandoffState.HANDOFF_ACCEPTED: "병원 기본 정보 입력",
        }[handoff.state],
    }


async def _payload(db: AsyncSession, handoff: HospitalHandoff) -> dict[str, object]:
    hospital = await db.get(Hospital, handoff.hospital_id)
    sales = await db.get(AdminUser, handoff.sales_owner_id) if handoff.sales_owner_id else None
    ae = await db.get(AdminUser, handoff.ae_owner_id) if handoff.ae_owner_id else None
    accepted = await db.get(AdminUser, handoff.accepted_by_id) if handoff.accepted_by_id else None
    return _payload_from_lookups(handoff, hospital=hospital, sales=sales, ae=ae, accepted=accepted)


async def _payloads_batch(
    db: AsyncSession, rows: list[HospitalHandoff]
) -> list[dict[str, object]]:
    """Batch-load hospitals + admin users for a page of handoffs.

    The old per-row path was up to 4 `db.get()` calls per handoff (hospital, sales
    owner, AE owner, accepted-by) — N+1 for the list endpoint. This loads each
    referenced table once with an IN(...) regardless of how many rows are on the page.
    """
    hospital_ids = {row.hospital_id for row in rows if row.hospital_id is not None}
    user_ids = {
        user_id
        for row in rows
        for user_id in (row.sales_owner_id, row.ae_owner_id, row.accepted_by_id)
        if user_id is not None
    }

    hospitals_by_id: dict[uuid.UUID, Hospital] = {}
    if hospital_ids:
        result = await db.execute(select(Hospital).where(Hospital.id.in_(hospital_ids)))
        hospitals_by_id = {h.id: h for h in result.scalars().all()}

    users_by_id: dict[uuid.UUID, AdminUser] = {}
    if user_ids:
        result = await db.execute(select(AdminUser).where(AdminUser.id.in_(user_ids)))
        users_by_id = {u.id: u for u in result.scalars().all()}

    return [
        _payload_from_lookups(
            row,
            hospital=hospitals_by_id.get(row.hospital_id) if row.hospital_id else None,
            sales=users_by_id.get(row.sales_owner_id) if row.sales_owner_id else None,
            ae=users_by_id.get(row.ae_owner_id) if row.ae_owner_id else None,
            accepted=users_by_id.get(row.accepted_by_id) if row.accepted_by_id else None,
        )
        for row in rows
    ]


#: 잘림 여부·다음 페이지 시작점을 알리는 응답 헤더. 응답 본문은 목록 그대로 유지해
#: 기존 호출부(Admin의 `fetchAPI<Handoff[]>`)를 깨지 않는다.
HAS_MORE_HEADER = "X-Has-More"
NEXT_OFFSET_HEADER = "X-Next-Offset"


@router.get("")
async def list_handoffs(
    response: Response,
    state: HandoffState | None = Query(default=None),
    hospital_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
) -> list[dict[str, object]]:
    # 필터 구성과 배치 로딩을 분리해 둔다 — hospital_id 필터가 여기 추가돼도
    # _payloads_batch의 배치 조회 로직은 그대로 재사용된다.
    #
    # limit+1건을 읽어 "더 있다"를 판정한다. 예전에는 limit 기본값 100에서 조용히 잘려
    # 운영자가 목록 끝을 전부라고 믿을 수 있었다.
    stmt = (
        select(HospitalHandoff)
        .order_by(HospitalHandoff.updated_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    if state is not None:
        stmt = stmt.where(HospitalHandoff.state == state)
    if hospital_id is not None:
        stmt = stmt.where(HospitalHandoff.hospital_id == hospital_id)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    response.headers[HAS_MORE_HEADER] = "true" if has_more else "false"
    response.headers[NEXT_OFFSET_HEADER] = str(offset + len(rows))
    return await _payloads_batch(db, rows)


@router.get("/{handoff_id}")
async def get_handoff(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
) -> dict[str, object]:
    return await _payload(db, await _get_or_404(db, handoff_id))


@router.post("/{handoff_id}/contract")
async def contract_handoff(
    handoff_id: uuid.UUID,
    body: HandoffContract,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> dict[str, object]:
    handoff = await _get_or_404(db, handoff_id)
    _assert_version(handoff, body.version)
    if handoff.state is not HandoffState.CONTRACT_PENDING:
        raise HTTPException(status_code=409, detail={"code": "INVALID_HANDOFF_TRANSITION"})
    assigned = actor.id in {handoff.sales_owner_id, handoff.ae_owner_id}
    if actor.role == ROLE_OPERATOR and not assigned:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_NOT_ASSIGNED"})
    if actor.role not in {ROLE_OWNER, ROLE_OPERATOR}:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_ROLE_FORBIDDEN"})
    await _active_owner(db, handoff.sales_owner_id)
    await _active_owner(db, handoff.ae_owner_id)
    handoff.contract_reference = body.contract_reference.strip()
    handoff.contract_effective_at = body.contract_effective_at
    handoff.plan = body.plan
    handoff.sla_due_at = body.sla_due_at
    handoff.state = HandoffState.CONTRACTED
    hospital = await db.get(Hospital, handoff.hospital_id)
    schedule_plan_synced: list[dict[str, str]] = []
    if hospital is not None:
        hospital.plan = body.plan
        # 계약 기록보다 일정이 먼저 만들어진 병원도 같은 트랜잭션에서 맞춘다.
        schedule_plan_synced = await _sync_active_schedule_plan(
            db, handoff.hospital_id, body.plan
        )
    detail: dict[str, object] = {
        "from": "CONTRACT_PENDING",
        "to": "CONTRACTED",
        "version": body.version,
        "owner_override": not assigned,
    }
    if schedule_plan_synced:
        detail["schedule_plan_synced"] = schedule_plan_synced
    await write_audit_log(
        db,
        action="handoff_contracted",
        hospital_id=handoff.hospital_id,
        actor=actor.email,
        target_type="hospital_handoff",
        target_id=handoff.id,
        detail=detail,
    )
    await _commit_transition(db)
    await db.refresh(handoff)
    return await _payload(db, handoff)


@router.post("/{handoff_id}/accept")
async def accept_handoff(
    handoff_id: uuid.UUID,
    body: HandoffAccept,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> dict[str, object]:
    handoff = await _get_or_404(db, handoff_id)
    _assert_version(handoff, body.version)
    if handoff.state is not HandoffState.CONTRACTED:
        raise HTTPException(status_code=409, detail={"code": "INVALID_HANDOFF_TRANSITION"})
    await _active_owner(db, handoff.ae_owner_id)
    accepting_for_other = actor.id != handoff.ae_owner_id
    if actor.role == ROLE_OPERATOR and accepting_for_other:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_NOT_ASSIGNED"})
    if actor.role == ROLE_OWNER and accepting_for_other and not body.reason:
        raise HTTPException(status_code=422, detail={"code": "OWNER_OVERRIDE_REASON_REQUIRED"})
    if actor.role not in {ROLE_OWNER, ROLE_OPERATOR}:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_ROLE_FORBIDDEN"})
    handoff.accepted_by_id = actor.id
    handoff.accepted_at = datetime.now(UTC)
    handoff.state = HandoffState.HANDOFF_ACCEPTED
    await write_audit_log(
        db,
        action="handoff_accepted",
        hospital_id=handoff.hospital_id,
        actor=actor.email,
        target_type="hospital_handoff",
        target_id=handoff.id,
        detail={
            "from": "CONTRACTED",
            "to": "HANDOFF_ACCEPTED",
            "version": body.version,
            "owner_override": accepting_for_other,
            "reason": body.reason,
        },
    )
    await _commit_transition(db)
    await db.refresh(handoff)
    return await _payload(db, handoff)


@router.post("/{handoff_id}/correct-contract")
async def correct_contract(
    handoff_id: uuid.UUID,
    body: HandoffCorrection,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
) -> dict[str, object]:
    if actor.role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_CORRECTION_OWNER_REQUIRED"})
    handoff = await _get_or_404(db, handoff_id)
    _assert_version(handoff, body.version)
    if handoff.state is HandoffState.CONTRACT_PENDING:
        raise HTTPException(status_code=409, detail={"code": "CONTRACT_NOT_RECORDED"})
    before = {
        "contract_reference": handoff.contract_reference,
        "plan": handoff.plan.value if handoff.plan else None,
        "contract_effective_at": handoff.contract_effective_at.isoformat()
        if handoff.contract_effective_at
        else None,
        "sla_due_at": handoff.sla_due_at.isoformat() if handoff.sla_due_at else None,
    }
    handoff.contract_reference = body.contract_reference.strip()
    handoff.contract_effective_at = body.contract_effective_at
    handoff.plan = body.plan
    handoff.sla_due_at = body.sla_due_at
    hospital = await db.get(Hospital, handoff.hospital_id)
    schedule_plan_synced: list[dict[str, str]] = []
    if hospital is not None:
        hospital.plan = body.plan
        schedule_plan_synced = await _sync_active_schedule_plan(
            db, handoff.hospital_id, body.plan
        )
    detail: dict[str, object] = {
        "reason": body.reason,
        "before": before,
        "version": body.version,
    }
    if schedule_plan_synced:
        detail["schedule_plan_synced"] = schedule_plan_synced
    await write_audit_log(
        db,
        action="handoff_contract_corrected",
        hospital_id=handoff.hospital_id,
        actor=actor.email,
        target_type="hospital_handoff",
        target_id=handoff.id,
        detail=detail,
    )
    await _commit_transition(db)
    await db.refresh(handoff)
    return await _payload(db, handoff)


@hospitals_router.post("/register-contract", status_code=status.HTTP_201_CREATED)
async def register_contract(
    body: ContractRegistration,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
) -> dict[str, object]:
    """한 화면 계약 등록 — 병원 생성·계약 기록·인수 수락을 한 트랜잭션에서 끝낸다.

    어느 단계가 실패해도 전부 되돌린다. 병원만 만들어지고 계약이 없는 행은 운영자가
    화면에서 고칠 수 없는 상태이므로, 부분 성공을 남기지 않는 것이 이 라우트의 계약이다.
    """
    # 승인자는 감사 판단의 근거다 — 클라이언트가 보낸 이름이 아니라 확인된 계정만 쓴다.
    audit_actor = verified_request_actor()
    if audit_actor is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "VERIFIED_ACTOR_REQUIRED",
                "message": "운영자 계정을 확인할 수 없습니다. 다시 로그인한 뒤 시도해 주세요.",
            },
        )
    if actor.role not in {ROLE_OWNER, ROLE_OPERATOR}:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_ROLE_FORBIDDEN"})
    # 인수 수락은 담당 AE 본인이 한다. OWNER는 대신 등록할 수 있고 그 사실만 감사에 남긴다
    # (별도 수락 라우트와 달리 여기서는 등록 행위 자체가 OWNER의 것이라 사유 칸이 없다).
    accepting_for_other = actor.id != body.ae_owner_id
    if actor.role == ROLE_OPERATOR and accepting_for_other:
        raise HTTPException(status_code=403, detail={"code": "HANDOFF_NOT_ASSIGNED"})

    sales_owner_id = body.sales_owner_id or actor.id
    await _active_owner(db, body.ae_owner_id)
    await _active_owner(db, sales_owner_id)

    name = body.name.strip()
    duplicates = await find_duplicate_hospitals(db, name=name)
    if duplicates:
        raise HTTPException(status_code=409, detail=_hospital_exists(duplicates[0].id))

    contract_reference = body.contract_reference.strip()
    taken = (
        await db.execute(
            select(HospitalHandoff).where(
                HospitalHandoff.contract_reference == contract_reference
            )
        )
    ).scalars().first()
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONTRACT_REFERENCE_EXISTS",
                "hospital_id": str(taken.hospital_id),
                "message": "이미 사용된 계약 번호입니다. 다른 번호를 입력해 주세요.",
            },
        )

    accepted_at = datetime.now(UTC)
    hospital = Hospital(
        name=name,
        slug=await allocate_hospital_slug(db, name),
        plan=body.plan,
        source_lead_id=body.lead_id,
        onboarding_note="Created from admin contract registration.",
    )
    handoff = HospitalHandoff(
        state=HandoffState.HANDOFF_ACCEPTED,
        acceptance_source=(
            HandoffSource.LEAD_CONVERSION if body.lead_id else HandoffSource.DIRECT_CREATE
        ),
        sales_owner_id=sales_owner_id,
        ae_owner_id=body.ae_owner_id,
        contract_reference=contract_reference,
        contract_effective_at=datetime.combine(body.contract_effective_at, time.min, tzinfo=KST),
        plan=body.plan,
        # 기한과 승인 시각이 같다 — 이 요청에서 담당 AE가 바로 인수했다는 사실 그대로다.
        sla_due_at=accepted_at,
        accepted_by_id=actor.id,
        accepted_at=accepted_at,
    )
    try:
        db.add(hospital)
        await db.flush()
        handoff.hospital_id = hospital.id
        db.add(handoff)
        await db.flush()
        # 감사 3건은 기존 3개 라우트가 남기던 것과 같은 action·detail을 유지한다 —
        # 한 화면으로 합쳤다고 해서 감사 이력의 모양이 달라지면 안 된다.
        await write_audit_log(
            db,
            action="create_hospital",
            hospital_id=hospital.id,
            actor=audit_actor,
            target_type="hospital",
            target_id=hospital.id,
            detail={
                "name": hospital.name,
                "slug": hospital.slug,
                "plan": body.plan.value,
                "source_lead_id": str(body.lead_id) if body.lead_id else None,
            },
        )
        await write_audit_log(
            db,
            action="handoff_contracted",
            hospital_id=hospital.id,
            actor=audit_actor,
            target_type="hospital_handoff",
            target_id=handoff.id,
            detail={
                "from": "CONTRACT_PENDING",
                "to": "CONTRACTED",
                "version": handoff.version,
                "owner_override": actor.id not in {sales_owner_id, body.ae_owner_id},
            },
        )
        await write_audit_log(
            db,
            action="handoff_accepted",
            hospital_id=hospital.id,
            actor=audit_actor,
            target_type="hospital_handoff",
            target_id=handoff.id,
            detail={
                "from": "CONTRACTED",
                "to": "HANDOFF_ACCEPTED",
                "version": handoff.version,
                "owner_override": accepting_for_other,
                "reason": None,
            },
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raced = await find_duplicate_hospitals(db, name=name)
        raise HTTPException(
            status_code=409,
            detail=_hospital_exists(raced[0].id if raced else None),
        ) from exc
    except Exception:
        # 계약 기록이나 인수 수락이 실패하면 병원도 남기지 않는다.
        await db.rollback()
        raise
    await db.refresh(hospital)
    await db.refresh(handoff)
    return {
        **serialize_hospital_detail(hospital),
        "handoff": serialize_handoff_summary(handoff),
    }
