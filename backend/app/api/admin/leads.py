"""Admin API — sales lead intake review."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from slugify import slugify
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital, Plan
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    ReportStatus,
)
from app.services import lead_delivery
from app.services.audit_log import default_actor, write_audit_log
from app.services.lead_privacy import purge_lead_completely_async, scrub_onboarding_note

router = APIRouter(prefix="/admin/leads", tags=["Admin — Leads"])


class LeadConvertRequest(BaseModel):
    hospital_id: uuid.UUID | None = None
    hospital_name: str | None = Field(default=None, max_length=200)
    plan: Plan = Plan.PLAN_12
    conversion_note: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_sales_leads(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    needs_attention: bool = Query(
        default=False,
        description="측정 FAILED · 리포트 BLOCKED · 발송 FAILED 중 하나인 진단을 가진 리드만",
    ),
):
    # offset 없이는 limit 상한(200) 이전의 오래된 리드에 UI가 닿을 수 없다 —
    # 개인정보 파기 요청 처리 대상은 주로 오래된 리드라 도달 가능성이 필수.
    stmt = select(SalesLead).order_by(SalesLead.created_at.desc())
    # `is True`인 이유: 기본값이 `Query(default=False)` 객체이므로, FastAPI를 거치지 않고
    # 이 함수를 직접 부르면(테스트가 그렇게 한다) 기본값이 truthy가 되어 **항상 필터가
    # 걸린다**. 그러면 목록이 조용히 비어 보인다.
    if needs_attention is True:
        # 실패는 Slack으로만 알리면 놓친 뒤에 찾을 방법이 없다 — 목록에서 되짚을 수 있어야 한다.
        stmt = stmt.where(
            select(LeadDiagnosis.id)
            .where(
                LeadDiagnosis.lead_id == SalesLead.id,
                _needs_attention_clause(),
            )
            .exists()
        )
    result = await db.execute(stmt.offset(offset).limit(limit))
    leads = list(result.scalars().all())
    diagnoses_by_lead = await _diagnoses_by_lead(db, [lead.id for lead in leads])
    # 한 번에 최대 200건의 이름·연락처·문의내용을 반환하는 대량 PII 열람이다. 누가 언제
    # 얼마나 조회했는지 남지 않으면 admin_audit_logs가 컴플라이언스 산출물로 성립하지 않는다.
    # 감사 로그 자체가 PII 사본이 되면 안 되므로 건수·페이지 메타만 남긴다.
    await write_audit_log(
        db,
        action="list_sales_leads",
        actor=default_actor(),
        target_type="sales_lead",
        detail={"returned_count": len(leads), "limit": limit, "offset": offset},
    )
    await db.commit()
    return [
        {
            **_serialize_lead(lead),
            "diagnoses": [
                _serialize_diagnosis(d) for d in diagnoses_by_lead.get(lead.id, [])
            ],
        }
        for lead in leads
    ]


@router.get("/{lead_id}/hospital-candidates")
async def list_hospital_candidates(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    candidates = await _find_duplicate_hospitals(db, lead)
    # 이 응답은 lead 한 건의 연락처·문의 원문을 그대로 담는다 — 목록 조회와 동일하게
    # 열람 사실을 남기되, 내용은 남기지 않는다.
    await write_audit_log(
        db,
        action="view_lead_hospital_candidates",
        actor=default_actor(),
        target_type="sales_lead",
        target_id=str(lead.id),
        detail={"candidate_count": len(candidates)},
    )
    await db.commit()
    return {
        "lead_id": str(lead.id),
        # Admin fetches this authenticated payload by identifier so onboarding
        # URLs never need to duplicate contact/question/source PII.
        "lead": _serialize_lead(lead),
        "candidates": [_serialize_hospital(candidate) for candidate in candidates],
    }


@router.post("/{lead_id}/convert", status_code=status.HTTP_201_CREATED)
async def convert_sales_lead(
    lead_id: uuid.UUID,
    body: LeadConvertRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    if lead.converted_hospital_id:
        hospital = await db.get(Hospital, lead.converted_hospital_id)
        # 이미 전환된 리드도 응답에 PII가 실려 나가므로 재열람 사실을 남긴다.
        await write_audit_log(
            db,
            action="convert_sales_lead",
            hospital_id=lead.converted_hospital_id,
            actor=default_actor(),
            target_type="sales_lead",
            target_id=str(lead.id),
            detail={"already_converted": True},
        )
        await db.commit()
        return {
            "lead": _serialize_lead(lead),
            "hospital": _serialize_hospital(hospital) if hospital else None,
            "onboarding_url": f"/hospitals/{hospital.id}/onboarding" if hospital else None,
        }

    request_body = body or LeadConvertRequest()
    hospital = None
    if request_body.hospital_id:
        hospital = await db.get(Hospital, request_body.hospital_id)
        if hospital is None:
            raise HTTPException(status_code=404, detail="Hospital not found")
        _merge_onboarding_note(hospital, lead, request_body.conversion_note)
        if hospital.source_lead_id is None:
            hospital.source_lead_id = lead.id
    else:
        hospital_name = request_body.hospital_name or lead.clinic_name
        slug = await _unique_hospital_slug(db, hospital_name)
        hospital = Hospital(
            name=hospital_name,
            slug=slug,
            plan=request_body.plan,
            # PII-2: lead.contact(개인 식별 가능)을 공개 hospital.phone로 복사하지 않는다 —
            # 복사 시 보유기간 파기/정보주체 파기를 우회해 공개 /site에 잔존한다. 병원 공식
            # 전화번호는 AE가 프로파일 단계에서 검증해 직접 입력한다.
            source_lead_id=lead.id,
            onboarding_note=_build_onboarding_note(lead, request_body.conversion_note),
            specialties=[lead.clinic_type] if lead.clinic_type else [],
        )
        db.add(hospital)
        await db.flush()

    lead.status = "CONVERTED"
    lead.converted_hospital_id = hospital.id
    lead.converted_at = datetime.now(timezone.utc)
    lead.conversion_note = request_body.conversion_note or _build_onboarding_note(lead, None)

    # 전환은 리드 PII를 병원 레코드(=보유기간이 다른 라이프사이클)로 옮기는 지점이라
    # 파기 요청 추적의 시작점이 된다. 대상 id만 남기고 이름·연락처는 남기지 않는다.
    await write_audit_log(
        db,
        action="convert_sales_lead",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="sales_lead",
        target_id=str(lead.id),
        detail={
            "hospital_id": str(hospital.id),
            "linked_existing_hospital": request_body.hospital_id is not None,
            "plan": request_body.plan.value if request_body.plan else None,
        },
    )
    await db.commit()
    await db.refresh(lead)
    await db.refresh(hospital)
    return {
        "lead": _serialize_lead(lead),
        "hospital": _serialize_hospital(hospital),
        "onboarding_url": f"/hospitals/{hospital.id}/onboarding",
    }


@router.post("/{lead_id}/erase")
async def erase_lead_pii(lead_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """정보주체 파기 요청 즉시 이행 (PII-2 / 처리방침 '즉시 파기' 약속).

    보유기간 만료를 기다리지 않고 개인 식별 필드를 즉시 익명화한다. 통계용 메타는 유지.
    """
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    # 진단 산출물(리포트 PDF·열람 토큰·AI 답변 원문)까지 함께 파기한다.
    # 이것을 빼면 purged_at만 찍히고 PDF와 활성 토큰이 남는데, 이후 보관기간
    # 배치는 purged_at IS NULL만 조회하므로 그 리드를 영원히 건너뛴다.
    _now = datetime.now(timezone.utc)
    changed = (await purge_lead_completely_async(db, lead, _now))["anonymized"]

    # CDX-M2: 전환 시 hospital.onboarding_note로 복사된 운영자 자유 텍스트도 함께 파기 —
    # lead row만 익명화하면 노트가 파기 라이프사이클을 우회한다.
    # lead가 이미 파기됐어도(changed=False) 노트는 별개로 남아 있을 수 있다 (R6):
    # 보관기간 cron이 노트 scrub 도입 전에 lead만 파기한 경우, 명시적 파기 요청에서
    # 노트가 영구 잔존하면 안 된다. converted_hospital_id가 있으면 항상 scrub한다.
    note_scrubbed = False
    if lead.converted_hospital_id:
        hospital = await db.get(Hospital, lead.converted_hospital_id)
        if hospital and hospital.onboarding_note:
            scrubbed_note = scrub_onboarding_note(hospital.onboarding_note, lead.id)
            if scrubbed_note != hospital.onboarding_note:
                hospital.onboarding_note = scrubbed_note
                note_scrubbed = True

    if changed or note_scrubbed:
        await write_audit_log(
            db,
            action="erase_lead_pii",
            hospital_id=lead.converted_hospital_id,
            actor=default_actor(),
            target_type="sales_lead",
            target_id=str(lead.id),
            detail={
                "reason": "data subject erasure request",
                "lead_already_purged": not changed,
                "onboarding_note_scrubbed": note_scrubbed,
            },
        )
        await db.commit()
    return {
        "detail": "erased" if (changed or note_scrubbed) else "already_purged",
        "lead_id": str(lead.id),
    }


def _needs_attention_clause():
    """AE가 손을 써야 하는 진단의 정의 — 세 축 중 하나라도 종결 실패인 경우."""
    return or_(
        LeadDiagnosis.execution_status == ExecutionStatus.FAILED.value,
        LeadDiagnosis.report_status == ReportStatus.BLOCKED.value,
        LeadDiagnosis.delivery_status == DeliveryStatus.FAILED.value,
    )


async def _diagnoses_by_lead(db: AsyncSession, lead_ids: list[uuid.UUID]) -> dict:
    """리드 목록의 진단을 한 번에 읽는다 (N+1 방지)."""
    if not lead_ids:
        return {}
    rows = (
        await db.execute(
            select(LeadDiagnosis)
            .where(LeadDiagnosis.lead_id.in_(lead_ids))
            .order_by(LeadDiagnosis.created_at.desc())
        )
    ).scalars().all()
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row.lead_id, []).append(row)
    return grouped


def _serialize_diagnosis(diagnosis: LeadDiagnosis) -> dict:
    """3축 상태를 그대로 노출한다.

    단일 status로 접어서 보여주면 "측정은 일부 실패했지만 리포트는 나갔다" 같은 상태가
    사라진다 — 그 구분이 AE가 원장에게 무엇을 말할지 결정하는 정보다(설계 §4).

    **병원명·지역은 담지 않는다.** 이 목록은 이미 대량 PII 열람이라 감사 로그를 남기는
    표면이고, 진단 요약에 식별정보를 더 얹을 이유가 없다 — 리드 행에 이미 있다.
    """
    return {
        "id": str(diagnosis.id),
        "execution_status": diagnosis.execution_status,
        "execution_attempts": diagnosis.execution_attempts,
        "report_status": diagnosis.report_status,
        "report_attempts": diagnosis.report_attempts,
        "delivery_status": diagnosis.delivery_status,
        "slot_date": diagnosis.slot_date.isoformat() if diagnosis.slot_date else None,
        "slot_no": diagnosis.slot_no,
        "lock_released_at": diagnosis.lock_released_at.isoformat()
        if diagnosis.lock_released_at
        else None,
        "lock_released_by": diagnosis.lock_released_by,
        "needs_attention": (
            diagnosis.execution_status == ExecutionStatus.FAILED.value
            or diagnosis.report_status == ReportStatus.BLOCKED.value
            or diagnosis.delivery_status == DeliveryStatus.FAILED.value
        ),
        "error": diagnosis.error,
        "created_at": diagnosis.created_at.isoformat() if diagnosis.created_at else None,
    }


def _serialize_lead(lead: SalesLead) -> dict:
    return {
        "id": str(lead.id),
        "clinic_name": lead.clinic_name,
        "clinic_type": lead.clinic_type,
        "contact": lead.contact,
        "question": lead.question,
        "privacy": lead.privacy,
        "source_path": lead.source_path,
        "status": lead.status,
        "converted_hospital_id": str(lead.converted_hospital_id)
        if lead.converted_hospital_id
        else None,
        "converted_at": lead.converted_at.isoformat() if lead.converted_at else None,
        "conversion_note": lead.conversion_note,
        "notification_status": getattr(lead, "notification_status", None),
        "notification_error": getattr(lead, "notification_error", None),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }


def _serialize_hospital(hospital: Hospital | None) -> dict | None:
    if hospital is None:
        return None
    return {
        "id": str(hospital.id),
        "name": hospital.name,
        "slug": hospital.slug,
        "status": hospital.status.value if hospital.status else None,
        "plan": hospital.plan.value if hospital.plan else None,
        "source_lead_id": str(hospital.source_lead_id) if hospital.source_lead_id else None,
        "onboarding_url": f"/hospitals/{hospital.id}/onboarding",
    }


async def _find_duplicate_hospitals(db: AsyncSession, lead: SalesLead) -> list[Hospital]:
    filters = [func.lower(Hospital.name) == lead.clinic_name.lower()]
    phone = _phone_contact_or_none(lead.contact)
    if phone:
        filters.append(Hospital.phone == phone)

    result = await db.execute(
        select(Hospital).where(or_(*filters)).order_by(Hospital.created_at.desc()).limit(10)
    )
    return list(result.scalars().all())


async def _unique_hospital_slug(db: AsyncSession, name: str) -> str:
    slug = slugify(name, separator="-") or f"hospital-{uuid.uuid4().hex[:8]}"
    existing = await db.execute(select(Hospital).where(Hospital.slug == slug))
    if existing.scalar_one_or_none():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    return slug


def _phone_contact_or_none(contact: str | None) -> str | None:
    if not contact:
        return None
    digits = "".join(ch for ch in contact if ch.isdigit())
    return contact if len(digits) >= 7 and "@" not in contact else None


def _build_onboarding_note(lead: SalesLead, operator_note: str | None) -> str:
    # PII-3: 연락처/문의 원문은 onboarding_note(병원 레코드)나 conversion_note에 영구 저장하지
    # 않는다 — 보유기간 자동 파기를 우회하기 때문. 원문은 보유기간이 관리되는 lead row에서만 확인.
    lines = [
        f"Source lead: {lead.id}",
        f"Clinic type / region: {lead.clinic_type}",
    ]
    if lead.source_path:
        lines.append(f"Source path: {lead.source_path}")
    if lead.consent_version:
        lines.append(f"Consent version: {lead.consent_version}")
    if operator_note:
        lines.append(f"Operator note: {operator_note}")
    return "\n".join(lines)


def _merge_onboarding_note(hospital: Hospital, lead: SalesLead, operator_note: str | None) -> None:
    lead_note = _build_onboarding_note(lead, operator_note)
    if hospital.onboarding_note:
        hospital.onboarding_note = f"{hospital.onboarding_note}\n\n{lead_note}"
    else:
        hospital.onboarding_note = lead_note


class RetryDeliveryRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)
    # 24시간이 지나 멱등성 키가 잊힌 경우에만 요구된다. 기본 False —
    # 중복 발송 위험을 기본값으로 감수하게 만들면 안 된다.
    acknowledge_duplicate_risk: bool = False


@router.post("/{lead_id}/retry-report-delivery")
async def retry_report_delivery(
    lead_id: uuid.UUID,
    request_body: RetryDeliveryRequest,
    db: AsyncSession = Depends(get_db),
):
    """FAILED로 끝난 리포트 메일을 수동으로 재발송한다.

    **이 엔드포인트가 없으면 발송 실패는 리드 영구 소실이다.** 자동 재시도 4회가 소진되면
    `delivery_status=FAILED`가 종결이고, 리포트는 만들어져 있는데 보낼 방법이 없다.
    게다가 전화번호·이메일이 영구 잠금이라(F1-6) 신청자가 다시 신청할 수도 없다.

    실제 상태 판정과 중복 발송 방지는 `lead_delivery.rearm_report_delivery`에 있다 —
    멱등성 키 수명이라는 근거가 그쪽 도메인이기 때문이다.
    """
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    diagnoses = (
        await db.execute(
            select(LeadDiagnosis)
            .where(LeadDiagnosis.lead_id == lead_id)
            .order_by(LeadDiagnosis.created_at.desc())
        )
    ).scalars().all()
    if not diagnoses:
        raise HTTPException(status_code=409, detail="이 리드에는 진단이 없습니다.")

    actor = default_actor()
    outcomes = []
    rearmed = []
    for diagnosis in diagnoses:
        outcome = await lead_delivery.rearm_report_delivery(
            db,
            diagnosis,
            actor=actor,
            reason=request_body.reason,
            acknowledge_duplicate_risk=request_body.acknowledge_duplicate_risk,
        )
        outcomes.append({"diagnosis_id": str(diagnosis.id), **outcome})
        if outcome.get("ok"):
            rearmed.append(diagnosis)

    if not rearmed:
        # 되살릴 게 하나도 없으면 왜 안 되는지를 그대로 돌려준다 — 409 본문이
        # 운영자가 보는 유일한 설명이다.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": "재발송할 수 있는 발송 건이 없습니다.", "diagnoses": outcomes},
        )

    await write_audit_log(
        db,
        action="retry_lead_report_delivery",
        hospital_id=lead.converted_hospital_id,
        actor=actor,
        target_type="sales_lead",
        target_id=str(lead.id),
        detail={
            "rearmed_count": len(rearmed),
            "reason": request_body.reason.strip()[:200],
            "acknowledged_duplicate_risk": request_body.acknowledge_duplicate_risk,
            "diagnosis_ids": [str(d.id) for d in rearmed],
        },
    )
    await db.commit()

    return {"detail": "rearmed", "rearmed_count": len(rearmed), "diagnoses": outcomes}


class ReleaseLockRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


@router.post("/{lead_id}/release-lock")
async def release_diagnosis_lock(
    lead_id: uuid.UUID,
    request_body: ReleaseLockRequest,
    db: AsyncSession = Depends(get_db),
):
    """무료 진단 1회 제한 해제 (PRD F1-7).

    **이 엔드포인트가 없으면 F1-6(전화번호+이메일 영구 잠금)은 리드 차단 장치가 된다.**
    공격자나 대행사가 공개된 병원 대표번호로 먼저 신청하면, 정작 원장은 영구히 거절된다.
    그 상황을 푸는 **유일한** 경로다.

    사유를 필수로 받고 감사 로그를 남긴다 — 잠금 해제는 그 병원이 무료 진단을 한 번 더
    받는다는 뜻이므로 누가 왜 풀었는지가 남아야 한다.
    """
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    now = datetime.now(timezone.utc)
    actor = default_actor()
    diagnoses = (
        await db.execute(
            select(LeadDiagnosis).where(
                LeadDiagnosis.lead_id == lead_id,
                LeadDiagnosis.lock_released_at.is_(None),
            )
        )
    ).scalars().all()

    if not diagnoses:
        # 이미 풀렸거나 진단이 없다. 404가 아니라 명시적 안내 — 운영자가 "무엇이
        # 잘못됐는지" 알 수 있어야 한다.
        raise HTTPException(status_code=409, detail="해제할 잠금이 없습니다.")

    for diagnosis in diagnoses:
        diagnosis.lock_released_at = now
        diagnosis.lock_released_by = actor
        diagnosis.lock_release_reason = request_body.reason.strip()[:200]

    await write_audit_log(
        db,
        action="release_lead_diagnosis_lock",
        hospital_id=lead.converted_hospital_id,
        actor=actor,
        target_type="sales_lead",
        target_id=str(lead.id),
        detail={
            "released_count": len(diagnoses),
            "reason": request_body.reason.strip()[:200],
        },
    )
    await db.commit()

    return {
        "detail": "released",
        "released_count": len(diagnoses),
        "released_by": actor,
        "released_at": now.isoformat(),
    }
