"""Public API — 원장 사전 설문(원장이 링크에서 작성).

AE가 병원 정보 탭에서 만든 링크(`/survey/{token}`)로 원장이 들어온다. 로그인은 없고 링크가 곧 열쇠다.

- 실패는 모두 404 "유효하지 않은 링크"로 통일한다(새 링크를 만들어 막힌 옛 링크와 없는 링크를 가리지
  않는다 — 토큰 존재 여부를 알려 주는 오라클이 되지 않게).
- 답을 저장하기 전에 개인정보 수집·이용 동의가 있어야 한다.
- 작성 중인 답은 자동 저장된다. 근거 자료에는 **제출한** 답만 들어간다(제출하면 설문 자료를 맞추고
  Essence 자동 요약을 부른다). 제출 뒤에도 같은 링크로 고쳐 다시 제출할 수 있다.
- 응답은 개인에게 발급된 표면이라 캐시하지 않는다(`main.PublicApiCacheMiddleware`).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.doctor_survey import DoctorSurvey
from app.models.hospital import Hospital
from app.services import doctor_survey_schema as schema
from app.services.audit_log import write_audit_log
from app.services.doctor_survey_source import (
    DOCTOR_SURVEY_ACTOR,
    sync_doctor_survey_source,
)
from app.services.doctor_survey_token import hash_survey_token
from app.services.essence_sources import enqueue_essence_refresh_best_effort
from app.utils.db_locks import acquire_hospital_advisory_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/doctor-survey", tags=["Public — Doctor survey"])

INVALID_LINK = "유효하지 않은 링크입니다. 담당자에게 새 링크를 요청해 주세요."
CONSENT_REQUIRED = "개인정보 수집·이용에 동의하셔야 작성하실 수 있습니다."
MAX_ANSWERS_BYTES = 256 * 1024


class ConsentBody(BaseModel):
    consent_version: str = Field(min_length=1, max_length=40)
    respondent: str = Field(min_length=1, max_length=20)


class AnswersBody(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    current_step: str | None = Field(default=None, max_length=400)
    schema_version: str | None = Field(default=None, max_length=40)
    # 화면의 저장 순번(밀리초 기반, 계속 커진다). 없으면 순번 검사를 하지 않는다.
    client_seq: int | None = Field(default=None, ge=0, le=2**62)


MAX_BODY_BYTES = 300 * 1024
_TOO_LARGE = "답이 너무 깁니다. 긴 답을 줄여 주세요."
_BAD_BODY = "입력 형식을 확인할 수 없습니다. 화면을 새로고침해 주세요."


async def _read_body(request: Request, model: type[BaseModel]) -> Any:
    """링크를 확인한 **뒤에** 본문을 읽는다 — 크기를 먼저 막고 끝까지 받지 않는다.

    FastAPI 본문 인자로 받으면 토큰을 보기 전에 본문 전체를 읽고 검사한다(없는 링크로 비용을 쓰게 된다).
    """

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=_TOO_LARGE)
        chunks.append(chunk)
    try:
        return model.model_validate_json(b"".join(chunks) or b"{}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_BAD_BODY) from exc


def uploads_enabled() -> bool:
    """파일 올리기는 다음 단계(PR2)에서 연다. 그 전에는 '담당자에게 따로 보내 주세요' 안내다."""

    return False


async def _survey_for_token(
    db: AsyncSession, raw_token: str, *, for_update: bool = False
) -> DoctorSurvey:
    raw = (raw_token or "").strip()
    if not raw or len(raw) > 100:
        raise HTTPException(status_code=404, detail=INVALID_LINK)
    stmt = select(DoctorSurvey).where(DoctorSurvey.token_hash == hash_survey_token(raw))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    survey = (await db.execute(stmt)).scalar_one_or_none()
    if survey is None:
        raise HTTPException(status_code=404, detail=INVALID_LINK)
    return survey


def _has_unsubmitted_changes(survey: DoctorSurvey) -> bool:
    if survey.submission_count == 0:
        return False
    return (survey.answers or {}) != (survey.submitted_answers or {})


def survey_view(survey: DoctorSurvey, hospital: Hospital | None) -> dict[str, Any]:
    answers = survey.answers or {}
    return {
        "hospital_name": getattr(hospital, "name", None),
        "new_clinic": bool(survey.new_clinic),
        "schema": schema.public_schema(uploads_enabled=uploads_enabled()),
        "answers": answers,
        "current_step": survey.current_step,
        "respondent": survey.respondent,
        "consented": survey.consent_at is not None
        and survey.consent_version == schema.CONSENT_VERSION,
        "saved_at": survey.last_saved_at.isoformat() if survey.last_saved_at else None,
        "submitted_at": survey.last_submitted_at.isoformat() if survey.last_submitted_at else None,
        "submission_count": survey.submission_count,
        "has_unsubmitted_changes": _has_unsubmitted_changes(survey),
        "progress": schema.progress(answers, new_clinic=bool(survey.new_clinic)),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _is_stale(survey: DoctorSurvey, body: AnswersBody) -> bool:
    return body.client_seq is not None and survey.client_seq is not None and body.client_seq <= survey.client_seq


def _write_draft(survey: DoctorSurvey, body: AnswersBody, answers: dict[str, Any], now: datetime) -> None:
    from app.services.survey_sources import mask_personal_info

    survey.answers = answers
    survey.schema_version = body.schema_version or schema.SCHEMA_VERSION
    if body.current_step is not None:
        # 화면 위치에는 진료 이름이 들어간다 — 답과 같이 가린다.
        survey.current_step = mask_personal_info(body.current_step)
    if body.client_seq is not None:
        survey.client_seq = max(body.client_seq, survey.client_seq or 0)
    survey.last_saved_at = now


def _require_consent(survey: DoctorSurvey) -> None:
    if survey.consent_at is None or survey.consent_version != schema.CONSENT_VERSION:
        raise HTTPException(status_code=409, detail=CONSENT_REQUIRED)


def _clean_answers(raw: dict[str, Any]) -> dict[str, Any]:
    """검사 → 가림. 토큰을 확인한 뒤에만 부른다(없는 링크로 검사 비용을 쓰게 하지 않는다)."""

    try:
        answers = schema.mask_answers(schema.validate_answers(raw))
    except schema.SurveyAnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(json.dumps(answers, ensure_ascii=False).encode()) > MAX_ANSWERS_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE)
    return answers


@router.get("/{token}")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def get_survey(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    survey = await _survey_for_token(db, token, for_update=True)
    now = datetime.now(UTC)
    survey.first_opened_at = survey.first_opened_at or now
    survey.last_opened_at = now
    survey.open_count = (survey.open_count or 0) + 1
    hospital = await db.get(Hospital, survey.hospital_id)
    view = survey_view(survey, hospital)
    await db.commit()
    return view


@router.post("/{token}/consent")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def give_consent(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    await _survey_for_token(db, token)
    body = await _read_body(request, ConsentBody)
    if body.consent_version != schema.CONSENT_VERSION:
        raise HTTPException(status_code=409, detail="동의 내용이 바뀌었습니다. 화면을 새로고침해 주세요.")
    if body.respondent not in schema.RESPONDENTS:
        raise HTTPException(status_code=422, detail="작성하시는 분을 골라 주세요.")
    survey = await _survey_for_token(db, token, for_update=True)
    now = datetime.now(UTC)
    if survey.consent_at is None or survey.consent_version != body.consent_version:
        survey.consent_at = now
        survey.consent_version = body.consent_version
        await write_audit_log(
            db,
            action="doctor_survey_consent",
            hospital_id=survey.hospital_id,
            actor=DOCTOR_SURVEY_ACTOR,
            target_type="doctor_survey",
            target_id=survey.id,
            detail={"consent_version": body.consent_version, "respondent": body.respondent},
        )
    survey.respondent = body.respondent
    await db.commit()
    return {"consented": True, "respondent": survey.respondent}


@router.put("/{token}/answers")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def save_answers(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    _require_consent(await _survey_for_token(db, token))
    body = await _read_body(request, AnswersBody)
    answers = _clean_answers(body.answers)
    survey = await _survey_for_token(db, token, for_update=True)
    _require_consent(survey)
    if _is_stale(survey, body):
        # 더 새 저장이 이미 들어왔다 — 이 요청은 늦게 도착한 옛 답이다.
        saved_at = _iso(survey.last_saved_at)
        await db.rollback()
        return {"stale": True, "saved_at": saved_at}
    now = datetime.now(UTC)
    _write_draft(survey, body, answers, now)
    await db.commit()
    return {
        "saved_at": now.isoformat(),
        "progress": schema.progress(answers, new_clinic=bool(survey.new_clinic)),
        "has_unsubmitted_changes": _has_unsubmitted_changes(survey),
    }


@router.post("/{token}/submit")
@limiter.limit(settings.PUBLIC_SITE_RATE_LIMIT)
async def submit_survey(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    # 병원 잠금 → 설문 행 잠금 순서. 설문 자료를 맞추는 일은 프로필 저장·Essence 조정과 같은 병원 잠금을 쓴다.
    preview = await _survey_for_token(db, token)
    _require_consent(preview)
    body = await _read_body(request, AnswersBody)
    answers = _clean_answers(body.answers)
    await acquire_hospital_advisory_lock(db, preview.hospital_id)
    survey = await _survey_for_token(db, token, for_update=True)
    _require_consent(survey)
    now = datetime.now(UTC)
    # 제출은 화면이 보낸 마지막 답이다. 순번만 앞으로 옮겨 늦게 도착한 옛 저장이 덮지 못하게 한다.
    _write_draft(survey, body, answers, now)
    result = await sync_doctor_survey_source(
        db,
        hospital_id=survey.hospital_id,
        answers=answers,
        new_clinic=bool(survey.new_clinic),
        source_id=survey.interview_source_id,
    )
    if result.source_id is not None:
        survey.interview_source_id = result.source_id
    survey.first_submitted_at = survey.first_submitted_at or now
    survey.last_submitted_at = now
    survey.submission_count = (survey.submission_count or 0) + 1
    survey.submitted_answers = answers
    await write_audit_log(
        db,
        action="doctor_survey_submit",
        hospital_id=survey.hospital_id,
        actor=DOCTOR_SURVEY_ACTOR,
        target_type="doctor_survey",
        target_id=survey.id,
        detail={
            "submission": survey.submission_count,
            "respondent": survey.respondent,
            "source_sync": result.status,
            "source_id": str(result.source_id) if result.source_id else None,
            "notes_added": result.notes_added,
            "notes_removed": result.notes_removed,
        },
    )
    await db.commit()
    if result.status in {"CREATED", "UPDATED", "CLEARED"}:
        # 커밋 뒤에만 부른다. 실패해도 15분 정기 조정이 설문 변경(T3)을 다시 집는다.
        enqueue_essence_refresh_best_effort(survey.hospital_id)
    return {
        "submitted_at": now.isoformat(),
        "submission_count": survey.submission_count,
        "progress": schema.progress(answers, new_clinic=bool(survey.new_clinic)),
    }
