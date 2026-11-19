"""원장 사전 설문 — 실제 Postgres에서 링크 발급 → 동의 → 저장 → 제출 → 다시 제출 → 새 링크."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.public import doctor_survey as public_survey
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.audit import AdminAuditLog
from app.models.essence import (
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.services import doctor_survey_schema as schema
from app.services.doctor_survey_source import DOCTOR_SURVEY_SYSTEM_KIND
from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "DATABASE_URL"
ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine(require_db_url(_URL_ENV))
    try:
        connection = await engine.connect()
    except OSError as exc:
        await engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
async def client(db: AsyncSession, monkeypatch):
    refreshes: list[uuid.UUID] = []
    monkeypatch.setattr(public_survey, "enqueue_essence_refresh_best_effort", refreshes.append)

    async def override_get_db():
        yield db

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            http.refreshes = refreshes  # type: ignore[attr-defined]
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter


async def _hospital(db: AsyncSession) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    await db.execute(
        text("INSERT INTO hospitals (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": hospital_id, "name": "설문 검증 정형외과의원", "slug": f"survey-{hospital_id.hex[:12]}"},
    )
    await db.commit()
    return hospital_id


def _token(link_url: str) -> str:
    return link_url.rsplit("/survey/", 1)[1]


ANSWERS = {
    "clinic_name": "설문 검증 정형외과의원",
    "opening_story": "대학병원에서 수술 대기만 몇 달씩 하는 환자들을 보며 동네에서 빨리 진료하고 싶었습니다.",
    "principle": "검사 화면을 같이 보며 설명한 다음 치료를 정합니다.",
    "features": ["자세한 설명"],
}


async def _survey_notes(db: AsyncSession, source_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = (
        await db.execute(
            select(HospitalSourceEvidenceNote).where(HospitalSourceEvidenceNote.source_asset_id == source_id)
        )
    ).scalars()
    return {row.note_metadata["label"]: row.id for row in rows}


async def test_doctor_writes_submits_edits_and_an_old_link_stops_working(db, client):
    hospital_id = await _hospital(db)
    base = f"/api/v1/admin/hospitals/{hospital_id}/doctor-survey"

    # AE가 링크를 만든다(새로 개원한 병원).
    status = (await client.get(base, headers=ADMIN)).json()
    assert status["status"] == "NOT_ISSUED"
    issued = await client.post(f"{base}/link", headers=ADMIN, json={"new_clinic": True})
    assert issued.status_code == 200, issued.text
    link = issued.json()["link_url"]
    assert issued.json()["status"] == "NOT_OPENED"
    token = _token(link)
    public = f"/api/v1/public/doctor-survey/{token}"

    # 없는 링크는 404, 열면 개인 응답이라 캐시하지 않는다.
    assert (await client.get("/api/v1/public/doctor-survey/nope")).status_code == 404
    opened = await client.get(public)
    assert opened.status_code == 200
    assert opened.headers["cache-control"] == "no-store"
    body = opened.json()
    assert body["hospital_name"] == "설문 검증 정형외과의원"
    assert body["new_clinic"] is True and body["consented"] is False
    assert body["schema"]["version"] == schema.SCHEMA_VERSION
    assert (await client.get(base, headers=ADMIN)).json()["status"] == "IN_PROGRESS"

    # 동의 전에는 저장하지 않는다.
    payload = {"answers": ANSWERS, "current_step": "q|approach", "schema_version": schema.SCHEMA_VERSION}
    assert (await client.put(f"{public}/answers", json=payload)).status_code == 409
    consent = await client.post(
        f"{public}/consent", json={"consent_version": schema.CONSENT_VERSION, "respondent": "DIRECTOR"}
    )
    assert consent.status_code == 200
    saved = await client.put(f"{public}/answers", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["progress"]["answered"] >= 3
    # 작성 중인 답은 근거 자료가 아니다.
    sources = (
        await db.execute(select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id))
    ).scalars().all()
    assert sources == []
    bad = await client.put(f"{public}/answers", json={"answers": {"wait_time": "하루"}})
    assert bad.status_code == 422

    # 제출하면 설문 자료 한 행이 결정적 노트와 함께 생기고 Essence 자동 요약을 부른다.
    submitted = await client.post(f"{public}/submit", json=payload)
    assert submitted.status_code == 200, submitted.text
    assert client.refreshes == [hospital_id]
    source = (
        await db.execute(select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id))
    ).scalar_one()
    assert source.source_type == SourceType.INTERVIEW
    assert source.status == SourceStatus.PROCESSED
    assert source.source_metadata["system_kind"] == DOCTOR_SURVEY_SYSTEM_KIND
    assert "설문 검증 정형외과의원" not in source.raw_text
    first_notes = await _survey_notes(db, source.id)
    assert set(first_notes) == {"개원 계기와 지역 선택 이유", "첫 진료 원칙", "AI 답변에 넣을 병원 특징"}
    status = (await client.get(base, headers=ADMIN)).json()
    assert status["status"] == "SUBMITTED" and status["has_unsubmitted_changes"] is False

    # 사람은 이 자료를 고치거나 뺄 수 없다(원장이 링크에서 고친다).
    patched = await client.patch(
        f"/api/v1/admin/hospitals/{hospital_id}/essence/sources/{source.id}",
        headers=ADMIN,
        json={"raw_text": "바꿈"},
    )
    assert patched.status_code == 409
    assert patched.json()["detail"]["code"] == "DOCTOR_SURVEY_READ_ONLY"

    # 고쳐 저장하면 '제출 뒤 고친 내용'이 있고, 다시 제출하면 같은 행을 제자리에서 맞춘다.
    edited = {**ANSWERS, "principle": "환자가 이해할 때까지 설명한 다음 치료를 정합니다."}
    await client.put(f"{public}/answers", json={"answers": edited})
    assert (await client.get(base, headers=ADMIN)).json()["has_unsubmitted_changes"] is True
    again = await client.post(f"{public}/submit", json={"answers": edited})
    assert again.status_code == 200
    assert again.json()["submission_count"] == 2
    await db.refresh(source)
    assert "환자가 이해할 때까지" in source.raw_text
    assert "검사 화면을 같이 보며" not in source.raw_text
    second_notes = await _survey_notes(db, source.id)
    assert second_notes["개원 계기와 지역 선택 이유"] == first_notes["개원 계기와 지역 선택 이유"]
    assert second_notes["첫 진료 원칙"] != first_notes["첫 진료 원칙"]
    count = (
        await db.execute(select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id))
    ).scalars().all()
    assert len(count) == 1

    # 새 링크를 만들면 옛 링크는 열리지 않고, 답은 그대로 이어진다.
    reissued = await client.post(f"{base}/link", headers=ADMIN, json={"new_clinic": True, "reissue": True})
    new_token = _token(reissued.json()["link_url"])
    assert new_token != token
    assert (await client.get(public)).status_code == 404
    reopened = (await client.get(f"/api/v1/public/doctor-survey/{new_token}")).json()
    assert reopened["answers"]["principle"].startswith("환자가 이해할 때까지")
    assert reopened["consented"] is True

    actions = (
        await db.execute(
            select(AdminAuditLog.action).where(AdminAuditLog.hospital_id == hospital_id).order_by(AdminAuditLog.created_at)
        )
    ).scalars().all()
    assert "doctor_survey_link_issue" in actions
    assert "doctor_survey_consent" in actions
    assert actions.count("doctor_survey_submit") == 2
    assert "doctor_survey_link_reissue" in actions


async def test_copying_the_link_again_returns_the_same_url(db, client):
    hospital_id = await _hospital(db)
    base = f"/api/v1/admin/hospitals/{hospital_id}/doctor-survey"
    first = (await client.post(f"{base}/link", headers=ADMIN, json={"new_clinic": False})).json()
    again = (await client.post(f"{base}/link", headers=ADMIN, json={"new_clinic": True})).json()
    shown = (await client.get(base, headers=ADMIN)).json()
    assert first["link_url"] == again["link_url"] == shown["link_url"]
    assert shown["new_clinic"] is True
