"""Public API — sales lead capture."""
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip, limiter
from app.models.lead import SalesLead
from app.services import inquiry_diagnosis, inquiry_sms, notifier

logger = logging.getLogger(__name__)

# Slack 한 줄로 나가는 자동 처리 결과. 고정 문구만 쓴다 — 사용자 입력이 섞이면 안 된다.
DIAGNOSIS_NOTE_QUEUED = "초도 노출 진단 자동 시작"
DIAGNOSIS_NOTE_NO_INPUT = "진료과·지역·키워드 미입력 — Admin에서 초도 진단 생성"
DIAGNOSIS_NOTE_REFUSED = "초도 노출 진단 자동 생성 거절 — Admin에서 입력값 확인 후 생성"

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
    # ── 초도 노출 진단 입력(선택). 셋이 모두 오면 접수 직후 INTERNAL 진단을 자동으로 만든다.
    # clinic_type은 도입문의 표식이 차지하므로 진료과는 별도 필드로 받는다.
    specialty: str | None = Field(default=None, max_length=100)
    region_keyword: str | None = Field(default=None, max_length=100)
    # 원시 길이는 넉넉히 받고 정리 뒤 4개로 자른다 — 키워드가 많다고 리드를 거절하지 않는다.
    core_keywords: list[str] | None = Field(default=None, max_length=12)
    contact_name: str | None = Field(default=None, max_length=100)
    # Honeypot — silently dropped if filled. 필드명은 _HONEYPOT_FIELDS와 일치해야 한다.
    website: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=500)

    @field_validator(
        "clinic_name", "clinic_type", "contact", "question", "source_path",
        "specialty", "region_keyword", "contact_name",
    )
    @classmethod
    def clean_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Must not be blank")
        return cleaned

    @field_validator("core_keywords")
    @classmethod
    def clean_keywords(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value and value.strip()]
        unique = list(dict.fromkeys(cleaned))
        if not unique:
            return None
        if any(len(value) > 50 for value in unique):
            raise ValueError("핵심 키워드는 50자 이내로 입력해 주세요.")
        return unique[:4]

    def diagnosis_input(self) -> "inquiry_diagnosis.InquiryDiagnosisInput | None":
        """셋이 모두 있어야 질의를 만들 수 있다. 하나라도 비면 Admin 수동 생성으로 남긴다."""
        if not (self.specialty and self.region_keyword and self.core_keywords):
            return None
        return inquiry_diagnosis.InquiryDiagnosisInput(
            specialty=self.specialty,
            region_keyword=self.region_keyword,
            core_keywords=list(self.core_keywords),
            contact_name=self.contact_name,
        )

    @field_validator("contact")
    @classmethod
    def validate_contact_format(cls, value: str) -> str:
        if not (_PHONE_PATTERN.search(value) or _EMAIL_PATTERN.search(value)):
            raise ValueError("이메일 또는 전화번호 형식으로 입력해 주세요.")
        return value

    # 자유 텍스트는 question만이 아니다 — clinic_name/clinic_type도 사용자가 임의로 채우는
    # 필드이고, 접수 즉시 Slack(국외 이전)으로 나가고 Admin 목록에 그대로 노출된다.
    # question에만 검증이 걸려 있으면 "홍길동 환자 900101-1234567"을 병원명 칸에 넣는 것만으로
    # 민감정보가 평문 유출되므로, 공개 폼의 모든 자유 텍스트 필드에 동일 검증을 적용한다.
    @field_validator(
        "clinic_name", "clinic_type", "question", "specialty", "region_keyword", "contact_name"
    )
    @classmethod
    def reject_patient_sensitive_free_text(cls, value: str | None) -> str | None:
        if value is not None and contains_patient_sensitive_text(value):
            raise ValueError("환자 개인정보나 진료기록은 이 문의 양식에 입력하지 마세요.")
        return value

    @field_validator("core_keywords")
    @classmethod
    def reject_patient_sensitive_keywords(cls, values: list[str] | None) -> list[str] | None:
        if values and any(contains_patient_sensitive_text(value) for value in values):
            raise ValueError("환자 개인정보나 진료기록은 이 문의 양식에 입력하지 마세요.")
        return values


@router.post("")
@limiter.limit(settings.PUBLIC_LEAD_RATE_LIMIT)
async def create_lead(
    request: Request,
    body: LeadCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create one sales lead.

    Rate-limit: applied via slowapi `Limiter.limit` decorator on `app.main`
    (registered there so the limiter has access to `app.state.limiter`).
    Honeypot: silently 200 if `website` is filled.
    """
    # Honeypot — bot가 채워주는 hidden field. 정상 사용자는 비워둠.
    # 어느 honeypot 필드든 채워져 있으면 silent 200으로 응답하고 DB에 저장하지 않는다.
    if any((getattr(body, field, None) or "").strip() for field in _HONEYPOT_FIELDS):
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
        specialty=body.specialty,
        region_keyword=body.region_keyword,
        core_keywords=body.core_keywords,
        contact_name=body.contact_name,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    # ── 초도 노출 진단 자동 생성. 리드는 이미 저장됐다 — 여기서 거절돼도 접수는 성공이고
    #    AE가 Admin에서 같은 규칙으로 만든다. 서비스는 검증을 mutate 전에 끝내므로 예외
    #    뒤 세션에 남는 변경이 없다.
    diagnosis_id: str | None = None
    spec = body.diagnosis_input()
    if spec is None:
        diagnosis_note = DIAGNOSIS_NOTE_NO_INPUT
    else:
        try:
            diagnosis = await inquiry_diagnosis.create_inquiry_diagnosis(
                db, lead, spec, actor=inquiry_diagnosis.PUBLIC_INTAKE_ACTOR
            )
            await db.commit()
            diagnosis_id = str(diagnosis.id)
            # 응답 뒤에 큐잉한다 — 브로커 접속 지연이 원장의 접수 응답을 붙잡지 않는다.
            # 커밋된 PENDING 행은 매분 drain이 회수하므로 큐잉 실패도 유실이 아니다.
            background_tasks.add_task(inquiry_diagnosis.enqueue_inquiry_diagnosis, diagnosis_id)
            diagnosis_note = DIAGNOSIS_NOTE_QUEUED
        except inquiry_diagnosis.InquiryDiagnosisError as exc:
            logger.warning(
                "inquiry diagnosis auto-creation refused for lead %s: %s", lead.id, exc
            )
            diagnosis_note = DIAGNOSIS_NOTE_REFUSED

    admin_url = f"{settings.ADMIN_BASE_URL.rstrip('/')}/leads"
    notified = await notifier.notify_lead_created(
        clinic_name=body.clinic_name,
        contact=body.contact,
        admin_url=admin_url,
        diagnosis_note=diagnosis_note,
    )
    lead.notification_status = "SENT" if notified else "FAILED"
    lead.notification_error = None if notified else "Slack/webhook delivery failed or is not configured."

    # ── 접수 안내 문자. Slack 뒤에 보낸다 — AE 알림이 문자 사업자 장애에 묶이면 안 된다.
    sms = await inquiry_sms.acknowledge_inquiry(db, lead)
    await db.commit()

    return {
        "ok": True,
        "lead_id": str(lead.id),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "diagnosis_id": diagnosis_id,
        "ack_sms": sms.status.lower(),
    }
