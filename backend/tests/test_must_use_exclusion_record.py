"""금지 표현 때문에 원문 요구에서 뺀 승인본 필수 문구는 조용히 사라지지 않는다.

제외할 때마다 구조화 경고 로그(병원·승인본 버전·문구 위치·걸린 금지 표현)가 남고, 운영자가
보는 기존 인시던트 경로에 승인본 버전 하나당 한 건만 기록된다. 같은 버전을 다시 검수·생성해도
두 번째 기록(재오픈·중복 알림)은 없다. 새 버전은 새 기록이다.

독립 검수 진입점(`review_generated_content`)을 실제로 태우고 공급자만 가짜로 바꾼다.
인시던트는 격리된 테스트 PostgreSQL(`INCIDENT_TEST_DATABASE_URL`)의 롤백 트랜잭션에 쓴다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.core.database as database
from app.models.hospital import Hospital
from app.models.operations import Incident, NotificationOutbox
from app.services import content_ai_review, ops_incident_alerts
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from tests.db_env import fail_unreachable, require_db_url
from tests.must_use_review_support import install_fake_reviewer, verdict
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

FORBIDDEN_MESSAGE = "저희 병원은 대장암 완치를 보장합니다."
ALLOWED_MESSAGE = "국립암센터는 50대 이상에게 5~10년 주기의 대장내시경 검사를 권고하고 있습니다."
_URL_ENV = "INCIDENT_TEST_DATABASE_URL"


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


@pytest.fixture
async def db(monkeypatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(require_db_url(_URL_ENV), future=True)
    try:
        try:
            connection = await engine.connect()
        except OSError as exc:
            fail_unreachable(_URL_ENV, exc)
        transaction = await connection.begin()
        ready = await connection.scalar(text("SELECT to_regclass('public.incidents') IS NOT NULL"))
        if not ready:
            await transaction.rollback()
            await connection.close()
            pytest.fail("incident PostgreSQL must include the operations schema", pytrace=False)
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

        class _Scope:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_exc):
                return False

        # 서비스가 여는 전역 세션을 이 테스트의 롤백 트랜잭션으로 돌린다.
        monkeypatch.setattr(database, "get_async_sessionmaker", lambda: _Scope)
        monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: _Scope)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
            await connection.close()
    finally:
        await engine.dispose()


def _philosophy(version: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="APPROVED",
        version=version,
        must_use_messages=[ALLOWED_MESSAGE, FORBIDDEN_MESSAGE],
        avoid_messages=[],
        medical_ad_risk_rules=[],
        positioning_statement="",
        doctor_voice="",
        content_principles=[],
        treatment_narratives=[],
    )


def _candidate() -> dict:
    return {
        "title": "대장내시경 검사 주기",
        "body": f"대장내시경은 선종을 찾는 검사입니다. {ALLOWED_MESSAGE}",
        "meta_description": "대장내시경 검사 주기 안내",
        "faq_question": "",
        "faq_answer_summary": "",
        "references": [],
    }


async def _hospital(db: AsyncSession) -> SimpleNamespace:
    suffix = uuid.uuid4().hex[:12]
    hospital = Hospital(
        id=uuid.uuid4(),
        name=f"필수문구 제외 기록 {suffix}",
        slug=f"must-use-excluded-{suffix}",
        region=["서울"],
        specialties=["내과"],
        treatments=["대장내시경"],
    )
    db.add(hospital)
    await db.commit()
    # 검수에는 값만 넘긴다. 세션 객체는 `_incidents`의 expire_all 뒤 동기 지연 로드가 된다.
    return SimpleNamespace(id=hospital.id, name=hospital.name)


def _dedupe_key(philosophy_id: uuid.UUID) -> str:
    return build_incident_key(
        "essence_must_use",
        "content_philosophy",
        str(philosophy_id),
        IncidentFingerprint.VALIDATION_FAILED,
    )


async def _incidents(db: AsyncSession, hospital_id: uuid.UUID) -> list[Incident]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(Incident)
                .where(Incident.hospital_id == hospital_id)
                .order_by(Incident.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _review(hospital: SimpleNamespace, philosophy: SimpleNamespace) -> None:
    review = await content_ai_review.review_generated_content(
        hospital=hospital,
        philosophy=philosophy,
        content=_candidate(),
        content_brief={"must_use_messages": list(philosophy.must_use_messages)},
    )
    assert review.status == content_ai_review.ContentAiReviewStatus.PASS


def _exclusion_logs(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "must_use_message_excluded"
    ]


async def test_excluded_message_logs_and_records_once_per_essence_version(
    db, monkeypatch, caplog
) -> None:
    install_fake_reviewer(monkeypatch, [verdict() | {"decision": "PASS", "findings": []}])
    hospital = await _hospital(db)
    caplog.set_level(logging.WARNING)

    # 대조군: 제외할 문구가 없는 승인본은 로그도 기록도 남기지 않는다.
    clean = _philosophy(3)
    clean.must_use_messages = [ALLOWED_MESSAGE]
    await _review(hospital, clean)
    assert _exclusion_logs(caplog) == []
    assert await _incidents(db, hospital.id) == []

    version_4 = _philosophy(4)
    await _review(hospital, version_4)

    [log] = _exclusion_logs(caplog)
    assert log.levelno == logging.WARNING
    assert log.hospital_id == str(hospital.id)
    assert log.philosophy_id == str(version_4.id)
    assert log.essence_version == 4
    assert log.must_use_index == 1
    assert log.forbidden_expressions
    # 로그는 문구 원문을 싣지 않는다 — 위치와 걸린 표현만.
    assert FORBIDDEN_MESSAGE not in log.getMessage()

    [incident] = await _incidents(db, hospital.id)
    assert incident.dedupe_key == _dedupe_key(version_4.id)
    assert incident.state == "OPEN"
    assert incident.admin_path == f"/hospitals/{hospital.id}/info"
    assert "v4" in incident.safe_error_message
    assert "2번째" in incident.safe_error_message
    first_version = incident.version
    outbox_count = await db.scalar(
        select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.incident_id == incident.id
        )
    )

    # 같은 승인본 버전을 다시 검수해도 로그는 남고 기록은 그대로 한 건이다(재오픈·알림 없음).
    caplog.clear()
    await _review(hospital, version_4)
    assert len(_exclusion_logs(caplog)) == 1
    [same] = await _incidents(db, hospital.id)
    assert same.id == incident.id
    assert same.version == first_version
    assert same.occurrence_count == incident.occurrence_count
    assert await db.scalar(
        select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.incident_id == incident.id
        )
    ) == outbox_count

    # 운영자가 확인 처리한 뒤에도 같은 버전은 다시 열지 않는다.
    closed_at = datetime.now(UTC)
    same.state = "ACKNOWLEDGED"
    same.recovered_at = closed_at
    same.acknowledged_at = closed_at
    await db.commit()
    await _review(hospital, version_4)
    [acknowledged] = await _incidents(db, hospital.id)
    assert acknowledged.state == "ACKNOWLEDGED"

    # 새 승인본 버전은 새 기록이다.
    version_5 = _philosophy(5)
    await _review(hospital, version_5)
    incidents = await _incidents(db, hospital.id)
    assert [item.dedupe_key for item in incidents] == [
        _dedupe_key(version_4.id),
        _dedupe_key(version_5.id),
    ]
