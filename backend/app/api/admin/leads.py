"""Admin API — sales lead intake review."""
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from slugify import slugify
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital, Plan
from app.models.lead import SalesLead
from app.services.audit_log import default_actor, write_audit_log
from app.services.lead_privacy import anonymize_lead, scrub_onboarding_note

router = APIRouter(prefix="/admin/leads", tags=["Admin — Leads"])


class LeadConvertRequest(BaseModel):
    hospital_id: uuid.UUID | None = None
    hospital_name: str | None = Field(default=None, max_length=200)
    plan: Plan = Plan.PLAN_8
    conversion_note: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_sales_leads(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    result = await db.execute(
        select(SalesLead).order_by(SalesLead.created_at.desc()).limit(limit)
    )
    return [_serialize_lead(lead) for lead in result.scalars().all()]


@router.get("/{lead_id}/hospital-candidates")
async def list_hospital_candidates(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(SalesLead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    candidates = await _find_duplicate_hospitals(db, lead)
    return {
        "lead_id": str(lead.id),
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

    changed = anonymize_lead(lead, datetime.now(timezone.utc))
    if changed:
        # CDX-M2: 전환 시 hospital.onboarding_note로 복사된 운영자 자유 텍스트도 함께 파기 —
        # lead row만 익명화하면 노트가 파기 라이프사이클을 우회한다.
        if lead.converted_hospital_id:
            hospital = await db.get(Hospital, lead.converted_hospital_id)
            if hospital and hospital.onboarding_note:
                hospital.onboarding_note = scrub_onboarding_note(
                    hospital.onboarding_note, lead.id
                )
        await write_audit_log(
            db,
            action="erase_lead_pii",
            hospital_id=lead.converted_hospital_id,
            actor=default_actor(),
            target_type="sales_lead",
            target_id=str(lead.id),
            detail={"reason": "data subject erasure request"},
        )
        await db.commit()
    return {"detail": "erased" if changed else "already_purged", "lead_id": str(lead.id)}


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
        "converted_hospital_id": str(lead.converted_hospital_id) if lead.converted_hospital_id else None,
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
        select(Hospital)
        .where(or_(*filters))
        .order_by(Hospital.created_at.desc())
        .limit(10)
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
