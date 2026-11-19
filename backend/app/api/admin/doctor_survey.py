"""Admin — 원장 사전 설문 링크와 진행 상태(병원 정보 탭의 '원장 인터뷰 설문' 카드).

AE가 링크를 만들어 복사해 원장에게 보낸다. 시스템은 링크를 원장에게 보내지 않고, 제출해도 Slack을
보내지 않는다 — 상태는 이 카드에서 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.doctor_survey import DoctorSurvey
from app.models.hospital import Hospital
from app.services import doctor_survey_files as survey_files
from app.services import doctor_survey_schema as schema
from app.services.audit_log import default_actor, write_audit_log
from app.services.doctor_survey_token import derive_survey_token, issue_survey_token, survey_url

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Doctor survey"])

STATUS_NOT_ISSUED = "NOT_ISSUED"
STATUS_NOT_OPENED = "NOT_OPENED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_SUBMITTED = "SUBMITTED"


class SurveyLinkBody(BaseModel):
    new_clinic: bool = False
    # 이미 링크가 있으면 True일 때만 새 링크를 만든다(옛 링크는 열리지 않는다).
    reissue: bool = False


def survey_status(survey: DoctorSurvey | None) -> str:
    if survey is None:
        return STATUS_NOT_ISSUED
    if survey.submission_count:
        return STATUS_SUBMITTED
    if survey.first_opened_at is None:
        return STATUS_NOT_OPENED
    return STATUS_IN_PROGRESS


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_survey(hospital_id: uuid.UUID, survey: DoctorSurvey | None) -> dict[str, Any]:
    if survey is None:
        return {"hospital_id": str(hospital_id), "status": STATUS_NOT_ISSUED, "link_url": None}
    answers = survey.answers or {}
    return {
        "hospital_id": str(hospital_id),
        "status": survey_status(survey),
        "link_url": survey_url(derive_survey_token(hospital_id, survey.token_version)),
        "link_issued_at": _iso(survey.link_issued_at),
        "link_issued_by": survey.link_issued_by,
        "new_clinic": bool(survey.new_clinic),
        "respondent": survey.respondent,
        "consented": survey.consent_at is not None,
        "first_opened_at": _iso(survey.first_opened_at),
        "last_opened_at": _iso(survey.last_opened_at),
        "last_saved_at": _iso(survey.last_saved_at),
        "first_submitted_at": _iso(survey.first_submitted_at),
        "last_submitted_at": _iso(survey.last_submitted_at),
        "submission_count": survey.submission_count,
        "has_unsubmitted_changes": bool(survey.submission_count)
        and answers != (survey.submitted_answers or {}),
        "progress": schema.progress(answers, new_clinic=bool(survey.new_clinic)),
        "interview_source_id": str(survey.interview_source_id) if survey.interview_source_id else None,
        # 원장이 올린 파일(답이 가리키는 것만). 사진 자료 등록은 AE가 분류·권리 근거와 함께 한다.
        "files": [
            {**item, "url": f"/api/admin/hospitals/{hospital_id}/doctor-survey/files/{item['id']}"}
            for item in survey_files.referenced_files(answers, survey.files)
        ],
        "photo_consent": answers.get("photo_consent") is True,
    }


async def _hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="병원을 찾을 수 없습니다.")
    return hospital


async def _survey(db: AsyncSession, hospital_id: uuid.UUID, *, for_update: bool = False) -> DoctorSurvey | None:
    stmt = select(DoctorSurvey).where(DoctorSurvey.hospital_id == hospital_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("/{hospital_id}/doctor-survey")
async def get_doctor_survey(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _hospital_or_404(db, hospital_id)
    return serialize_survey(hospital_id, await _survey(db, hospital_id))


@router.post("/{hospital_id}/doctor-survey/link")
async def issue_doctor_survey_link(
    hospital_id: uuid.UUID, body: SurveyLinkBody, db: AsyncSession = Depends(get_db)
):
    await _hospital_or_404(db, hospital_id)
    survey = await _survey(db, hospital_id, for_update=True)
    actor = default_actor()
    now = datetime.now(UTC)
    if survey is None:
        _raw, token_hash = issue_survey_token(hospital_id, 1)
        survey = DoctorSurvey(
            hospital_id=hospital_id,
            token_version=1,
            token_hash=token_hash,
            link_issued_at=now,
            link_issued_by=actor,
            new_clinic=body.new_clinic,
            answers={},
        )
        db.add(survey)
        await db.flush()
        action = "doctor_survey_link_issue"
    elif body.reissue:
        version = survey.token_version + 1
        _raw, token_hash = issue_survey_token(hospital_id, version)
        survey.token_version = version
        survey.token_hash = token_hash
        survey.link_issued_at = now
        survey.link_issued_by = actor
        survey.new_clinic = body.new_clinic
        action = "doctor_survey_link_reissue"
    else:
        survey.new_clinic = body.new_clinic
        action = "doctor_survey_link_update"
    await write_audit_log(
        db,
        action=action,
        hospital_id=hospital_id,
        actor=actor,
        target_type="doctor_survey",
        target_id=survey.id,
        detail={"token_version": survey.token_version, "new_clinic": survey.new_clinic},
    )
    await db.commit()
    await db.refresh(survey)
    return serialize_survey(hospital_id, survey)


@router.get("/{hospital_id}/doctor-survey/files/{file_id}")
async def get_doctor_survey_file(
    hospital_id: uuid.UUID, file_id: str, db: AsyncSession = Depends(get_db)
):
    from app.api.admin.essence import _asset_response

    survey = await _survey(db, hospital_id)
    entry = (survey.files or {}).get(file_id) if survey else None
    if not entry:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return _asset_response(
        entry["ref"],
        hospital_id=hospital_id,
        media_type=entry.get("mime"),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
