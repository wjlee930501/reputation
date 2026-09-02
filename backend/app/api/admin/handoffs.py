"""Explicit, audited customer handoff transitions."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.admin.accounts import require_active_account, require_owner_account
from app.core.database import get_db
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital, Plan
from app.schemas.handoff import HandoffAccept, HandoffContract, HandoffResponse
from app.services.audit_log import write_audit_log

router = APIRouter(prefix="/admin/handoffs", tags=["Admin — Handoffs"])


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
    if hospital is not None:
        hospital.plan = body.plan
    await write_audit_log(
        db,
        action="handoff_contracted",
        hospital_id=handoff.hospital_id,
        actor=actor.email,
        target_type="hospital_handoff",
        target_id=handoff.id,
        detail={
            "from": "CONTRACT_PENDING",
            "to": "CONTRACTED",
            "version": body.version,
            "owner_override": not assigned,
        },
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
    if hospital is not None:
        hospital.plan = body.plan
    await write_audit_log(
        db,
        action="handoff_contract_corrected",
        hospital_id=handoff.hospital_id,
        actor=actor.email,
        target_type="hospital_handoff",
        target_id=handoff.id,
        detail={"reason": body.reason, "before": before, "version": body.version},
    )
    await _commit_transition(db)
    await db.refresh(handoff)
    return await _payload(db, handoff)
