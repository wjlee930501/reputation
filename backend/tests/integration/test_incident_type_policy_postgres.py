"""스윕 소유 사고 종류는 RETRYING+기한으로 열린다(Phase 1-1). 등록 밖 종류는 종전과 같다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.operations import IncidentSeverity
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)  # 12:00 KST, 다음 달(11월) 슬롯 준비 기간


def _request(incident_type: str = "MONTHLY_SLOT_GENERATION_FAILED", period: str = "2026-11"):
    object_id = f"{uuid.uuid4()}:{period}"
    return IncidentOpenRequest(
        pipeline="policy_test",
        object_type="hospital_month",
        object_id=object_id,
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type=incident_type,
        severity=IncidentSeverity.HIGH,
        customer_impact="테스트 영향",
        source_type="POLICY_TEST",
        next_action="테스트 조치",
        admin_path="/operations",
        source_id=object_id,
    )


async def _open(db, request, at):
    incident = await open_or_touch_incident(db, request, now=at)
    await db.flush()
    return incident


async def test_sweep_owned_type_opens_as_retrying_with_its_window(pg_async_session):
    incident = await _open(pg_async_session, _request(), NOW)

    assert incident.state == "RETRYING"
    assert incident.sla_due_at == NOW + timedelta(hours=24)


async def test_touch_inside_the_window_keeps_retrying_and_the_original_deadline(pg_async_session):
    request = _request()
    await _open(pg_async_session, request, NOW)

    touched = await _open(pg_async_session, request, NOW + timedelta(hours=6))

    assert touched.state == "RETRYING"
    assert touched.sla_due_at == NOW + timedelta(hours=24)
    assert touched.occurrence_count == 2


async def test_touch_after_the_window_hands_the_incident_to_people(pg_async_session):
    request = _request()
    await _open(pg_async_session, request, NOW)

    late = await _open(pg_async_session, request, NOW + timedelta(hours=25))

    assert late.state == "OPEN"


async def test_unregistered_type_keeps_todays_open_without_deadline(pg_async_session):
    incident = await _open(pg_async_session, _request("POLICY_TEST_UNREGISTERED"), NOW)

    assert incident.state == "OPEN"
    assert incident.sla_due_at is None


async def test_an_open_incident_is_never_demoted_by_a_later_touch(pg_async_session):
    request = _request()
    first = await _open(pg_async_session, request, NOW)
    first.state = "OPEN"  # 배포 전부터 사람이 가진 행
    await pg_async_session.flush()

    touched = await _open(pg_async_session, request, NOW + timedelta(hours=1))

    assert touched.state == "OPEN"


async def test_reopen_after_acknowledgement_starts_a_new_window(pg_async_session):
    request = _request()
    first = await _open(pg_async_session, request, NOW)
    first.state = "ACKNOWLEDGED"
    first.recovered_at = NOW
    first.acknowledged_at = NOW
    await pg_async_session.flush()

    reopened = await _open(pg_async_session, request, NOW + timedelta(days=2))

    assert reopened.state == "RETRYING"
    assert reopened.episode_seq == 2
    assert reopened.sla_due_at == NOW + timedelta(days=3)
