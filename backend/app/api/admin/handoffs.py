"""Explicit, audited customer handoff transitions."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
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


async def _payload(db: AsyncSession, handoff: HospitalHandoff) -> dict[str, object]:
    hospital = await db.get(Hospital, handoff.hospital_id)
    sales = await db.get(AdminUser, handoff.sales_owner_id) if handoff.sales_owner_id else None
    ae = await db.get(AdminUser, handoff.ae_owner_id) if handoff.ae_owner_id else None
    accepted = await db.get(AdminUser, handoff.accepted_by_id) if handoff.accepted_by_id else None
    return {
        **HandoffResponse.model_validate(handoff).model_dump(),
        "hospital_name": hospital.name if hospital else None,
        "sales_owner_name": sales.name if sales else None,
        "ae_owner_name": ae.name if ae else None,
        "accepted_by_name": accepted.name if accepted else None,
        "next_action": {
            HandoffState.CONTRACT_PENDING: "계약 정보 입력",
            HandoffState.CONTRACTED: "AE 고객 인수 승인",
            HandoffState.HANDOFF_ACCEPTED: "병원 프로파일 입력",
        }[handoff.state],
    }


@router.get("")
async def list_handoffs(
    state: HandoffState | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
) -> list[dict[str, object]]:
    stmt = select(HospitalHandoff).order_by(HospitalHandoff.updated_at.desc())
    if state is not None:
        stmt = stmt.where(HospitalHandoff.state == state)
    rows = list((await db.execute(stmt)).scalars().all())
    return [await _payload(db, row) for row in rows]


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
