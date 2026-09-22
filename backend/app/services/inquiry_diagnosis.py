"""도입문의 리드의 내부용(INTERNAL) 초도 노출 진단 생성.

두 진입점이 같은 규칙을 써야 한다:

* 공개 접수(`api/public/leads.create_lead`) — 랜딩 폼이 진료과·지역·키워드를 함께 보내면
  접수 직후 자동으로 만든다.
* Admin(`api/admin/leads.create_internal_inquiry_diagnosis`) — 폼이 비어 있었거나 자동
  생성이 거절된 리드를 AE가 채워서 만든다.

여기서 만드는 진단은 무료 진단 자리·잠금·공개 토큰을 소비하지 않고, 고객 발송 폴러가
건드리지 않는 INTERNAL 상태로 태어난다. 검증은 전부 mutate 전에 끝난다 — 예외가 나면
세션에 남는 변경이 없어 호출자가 rollback 없이 다음 일(Slack·문자)을 이어갈 수 있다.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.lead import LEAD_CLINIC_TYPE_INQUIRY_MARKER, SalesLead, is_internal_inquiry
from app.models.lead_diagnosis import DeliveryStatus, LeadDiagnosis
from app.services import sov_engine
from app.services.audit_log import write_audit_log
from app.services.query_mapper import QueryMappingError, build_lead_diagnosis_queries

logger = logging.getLogger(__name__)

PUBLIC_INTAKE_ACTOR = "system:public-inquiry"


class InquiryDiagnosisError(Exception):
    """생성 거절. `status_code`는 Admin 응답에 그대로 쓰고, 공개 접수는 사유만 기록한다."""

    def __init__(self, status_code: int, detail: str | dict):
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class InquiryDiagnosisInput:
    specialty: str
    region_keyword: str
    core_keywords: list[str] = field(default_factory=list)
    email: str | None = None
    clinic_phone: str | None = None
    contact_name: str | None = None


async def latest_diagnosis_for(db: AsyncSession, lead_id: uuid.UUID) -> LeadDiagnosis | None:
    return await db.scalar(
        select(LeadDiagnosis)
        .where(LeadDiagnosis.lead_id == lead_id)
        .order_by(LeadDiagnosis.created_at.desc())
        .limit(1)
    )


async def active_diagnosis_for(db: AsyncSession, lead_id: uuid.UUID) -> LeadDiagnosis | None:
    """갈음되지 않은 진단. 리드당 하나뿐이며 이것이 '이 리드의 현재 진단'이다."""
    return await db.scalar(
        select(LeadDiagnosis)
        .where(
            LeadDiagnosis.lead_id == lead_id,
            LeadDiagnosis.superseded_at.is_(None),
        )
        .order_by(LeadDiagnosis.created_at.desc())
        .limit(1)
    )


def build_inquiry_queries(clinic_name: str, spec: InquiryDiagnosisInput) -> list[dict]:
    """질의를 만들되 병원명이 섞이면 거절한다. 세션을 건드리지 않는 순수 검증 단계."""
    # 지연 import — public.diagnosis가 public.leads를 import하므로 모듈 순환을 피한다.
    from app.api.public.diagnosis import keyword_contains_hospital_name

    if keyword_contains_hospital_name(
        clinic_name, [spec.specialty, spec.region_keyword, *spec.core_keywords]
    ):
        raise InquiryDiagnosisError(
            400,
            "진료과·지역·키워드에는 병원명을 넣을 수 없습니다. 진료·증상 키워드를 입력해 주세요.",
        )
    try:
        queries = build_lead_diagnosis_queries(
            region=spec.region_keyword,
            specialty=spec.specialty,
            keywords=spec.core_keywords,
        )
    except QueryMappingError as exc:
        raise InquiryDiagnosisError(400, str(exc)) from exc
    if keyword_contains_hospital_name(clinic_name, [query["text"] for query in queries]):
        raise InquiryDiagnosisError(
            400, "입력값에서 병원명을 제외해 주세요. 병원명이 포함되면 측정이 무의미합니다."
        )
    return queries


async def create_inquiry_diagnosis(
    db: AsyncSession,
    lead: SalesLead,
    spec: InquiryDiagnosisInput,
    *,
    actor: str,
    supersede_reason: str | None = None,
) -> LeadDiagnosis:
    """INTERNAL 진단 1건을 세션에 추가하고 flush한다. commit과 큐잉은 호출자 몫이다.

    `supersede_reason`이 있으면 기존 활성 진단을 갈음하고 새로 만든다. 도입문의 폼에
    진료과·지역·키워드를 틀리게 적은 리드를 고치는 경로다 — 값이 비어 있으면 자동
    생성이 거절돼 이 함수가 처음 만들지만, 틀린 값은 자동 생성을 통과해 잘못된 질의로
    측정까지 끝난다. 옛 진단은 지우지 않는다. 실제로 지출한 공급자 호출과 그때 무엇을
    쟀는지가 기록으로 남아야 한다.
    """
    if not is_internal_inquiry(lead):
        raise InquiryDiagnosisError(400, "도입문의 리드만 내부용 진단을 생성할 수 있습니다.")

    existing = await active_diagnosis_for(db, lead.id)
    if existing is not None and supersede_reason is None:
        raise InquiryDiagnosisError(
            409,
            {
                "message": "이 도입문의에는 이미 내부 진단이 있습니다.",
                "diagnosis_id": str(existing.id),
            },
        )
    if existing is not None and existing.delivery_status != DeliveryStatus.INTERNAL.value:
        # 고객에게 나갔거나 나가는 중인 진단은 갈음 대상이 아니다. 콜용이 아니라는 뜻이고,
        # 공개 토큰이 걸린 판을 조용히 비활성으로 만들면 링크의 의미가 바뀐다.
        raise InquiryDiagnosisError(
            409,
            {
                "message": "고객 발송 이력이 있는 진단은 갈음할 수 없습니다. 개발팀에 진단 ID를 알려 주세요.",
                "diagnosis_id": str(existing.id),
            },
        )

    queries = build_inquiry_queries(lead.clinic_name, spec)

    # 여기서부터 mutate. 위 검증이 모두 통과한 뒤라 예외로 세션이 반쯤 바뀌는 일이 없다.
    if spec.email is not None:
        lead.email = spec.email
    # 도입문의 표식은 남겨둔다. clinic_type 하나만 보는 판정(Admin 목록 배지·도입문의 상세
    # 카드, 고객 발송 폴러의 레거시 방어선)이 진료과로 덮이면 그 리드는 일반 진단 신청처럼
    # 보인다. 진료과는 `specialty` 컬럼과 슬롯 1 질의에 남는다.
    if (lead.clinic_type or "").strip() != LEAD_CLINIC_TYPE_INQUIRY_MARKER:
        lead.clinic_type = spec.specialty
    lead.specialty = spec.specialty
    lead.region_keyword = spec.region_keyword
    lead.core_keywords = list(spec.core_keywords)
    if spec.clinic_phone is not None:
        lead.clinic_phone = spec.clinic_phone
    if spec.contact_name is not None:
        lead.contact_name = spec.contact_name

    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        applicant_email_hash=None,
        subject_phone_hash=None,
        subject_hospital_name=lead.clinic_name,
        subject_region=spec.region_keyword,
        slot_date=None,
        slot_no=None,
        queries=queries,
        requested_models={
            "openai": settings.OPENAI_MODEL_QUERY,
            "gemini": settings.GEMINI_MODEL,
            "judge": settings.OPENAI_MODEL_PARSE,
        },
        measurement_config=sov_engine.measurement_protocol(),
        repeat_count=settings.LEADGEN_REPEAT_COUNT,
        delivery_status=DeliveryStatus.INTERNAL.value,
    )
    db.add(diagnosis)
    await db.flush()
    if existing is not None:
        # 갈음은 새 진단이 생긴 뒤에 표시한다 — 그 사이에 "활성 진단이 없는" 리드가
        # 만들어지면 폴러와 Admin 목록이 서로 다른 것을 본다.
        existing.superseded_at = datetime.now(timezone.utc)
        existing.superseded_by_id = diagnosis.id
    await write_audit_log(
        db,
        action="create_internal_inquiry_diagnosis",
        hospital_id=lead.converted_hospital_id,
        actor=actor,
        target_type="lead_diagnosis",
        target_id=diagnosis.id,
        detail={
            "lead_id": str(lead.id),
            "specialty": spec.specialty,
            "query_count": len(queries),
            "customer_delivery": False,
            "report_token_minted": False,
            "free_slot_claimed": False,
            "applicant_locks_claimed": False,
            "superseded_diagnosis_id": str(existing.id) if existing is not None else None,
            "supersede_reason": supersede_reason,
        },
    )
    return diagnosis


def enqueue_inquiry_diagnosis(diagnosis_id: str) -> None:
    """Best-effort fast path; the PENDING database drain remains the guarantee."""
    try:
        from app.workers.lead_diagnosis_tasks import run_lead_diagnosis

        run_lead_diagnosis.delay(diagnosis_id)
    except Exception:  # noqa: BLE001 — the committed PENDING row is drained every minute.
        logger.warning("internal inquiry diagnosis enqueue failed for %s", diagnosis_id)
