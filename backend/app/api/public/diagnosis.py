"""공개 API — 무료 AI 노출 진단 접수 (1단 리드마그넷).

기존 `/public/leads`(자유 문의)와 **별도 엔드포인트**다. 받는 필드도, 방어 장치도,
성공의 의미도 다르다. 하나의 라우트에 분기를 넣으면 문의 폼의 검증이 진단 폼에
잘못 적용되거나 그 반대가 된다.

접수 1건이 하는 일:
  1. 남용 방어 (허니팟 · 동의 · 환자 민감정보 · 프롬프트 인젝션)
  2. 선착순 자리 1칸 확보 — **이것이 예산 상한이다**
  3. 전화번호 + 이메일 이중 잠금
  4. 질의 3개 생성 (병원명은 절대 넣지 않는다)
  5. 열람 토큰 발급 → 상태 페이지 주소 반환
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public.leads import contains_patient_sensitive_text
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip, limiter
from app.models.lead import LEAD_SOURCE_AI_DIAGNOSIS, SalesLead
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    ExecutionStatus,
    LeadDiagnosis,
    LeadDiagnosisSlotDay,
    LeadReportArtifact,
    LeadReportToken,
    ReportStatus,
)
from app.services import lead_report_token
from app.services.lead_diagnosis_identity import (
    InvalidEmail,
    InvalidPhoneNumber,
    email_lock_hash,
    phone_lock_hash,
)
from app.services.query_mapper import (
    QUERY_SLOT_COUNT,
    QueryMappingError,
    build_lead_diagnosis_queries,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/diagnosis", tags=["Public — AI Diagnosis"])

_KST = ZoneInfo("Asia/Seoul")
_HONEYPOT_FIELDS = ("website", "url")
_NON_WORD = re.compile(r"[\s\-_·]+")

# 자리가 리셋되는 시각(KST). 자정이 아니라 아침인 이유는 **병원이 문을 여는 시간에
# 자리가 열려야** 하기 때문이다. 자정 리셋은 새벽에 자리가 소진되어, 정작 원장님이
# 출근해서 들어오면 이미 마감돼 있는 상태를 만든다.
#
# **랜딩 문구와 같은 값이어야 한다** (site/lib/landing-copy.ts의 heroScarcity.note).
# 어긋나면 신청자가 안내받은 시각에 와서 마감 화면을 본다.
SLOT_RESET_HOUR_KST = 8


def _slot_day(now: datetime | None = None) -> date:
    """지금이 속한 '자리 날짜'.

    08:00 이전은 아직 **전날 자리**다. 경계를 시각으로 옮기면 달력 날짜와 자리 날짜가
    갈라지므로, 자리를 세는 곳과 배정하는 곳이 반드시 같은 함수를 써야 한다.
    """
    moment = now or datetime.now(_KST)
    return (moment - timedelta(hours=SLOT_RESET_HOUR_KST)).date()


def _normalized_for_containment(value: str) -> str:
    return _NON_WORD.sub("", (value or "")).lower()


def keyword_contains_hospital_name(hospital_name: str, keywords: list[str]) -> bool:
    """키워드에 병원명을 실어 언급을 유도하는 프롬프트 인젝션 (PRD F1-4).

    병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다(F1-1).
    `query_mapper`는 병원명 인자를 받지 않아 구조적으로 막혀 있지만, 키워드 칸으로
    우회하는 경로가 남으므로 접수에서 끊는다.

    띄어쓰기·하이픈만 바꾼 우회를 막기 위해 정규화 후 비교한다.
    """
    needle = _normalized_for_containment(hospital_name)
    if not needle:
        return False
    return any(needle in _normalized_for_containment(keyword) for keyword in keywords)


class DiagnosisRequest(BaseModel):
    # ── 진단 정보
    clinic_name: str = Field(min_length=2, max_length=200)     # 정식 병원명 (~의원까지)
    clinic_type: str = Field(min_length=1, max_length=100)     # 진료과
    region_keyword: str = Field(min_length=1, max_length=100)  # 지하철역·동
    clinic_phone: str = Field(min_length=1, max_length=40)     # 병원 대표번호
    core_keywords: list[str] = Field(min_length=1, max_length=4)

    # ── 신청 정보
    contact_name: str = Field(min_length=1, max_length=100)
    contact: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)

    privacy: bool
    source_path: str | None = Field(default=None, max_length=500)

    # 허니팟 — 채워져 있으면 조용히 200. 필드명은 _HONEYPOT_FIELDS와 일치해야 한다.
    website: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=500)

    @field_validator(
        "clinic_name", "clinic_type", "region_keyword", "clinic_phone",
        "contact_name", "contact", "email", "source_path",
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
    def clean_keywords(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v and v.strip()]
        if not cleaned:
            raise ValueError("핵심 키워드를 최소 1개 입력해 주세요.")
        # 중복 제거 후에도 순서를 유지한다 — 첫 번째 키워드가 질의 슬롯 2를 결정한다.
        seen: set[str] = set()
        unique = [k for k in cleaned if not (k in seen or seen.add(k))]
        if any(len(k) > 50 for k in unique):
            raise ValueError("핵심 키워드는 50자 이내로 입력해 주세요.")
        return unique[:4]

    # 공개 폼의 모든 자유 텍스트에 같은 검증을 적용한다 — 한 칸만 열려 있어도
    # "홍길동 환자 900101-1234567"이 평문으로 Slack(국외 이전)과 Admin에 노출된다.
    @field_validator("clinic_name", "clinic_type", "region_keyword", "contact_name")
    @classmethod
    def reject_patient_sensitive_free_text(cls, value: str) -> str:
        if contains_patient_sensitive_text(value):
            raise ValueError("환자 개인정보나 진료기록은 이 양식에 입력하지 마세요.")
        return value

    @field_validator("core_keywords")
    @classmethod
    def reject_patient_sensitive_keywords(cls, values: list[str]) -> list[str]:
        for value in values:
            if contains_patient_sensitive_text(value):
                raise ValueError("환자 개인정보나 진료기록은 이 양식에 입력하지 마세요.")
        return values


def _remaining_slots(used: int) -> int:
    return max(0, settings.LEADGEN_DAILY_SLOTS - used)


@router.get("/slots")
async def get_slot_availability(db: AsyncSession = Depends(get_db)):
    """오늘 남은 자리. 랜딩의 "오늘 남은 자리 N / 20"이 이 값을 그대로 쓴다.

    **실제 카운터다.** 희소성을 연출하려고 숫자를 조작하지 않는다 — 방법론을 공개하는
    것이 이 제품의 차별점인데 카운터를 꾸미면 그 주장이 무너진다.
    """
    today = _slot_day()
    used = await db.scalar(
        select(func.count()).select_from(LeadDiagnosis).where(LeadDiagnosis.slot_date == today)
    )
    used = int(used or 0)
    return {
        "date": today.isoformat(),
        "total": settings.LEADGEN_DAILY_SLOTS,
        "used": used,
        "remaining": _remaining_slots(used),
    }


async def _resolve_token(db: AsyncSession, raw_token: str) -> tuple[LeadReportToken, LeadDiagnosis]:
    """토큰 → (토큰 행, 진단). 실패는 전부 404로 통일한다.

    "만료됨"과 "없음"을 구분해 알려주면 토큰 존재 여부를 확인하는 오라클이 된다.
    """
    token_hash = lead_report_token.hash_report_token(raw_token or "")
    row = (
        await db.execute(
            select(LeadReportToken).where(LeadReportToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise HTTPException(status_code=404, detail="유효하지 않은 링크입니다.")

    diagnosis = (
        await db.execute(select(LeadDiagnosis).where(LeadDiagnosis.id == row.diagnosis_id))
    ).scalar_one_or_none()
    if diagnosis is None:  # pragma: no cover - FK CASCADE로 함께 지워진다
        raise HTTPException(status_code=404, detail="유효하지 않은 링크입니다.")

    row.last_accessed_at = now
    row.access_count = (row.access_count or 0) + 1
    await db.commit()
    return row, diagnosis


# 사용자가 보는 진행 단계. 내부 3축 상태를 그대로 노출하지 않는다 —
# 신청자에게 'PARTIAL'이나 'BUILDING'은 아무 의미가 없고, 내부 모델이 바뀔 때마다
# 공개 계약이 흔들린다.
_PHASE_MEASURING = "MEASURING"
_PHASE_BUILDING = "BUILDING_REPORT"
_PHASE_READY = "READY"
_PHASE_FAILED = "FAILED"
_PHASE_EXPIRED = "EXPIRED"


def _public_phase(diagnosis: LeadDiagnosis) -> str:
    if diagnosis.report_status == ReportStatus.PURGED.value:
        return _PHASE_EXPIRED
    if diagnosis.report_status == ReportStatus.READY.value:
        return _PHASE_READY
    if (
        diagnosis.execution_status == ExecutionStatus.FAILED.value
        or diagnosis.report_status == ReportStatus.BLOCKED.value
    ):
        return _PHASE_FAILED
    if diagnosis.execution_status in REPORTABLE_EXECUTION_STATUSES:
        return _PHASE_BUILDING
    return _PHASE_MEASURING


@router.get("/{token}/status")
async def get_diagnosis_status(token: str, db: AsyncSession = Depends(get_db)):
    """접수 직후 사용자가 보는 화면이 폴링한다 (PRD F6-5).

    이메일 확인 링크를 없앴으므로(F1-4), 이 페이지가 "메일이 안 와도 결과에 도달하는
    두 번째 경로"를 겸한다.
    """
    _, diagnosis = await _resolve_token(db, token)
    phase = _public_phase(diagnosis)
    return {
        "phase": phase,
        "hospital_name": diagnosis.subject_hospital_name,
        "submitted_at": diagnosis.created_at.isoformat() if diagnosis.created_at else None,
        "report_ready": phase == _PHASE_READY,
        # 실패 사유 원문을 그대로 내보내지 않는다 — 내부 예외 메시지가 공개 표면에 실린다.
        "message": {
            _PHASE_MEASURING: "AI 답변을 측정하고 있습니다. 완료되면 이메일로 알려드립니다.",
            _PHASE_BUILDING: "측정을 마쳤습니다. 리포트를 만들고 있습니다.",
            _PHASE_READY: "리포트가 준비되었습니다.",
            _PHASE_FAILED: "측정에 실패했습니다. 담당자가 확인 후 연락드립니다.",
            _PHASE_EXPIRED: "보관 기간이 지나 리포트가 삭제되었습니다.",
        }[phase],
    }


@router.get("/{token}")
async def get_diagnosis_report(token: str, db: AsyncSession = Depends(get_db)):
    """블러 리포트 PDF.

    파기 후에는 **410 Gone**이다. 404가 아닌 이유는 "있었고 우리가 지웠다"가 사실이기
    때문이다 — 없었던 것처럼 응답하면 파기 사실 자체가 기록에서 사라진다.
    """
    _, diagnosis = await _resolve_token(db, token)

    if diagnosis.report_status == ReportStatus.PURGED.value:
        raise HTTPException(status_code=410, detail="보관 기간이 지나 삭제된 리포트입니다.")
    if diagnosis.report_status != ReportStatus.READY.value:
        raise HTTPException(status_code=409, detail="리포트가 아직 준비되지 않았습니다.")

    artifact = (
        await db.execute(
            select(LeadReportArtifact)
            .where(
                LeadReportArtifact.diagnosis_id == diagnosis.id,
                LeadReportArtifact.purged_at.is_(None),
            )
            .order_by(LeadReportArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if artifact is None:
        # READY인데 산출물이 없다 = 업로드와 상태가 어긋난 상태. 조용히 빈 응답을 주지 않는다.
        logger.error("lead report READY but no artifact: diagnosis=%s", diagnosis.id)
        raise HTTPException(status_code=409, detail="리포트가 아직 준비되지 않았습니다.")

    data = _read_artifact(artifact.storage_uri)
    if data is None:
        logger.error("lead report artifact unreadable: %s", artifact.storage_uri)
        raise HTTPException(status_code=409, detail="리포트를 불러오지 못했습니다.")

    filename = f"{diagnosis.subject_hospital_name}_AI노출진단.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            # 링크가 전달돼도 검색엔진에 잡히거나 referrer로 새어나가지 않게 한다(F5-4).
            "Content-Disposition": f'inline; filename*=UTF-8\'\'{quote(filename)}',
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
        },
    )


def _read_artifact(storage_uri: str) -> bytes | None:
    """산출물을 우리 서버가 읽어 스트리밍한다.

    GCS 서명 URL로 리다이렉트하지 않는 이유: 그 URL은 우리 헤더(no-store·noindex)를
    벗어나고, 만료 전까지 토큰 폐기와 무관하게 살아 있다.
    """
    if not storage_uri:
        return None
    if not storage_uri.startswith("gs://"):
        path = Path(storage_uri)
        return path.read_bytes() if path.exists() else None
    try:
        from google.cloud import storage

        _, _, rest = storage_uri.partition("gs://")
        bucket_name, _, blob_name = rest.partition("/")
        client = storage.Client()
        return client.bucket(bucket_name).blob(blob_name).download_as_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.error("lead report artifact download failed: %s", exc)
        return None


def _violated(exc: IntegrityError, *index_names: str) -> bool:
    """어느 제약이 걸렸는지. 잠금 위반은 사용자 안내이고 자리 위반은 재시도라 갈라야 한다."""
    text = f"{exc.orig}"
    return any(name in text for name in index_names)


@router.post("")
@limiter.limit(settings.PUBLIC_LEAD_RATE_LIMIT)
async def create_diagnosis(
    request: Request,
    body: DiagnosisRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # 허니팟 — 정상 사용자는 비워둔다. 봇에게는 성공처럼 보이게 하고 저장하지 않는다.
    if any((getattr(body, field, None) or "").strip() for field in _HONEYPOT_FIELDS):
        return {"ok": True, "diagnosis_id": None, "status_url": None}

    if not body.privacy:
        raise HTTPException(status_code=400, detail="개인정보 수집·이용 동의가 필요합니다.")

    # 질의에 들어가는 **모든** 입력을 검사한다. 키워드만 막으면 진료과나 지역 칸에
    # 병원명을 넣어 우회할 수 있고, 그러면 언급이 보장되어 측정이 무의미해진다(F1-1).
    if keyword_contains_hospital_name(
        body.clinic_name, [*body.core_keywords, body.clinic_type, body.region_keyword]
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "진료과·지역·키워드에는 병원명을 넣을 수 없습니다. "
                "진료·증상 키워드를 입력해 주세요."
            ),
        )

    try:
        email_hash = email_lock_hash(body.email)
    except InvalidEmail as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        phone_hash = phone_lock_hash(body.clinic_phone)
    except InvalidPhoneNumber as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        queries = build_lead_diagnosis_queries(
            region=body.region_keyword,
            specialty=body.clinic_type,
            keywords=body.core_keywords,
        )
    except QueryMappingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # fail-closed 최종 확인 — 위 검사를 우회한 조합이 있어도 병원명이 들어간 질의는
    # 절대 저장되지 않는다. 측정 무결성의 마지막 방어선이다.
    if keyword_contains_hospital_name(body.clinic_name, [q["text"] for q in queries]):
        logger.error("hospital name leaked into generated queries: %s", body.clinic_name)
        raise HTTPException(
            status_code=400,
            detail="입력값에서 병원명을 제외해 주세요. 병원명이 포함되면 측정이 무의미해집니다.",
        )

    today = _slot_day()
    now = datetime.now(timezone.utc)

    # ── 자리를 원자적으로 잡는다.
    # 날짜 행에 대한 조건부 UPDATE는 행 잠금으로 직렬화되므로, 20건이 동시에 들어와도
    # 정확히 한도까지 배정된다. COUNT를 읽고 +1 하는 방식은 전부 같은 값을 읽어
    # 한 건만 성공하고 나머지가 재시도 소진으로 503이 됐다 — 자리가 남았는데도.
    await db.execute(
        pg_insert(LeadDiagnosisSlotDay)
        .values(slot_date=today, used=0)
        .on_conflict_do_nothing(index_elements=["slot_date"])
    )
    claimed = (
        await db.execute(
            update(LeadDiagnosisSlotDay)
            .where(
                LeadDiagnosisSlotDay.slot_date == today,
                LeadDiagnosisSlotDay.used < settings.LEADGEN_DAILY_SLOTS,
            )
            .values(used=LeadDiagnosisSlotDay.used + 1)
            .returning(LeadDiagnosisSlotDay.used)
        )
    ).scalar_one_or_none()

    if claimed is None:
        await db.rollback()
        raise HTTPException(
            status_code=429,
            detail=(
                f"오늘 진단 {settings.LEADGEN_DAILY_SLOTS}건이 모두 접수되었습니다. "
                "내일 다시 신청해 주세요."
            ),
        )

    slot_no = int(claimed)

    lead = SalesLead(
        clinic_name=body.clinic_name,
        clinic_type=body.clinic_type,
        contact=body.contact,
        contact_name=body.contact_name,
        clinic_phone=body.clinic_phone,
        email=body.email,
        region_keyword=body.region_keyword,
        core_keywords=body.core_keywords,
        question=None,
        privacy=True,
        source=LEAD_SOURCE_AI_DIAGNOSIS,
        source_path=body.source_path,
        consent_ip=get_request_ip(request),
        # 클라이언트 입력을 신뢰하지 않고 항상 서버 ENV에서 가져온다.
        consent_version=settings.LEAD_CONSENT_VERSION.strip()[:40],
        retain_until=now + timedelta(days=settings.LEAD_RETENTION_DAYS),
        notification_status="PENDING",
    )
    diagnosis = LeadDiagnosis(
        applicant_email_hash=email_hash,
        subject_phone_hash=phone_hash,
        subject_hospital_name=body.clinic_name,
        subject_region=body.region_keyword,
        slot_date=today,
        slot_no=slot_no,
        queries=queries,
        requested_models={
            "openai": settings.OPENAI_MODEL_QUERY,
            "gemini": settings.GEMINI_MODEL,
            "judge": settings.OPENAI_MODEL_PARSE,
        },
        repeat_count=settings.LEADGEN_REPEAT_COUNT,
    )

    try:
        # 리드·진단·토큰을 한 트랜잭션에 넣는다. 잠금 위반은 commit이 아니라 이 flush에서
        # 터지므로, 삽입 전체를 감싸지 않으면 예외가 그대로 500으로 새어 나간다.
        db.add(lead)
        await db.flush()
        diagnosis.lead_id = lead.id
        db.add(diagnosis)
        await db.flush()
        # 토큰은 진단 id에서 유도한다 — 나중에 리포트 메일이 같은 링크를 실어야 한다.
        raw_token, token_hash = lead_report_token.issue_report_token(diagnosis.id)
        db.add(
            LeadReportToken(
                diagnosis_id=diagnosis.id,
                token_hash=token_hash,
                expires_at=now + timedelta(days=settings.LEAD_REPORT_TOKEN_TTL_DAYS),
            )
        )
        await db.commit()
    except IntegrityError as exc:
        # 리드와 진단을 한 트랜잭션에 넣었으므로 롤백하면 고아 리드가 남지 않는다.
        await db.rollback()
        if _violated(exc, "uq_lead_diagnoses_email_lock", "uq_lead_diagnoses_phone_lock"):
            # **기존 행의 상태 URL을 돌려주지 않는다.** 남의 병원 대표번호를 아는
            # 사람이 재신청으로 그 병원의 리포트 링크를 얻어가는 경로가 된다.
            raise HTTPException(
                status_code=409,
                detail=(
                    "이미 진단을 신청한 병원입니다. 한 병원당 한 번만 신청할 수 있습니다. "
                    "리포트 링크는 신청 시 입력한 이메일로 발송됩니다."
                ),
            ) from exc
        raise

    # Slack 지연/장애가 신청 응답을 늦추지 않도록 커밋 뒤 Celery로 넘긴다. Redis가
    # 순간적으로 내려가도 1분 폴러가 notification_status=PENDING을 다시 회수한다.
    background_tasks.add_task(_enqueue_lead_intake_notification, str(lead.id))

    return {
        "ok": True,
        "diagnosis_id": str(diagnosis.id),
        "status_url": lead_report_token.report_status_url(raw_token),
        "query_count": QUERY_SLOT_COUNT,
        "slot_no": diagnosis.slot_no,
        "remaining_slots": _remaining_slots(slot_no),
    }


def _enqueue_lead_intake_notification(lead_id: str) -> None:
    """응답 이후 broker에 접수 알림을 넣는다; 실패분은 1분 폴러가 회수한다."""
    try:
        from app.workers.lead_diagnosis_tasks import notify_lead_intake

        notify_lead_intake.delay(lead_id)
    except Exception:  # noqa: BLE001 — 접수는 이미 안전하게 저장됐다.
        logger.warning("lead intake notification enqueue failed for %s", lead_id)
