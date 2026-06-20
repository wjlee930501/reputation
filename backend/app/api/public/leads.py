"""Public API — sales lead capture."""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip, limiter
from app.models.lead import SalesLead
from app.services import notifier

router = APIRouter(prefix="/public/leads", tags=["Public — Leads"])

_PHONE_PATTERN = re.compile(r"\d[\d\-\s]{6,}")
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_HONEYPOT_FIELDS = {"website", "url"}
_RESIDENT_REGISTRATION_NUMBER = re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b")
_PATIENT_RECORD_CONTEXT = re.compile(
    r"(수술\s*기록|진료\s*기록|진료\s*내역|의무\s*기록|검사\s*결과|처방\s*내역|처방전|차트)"
)
_PATIENT_RECORD_WITH_PERSON_CONTEXT = re.compile(
    r"(환자|보호자).{0,30}"
    r"(주민등록|진료\s*기록|진료\s*내역|수술\s*기록|의무\s*기록|검사\s*결과|처방\s*내역|처방전|차트)"
)
_NATIONAL_ID_CONTEXT = re.compile(r"주민등록(?:번호)?")
_PERSONAL_IDENTIFIER_CONTEXT = re.compile(r"(연락처|전화번호|휴대폰|생년월일|환자\s*번호)")
_FOUR_OR_MORE_DIGITS = re.compile(r"\d{4,}")


def contains_patient_sensitive_text(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized:
        return False
    return (
        _RESIDENT_REGISTRATION_NUMBER.search(normalized) is not None
        or _PATIENT_RECORD_CONTEXT.search(normalized) is not None
        or _PATIENT_RECORD_WITH_PERSON_CONTEXT.search(normalized) is not None
        or _NATIONAL_ID_CONTEXT.search(normalized) is not None
        or (
            _PERSONAL_IDENTIFIER_CONTEXT.search(normalized) is not None
            and _FOUR_OR_MORE_DIGITS.search(normalized) is not None
        )
    )


class LeadCreate(BaseModel):
    clinic_name: str = Field(min_length=1, max_length=200)
    clinic_type: str = Field(min_length=1, max_length=200)
    contact: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=1000)
    privacy: bool
    consent_version: str | None = Field(default=None, max_length=40)
    source_path: str | None = Field(default=None, max_length=500)
    # Honeypot — silently dropped if filled.
    website: str | None = Field(default=None, max_length=500)

    @field_validator("clinic_name", "clinic_type", "contact", "question", "source_path")
    @classmethod
    def clean_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Must not be blank")
        return cleaned

    @field_validator("contact")
    @classmethod
    def validate_contact_format(cls, value: str) -> str:
        if not (_PHONE_PATTERN.search(value) or _EMAIL_PATTERN.search(value)):
            raise ValueError("이메일 또는 전화번호 형식으로 입력해 주세요.")
        return value

    @field_validator("question")
    @classmethod
    def reject_patient_sensitive_question(cls, value: str) -> str:
        if contains_patient_sensitive_text(value):
            raise ValueError("환자 개인정보나 진료기록은 이 문의 양식에 입력하지 마세요.")
        return value


@router.post("")
@limiter.limit(settings.PUBLIC_LEAD_RATE_LIMIT)
async def create_lead(
    request: Request,
    body: LeadCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create one sales lead.

    Rate-limit: applied via slowapi `Limiter.limit` decorator on `app.main`
    (registered there so the limiter has access to `app.state.limiter`).
    Honeypot: silently 200 if `website` is filled.
    """
    # Honeypot — bot가 채워주는 hidden field. 정상 사용자는 비워둠.
    if body.website:
        return {"ok": True, "lead_id": None, "created_at": None}

    if not body.privacy:
        raise HTTPException(status_code=400, detail="privacy consent is required")

    consent_ip = get_request_ip(request)
    # consent_version은 클라이언트 입력을 신뢰하지 않고 항상 서버 ENV에서 가져온다.
    # 처리방침이 갱신되면 서버 배포 시점에 ENV가 바뀌어 추적 무결성이 보장된다.
    consent_version = settings.LEAD_CONSENT_VERSION.strip()[:40]
    retain_until = datetime.now(timezone.utc) + timedelta(days=settings.LEAD_RETENTION_DAYS)

    lead = SalesLead(
        clinic_name=body.clinic_name,
        clinic_type=body.clinic_type,
        contact=body.contact,
        question=body.question,
        privacy=True,
        source_path=body.source_path,
        consent_ip=consent_ip,
        consent_version=consent_version,
        retain_until=retain_until,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    admin_url = f"{settings.ADMIN_BASE_URL.rstrip('/')}/leads"
    notified = await notifier.notify_lead_created(
        clinic_name=body.clinic_name,
        clinic_type=body.clinic_type,
        contact=body.contact,
        admin_url=admin_url,
    )
    lead.notification_status = "SENT" if notified else "FAILED"
    lead.notification_error = None if notified else "Slack/webhook delivery failed or is not configured."
    await db.commit()

    return {
        "ok": True,
        "lead_id": str(lead.id),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }
