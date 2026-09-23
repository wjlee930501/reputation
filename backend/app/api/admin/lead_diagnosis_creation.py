"""Admin이 직접 만드는 노출 진단(콜용 보고서).

두 가지를 한 경로로 처리한다.

* **새로 만들기** — 문의가 없는 병원도 진단한다. 리드 행을 함께 만든다
  (`LeadDiagnosis.lead_id`가 필수이고, 영업 기록도 리드에 남는 편이 맞다).
* **기존 도입문의 고쳐 만들기** — 원장이 진료과·지역·키워드를 틀리게 적은 리드다.
  값이 비어 있으면 자동 생성이 거절돼 `POST /admin/leads/{id}/diagnoses/internal`이
  처음 만들지만, **틀린 값은 자동 생성을 통과한다** — 잘못된 질의로 측정이 끝나고
  콜용 보고서까지 만들어진다. 그 뒤에는 손댈 길이 없었다(`다시 측정`은 저장된 그
  질의를 다시 묻고, `보고서 다시 만들기`는 같은 측정 결과로 PDF만 다시 만든다).

어느 쪽이든 만들어지는 진단은 무료 진단 자리·잠금·공개 토큰을 소비하지 않고
INTERNAL로 태어난다. 고객 발송 폴러가 건드리지 않는다.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.api.public.leads import contains_patient_sensitive_text
from app.core.config import settings
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.lead import (
    LEAD_CLINIC_TYPE_INQUIRY_MARKER,
    LEAD_SOURCE_INQUIRY,
    SalesLead,
    is_internal_inquiry,
)
from app.models.lead_diagnosis import DeliveryStatus, LeadDiagnosis, ReportStatus
from app.models.operations import OperationRun, OperationRunState
from app.services import inquiry_diagnosis
from app.services.operation_run_keys import normalize_operation_key

router = APIRouter(prefix="/admin/lead-diagnoses", tags=["Admin — Lead diagnoses"])


class ManualDiagnosisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    # 정식 병원명. "연세의원"과 "강남연세의원"은 다른 문자열이고 판정이 여기에 달려 있다.
    clinic_name: str = Field(min_length=2, max_length=200)
    specialty: str = Field(min_length=1, max_length=100)
    region_keyword: str = Field(min_length=1, max_length=100)
    core_keywords: list[str] = Field(min_length=1, max_length=4)
    # 영업 기록이므로 연락할 수단은 남긴다. 병원 대표번호도 괜찮다. 고쳐 만드는 경로는
    # 리드에 원장이 남긴 연락처가 이미 있고 그 값을 덮지 않으므로 받지 않아도 된다.
    contact: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=100)
    # 기존 도입문의를 고쳐 만드는 경우에만 준다. 활성 진단이 있으면 갈음한다.
    lead_id: uuid.UUID | None = None
    # 왜 사람이 직접 만드는가. 갈음 기록과 감사 로그에 그대로 남는다.
    reason: str = Field(min_length=3, max_length=200)

    @field_validator(
        "clinic_name", "specialty", "region_keyword", "contact", "contact_name", "reason"
    )
    @classmethod
    def clean_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Must not be blank")
        return cleaned

    @model_validator(mode="after")
    def contact_required_for_new_lead(self) -> "ManualDiagnosisRequest":
        if self.lead_id is None and self.contact is None:
            raise ValueError("연락할 수단을 남겨 주세요. 병원 대표번호도 괜찮습니다.")
        return self

    @field_validator("core_keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        unique = list(dict.fromkeys(cleaned))
        if not unique:
            raise ValueError("핵심 키워드를 최소 1개 입력해 주세요.")
        if any(len(value) > 50 for value in unique):
            raise ValueError("핵심 키워드는 50자 이내로 입력해 주세요.")
        return unique[:4]

    @field_validator(
        "clinic_name", "specialty", "region_keyword", "contact_name", "reason"
    )
    @classmethod
    def reject_patient_sensitive_text(cls, value: str | None) -> str | None:
        if value and contains_patient_sensitive_text(value):
            raise ValueError("환자 개인정보나 진료기록은 진단 정보에 입력하지 마세요.")
        return value

    @field_validator("core_keywords")
    @classmethod
    def reject_patient_sensitive_keywords(cls, values: list[str]) -> list[str]:
        if any(contains_patient_sensitive_text(value) for value in values):
            raise ValueError("환자 개인정보나 진료기록은 진단 정보에 입력하지 마세요.")
        return values


async def _load_inquiry_lead(db: AsyncSession, lead_id: uuid.UUID) -> SalesLead:
    lead = (
        await db.execute(
            select(SalesLead).where(SalesLead.id == lead_id).with_for_update()
        )
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="상담 요청을 찾을 수 없습니다.")
    if not is_internal_inquiry(lead):
        raise HTTPException(
            status_code=400,
            detail="도입문의 상담 요청에만 내부용 진단을 만들 수 있습니다.",
        )
    return lead


def _new_outbound_lead(body: ManualDiagnosisRequest, actor: str) -> SalesLead:
    """문의가 없는 병원의 리드 행.

    `privacy`는 False다 — 원장이 동의한 적이 없다. 이 값을 True로 적으면 받지 않은
    동의를 받았다고 기록하는 셈이고, 그 기록은 나중에 아무도 되돌릴 수 없다.
    보유기간은 공개 접수와 같은 규칙을 쓴다.
    """
    return SalesLead(
        clinic_name=body.clinic_name,
        # 도입문의 표식을 심어야 이 리드의 진단이 고객 발송 폴러 밖에 있는다.
        clinic_type=LEAD_CLINIC_TYPE_INQUIRY_MARKER,
        contact=body.contact,
        contact_name=body.contact_name,
        question=f"Admin 수동 생성({actor}): {body.reason}",
        privacy=False,
        source=LEAD_SOURCE_INQUIRY,
        source_path="/admin/lead-diagnoses",
        specialty=body.specialty,
        region_keyword=body.region_keyword,
        core_keywords=list(body.core_keywords),
        retain_until=datetime.now(timezone.utc)
        + timedelta(days=settings.LEAD_RETENTION_DAYS),
    )


# 생성 영수증. 진단 한 건은 유료 공급자 호출 18회를 산다 — 같은 제출이 두 번 도착하면
# (두 번 누름, 프록시·브라우저 재전송) 두 번째는 새로 사지 않고 첫 결과를 돌려준다.
# 고쳐 만드는 경로라면 방금 만든 진단을 곧바로 갈음해 두 건을 모두 사게 된다.
CREATE_OPERATION_TYPE = "CREATE_LEAD_DIAGNOSIS"
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


def _request_fingerprint(body: ManualDiagnosisRequest) -> str:
    return hashlib.sha256(body.model_dump_json().encode()).hexdigest()


async def _replayed_creation(
    db: AsyncSession, actor: AdminUser, key: str, fingerprint: str
) -> dict | None:
    run = await db.scalar(
        select(OperationRun).where(
            OperationRun.operation_type == CREATE_OPERATION_TYPE,
            OperationRun.requested_by_id == actor.id,
            OperationRun.hospital_id.is_(None),
            OperationRun.idempotency_key == key,
        )
    )
    if run is None:
        return None
    payload = run.request_payload or {}
    if payload.get("request_fingerprint") != fingerprint:
        raise HTTPException(
            status_code=409,
            detail="같은 요청 키로 다른 값이 들어왔습니다. 화면을 새로 고친 뒤 다시 만들어 주세요.",
        )
    return {**payload["response"], "idempotent_replay": True}


def _enqueue(diagnosis_id: str) -> None:
    """Best-effort fast path; the PENDING database drain remains the guarantee."""
    inquiry_diagnosis.enqueue_inquiry_diagnosis(diagnosis_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_manual_diagnosis(
    body: ManualDiagnosisRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
    idempotency_key: IdempotencyKey = None,
) -> dict:
    """콜용 노출 진단 1건을 만든다. 기존 진단이 있으면 갈음하고 보존한다."""
    key = normalize_operation_key(idempotency_key)
    fingerprint = _request_fingerprint(body)
    if key is not None:
        replay = await _replayed_creation(db, actor, key, fingerprint)
        if replay is not None:
            return replay
    if body.lead_id is not None:
        lead = await _load_inquiry_lead(db, body.lead_id)
        existing = await inquiry_diagnosis.active_diagnosis_for(db, lead.id)
    else:
        lead = _new_outbound_lead(body, actor.email)
        db.add(lead)
        await db.flush()
        existing = None

    try:
        diagnosis = await inquiry_diagnosis.create_inquiry_diagnosis(
            db,
            lead,
            inquiry_diagnosis.InquiryDiagnosisInput(
                specialty=body.specialty,
                region_keyword=body.region_keyword,
                core_keywords=list(body.core_keywords),
                contact_name=body.contact_name,
                clinic_name=body.clinic_name,
            ),
            actor=actor.email,
            # 활성 진단이 없으면 갈음할 것도 없다. 사유는 그때만 넘긴다.
            supersede_reason=body.reason if existing is not None else None,
        )
    except inquiry_diagnosis.InquiryDiagnosisError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    response = {
        "detail": "manual_diagnosis_queued",
        "lead_id": str(lead.id),
        "diagnosis_id": str(diagnosis.id),
        "delivery_status": DeliveryStatus.INTERNAL.value,
        "superseded_diagnosis_id": str(existing.id) if existing is not None else None,
    }
    if key is not None:
        now = datetime.now(timezone.utc)
        db.add(
            OperationRun(
                operation_type=CREATE_OPERATION_TYPE,
                state=OperationRunState.SUCCEEDED.value,
                idempotency_key=key,
                requested_by_id=actor.id,
                request_payload={
                    "source_id": str(diagnosis.id),
                    "request_fingerprint": fingerprint,
                    "response": response,
                },
                completed_at=now,
            )
        )
    try:
        await db.commit()
    except IntegrityError:
        # 같은 키의 요청이 동시에 들어와 먼저 커밋했다. 이 요청의 리드·진단은 함께 되돌려진다.
        await db.rollback()
        replay = await _replayed_creation(db, actor, key, fingerprint) if key else None
        if replay is None:
            raise
        return replay
    background_tasks.add_task(_enqueue, str(diagnosis.id))
    return {**response, "idempotent_replay": False}


def _serialize_history_row(diagnosis: LeadDiagnosis, lead: SalesLead | None) -> dict:
    """생성 화면이 "내가 방금 만든 것"을 확인할 수 있는 최소한의 정보.

    연락처·이메일·문의 원문은 담지 않는다. 이 목록의 용도는 식별과 진행 상태 확인이고,
    상담 요청 목록처럼 대량 PII를 여는 표면이 되면 안 된다(그쪽은 그래서 감사 로그를
    남긴다). 병원명·진료과·지역·키워드는 측정 입력 그 자체라 여기 없으면 무엇을
    만들었는지 알 수 없다.
    """
    return {
        "id": str(diagnosis.id),
        "lead_id": str(diagnosis.lead_id),
        # 판정 대상은 진단에 고정된 이름이다. 값을 고쳐 만들면 리드의 병원명이 바뀌므로,
        # 리드에서 읽으면 갈음된 진단이 실제로 쟀던 이름 대신 새 이름을 보여준다.
        "clinic_name": diagnosis.subject_hospital_name,
        "specialty": lead.specialty if lead else None,
        "region_keyword": diagnosis.subject_region,
        "core_keywords": list(lead.core_keywords or []) if lead else [],
        "execution_status": diagnosis.execution_status,
        "report_status": diagnosis.report_status,
        "delivery_status": diagnosis.delivery_status,
        "superseded_at": diagnosis.superseded_at.isoformat()
        if diagnosis.superseded_at
        else None,
        "superseded_by_id": str(diagnosis.superseded_by_id)
        if diagnosis.superseded_by_id
        else None,
        "report_ready": diagnosis.report_status == ReportStatus.READY.value,
        "created_at": diagnosis.created_at.isoformat() if diagnosis.created_at else None,
    }


@router.get("")
async def list_manual_diagnoses(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    _actor: AdminUser = Depends(require_active_account),
) -> dict:
    """최근 콜용 진단 목록. 생성 화면 하단에서 만든 것을 바로 확인하고 연다.

    만든 뒤 "상담 요청에서 보세요"로 넘기면, 리드가 수십 건인 목록에서 방금 만든 것을
    찾아야 한다. 만든 화면이 만든 결과를 보여주는 편이 맞다.

    대상은 INTERNAL(콜용)뿐이다. 무료 진단 신청은 성격이 다르고 이 화면의 일이 아니다.
    """
    # 모델에 관계가 없으므로 명시적으로 조인한다. 관계를 새로 더하면 이 화면 하나 때문에
    # 다른 경로의 로딩 전략까지 바뀐다.
    rows = (
        await db.execute(
            select(LeadDiagnosis, SalesLead)
            .join(SalesLead, SalesLead.id == LeadDiagnosis.lead_id)
            .where(LeadDiagnosis.delivery_status == DeliveryStatus.INTERNAL.value)
            .order_by(LeadDiagnosis.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {"items": [_serialize_history_row(diagnosis, lead) for diagnosis, lead in rows]}
