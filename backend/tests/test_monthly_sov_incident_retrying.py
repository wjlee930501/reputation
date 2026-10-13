"""월초 1~7일 catch-up이 소유한 측정 실패는 사람의 일이 아니다(2026-10 감사 F5-①, Phase 1-6a).

기한·상태는 사고 종류 레지스트리(`incident_types._monthly_sov_window`)가 정한다. 여기서는
그 기한이 8일 최종화 뒤 마지막 판정 시각까지 이어지는지, 사람 몫 원인은 처음부터 사람의 일인지,
기계가 들고 있던 에피소드가 사람 몫으로 바뀌면 알림이 나가는지를 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.operations import Incident, IncidentSeverity
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest, retry_deadline
from app.services.monthly_period import (
    KST,
    monthly_final_judgment_at,
    monthly_recovery_deadline,
)
from app.workers import weekly_sov_incident_control as control

HOSPITAL = uuid.UUID("22222222-2222-2222-2222-222222222222")
DEADLINE_SEP = datetime(2026, 10, 7, 23, 59, 59, tzinfo=KST)
JUDGMENT_SEP = datetime(2026, 10, 8, 9, 0, tzinfo=KST)


def _request(code: str | None, period: str = "2026-09") -> IncidentOpenRequest:
    source = f"{HOSPITAL}:{period}"
    return IncidentOpenRequest(
        pipeline="monthly_sov",
        object_type="hospital_month",
        object_id=source,
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type="MONTHLY_SOV_MEASUREMENT_FAILED",
        severity=IncidentSeverity.HIGH,
        customer_impact="테스트",
        source_type="MONTHLY_SOV_MEASUREMENT",
        next_action="테스트",
        admin_path="/operations",
        hospital_id=HOSPITAL,
        source_id=source,
        safe_error_code=code,
    )


def test_recovery_deadline_is_the_last_second_of_day_seven_kst():
    assert monthly_recovery_deadline(2026, 9) == DEADLINE_SEP
    assert monthly_recovery_deadline(2026, 12) == datetime(2027, 1, 7, 23, 59, 59, tzinfo=KST)


def test_final_judgment_is_after_the_day_eight_finalize():
    # 8일 00:15 월간 배치가 마감 사실로 최종화하고, 09:00 공백 요약이 마지막으로 판정한다.
    assert monthly_final_judgment_at(2026, 9) == JUDGMENT_SEP
    assert monthly_final_judgment_at(2026, 12) == datetime(2027, 1, 8, 9, 0, tzinfo=KST)
    assert monthly_final_judgment_at(2026, 9) > monthly_recovery_deadline(2026, 9) + timedelta(
        minutes=16
    )


def test_tasks_recovery_horizon_delegates_to_the_service_function():
    from app.workers.tasks import _monthly_sov_recovery_deadline

    assert _monthly_sov_recovery_deadline(2026, 9) == monthly_recovery_deadline(2026, 9)


@pytest.mark.parametrize(
    "code",
    [
        "MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
        "MONTHLY_SOV_MEASUREMENT_PARTIAL",
        "MONTHLY_SOV_COST_GUARD_BLOCKED",
    ],
)
def test_machine_owned_failures_stay_retrying_through_the_day_eight_finalize(code):
    day_one = datetime(2026, 10, 1, 0, 20, tzinfo=KST)
    window = datetime(2026, 9, 26, 6, 0, tzinfo=KST)
    # 7일 23:59:59 직후 ~ 8일 00:15 최종화 사이에 '조치 필요'가 먼저 나가면 오탐이다.
    finalize = datetime(2026, 10, 8, 0, 15, tzinfo=KST)

    for at in (day_one, window, finalize):
        assert retry_deadline(_request(code), episode_start=at, observed_at=at) == JUDGMENT_SEP


@pytest.mark.parametrize(
    "code",
    [
        "MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
        "MONTHLY_SOV_MEASUREMENT_POLICY_DRIFT",
        "MONTHLY_SOV_UNRESOLVED_MANIFEST_STATE",
    ],
)
def test_human_failures_open_immediately(code):
    at = datetime(2026, 10, 1, 0, 20, tzinfo=KST)
    assert retry_deadline(_request(code), episode_start=at, observed_at=at) is None


def test_after_the_final_judgment_nothing_is_machine_owned():
    late = JUDGMENT_SEP + timedelta(seconds=1)
    request = _request("MONTHLY_SOV_MEASUREMENT_INCOMPLETE")
    assert retry_deadline(request, episode_start=late, observed_at=late) is None


def test_malformed_period_is_not_machine_owned():
    at = datetime(2026, 10, 1, tzinfo=KST)
    request = _request("MONTHLY_SOV_MEASUREMENT_INCOMPLETE", period="2026-W40")
    assert retry_deadline(request, episode_start=at, observed_at=at) is None


class _DB:
    def __init__(self, previous_state):
        self.previous_state = previous_state

    async def scalar(self, _stmt):
        if self.previous_state is None:
            return None
        return SimpleNamespace(state=self.previous_state)

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _patch(monkeypatch, *, previous_state, opened_state):
    enqueued = []

    async def fake_open(_db, request, **_kwargs):
        return Incident(
            id=uuid.uuid4(), state=opened_state, severity=request.severity.value, version=2,
            episode_seq=1, customer_impact=request.customer_impact,
            next_action=request.next_action, admin_path=request.admin_path,
            hospital_id=request.hospital_id, operation_run_id=request.operation_run_id,
            safe_error_message=request.safe_error_message,
            incident_type=request.incident_type,
        )

    async def fake_enqueue(_db, intent, **_kwargs):
        enqueued.append(intent)

    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: (lambda: _DB(previous_state)))
    monkeypatch.setattr(control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(control, "enqueue_notification", fake_enqueue)
    return enqueued


async def _open(code: str) -> None:
    await control.open_monthly_sov_failure(
        hospital_id=HOSPITAL, hospital_name="테스트의원", period_key="2026-09", error_code=code
    )


async def test_human_code_after_a_machine_episode_pages_once(monkeypatch):
    """비용 보류로 시작한(알림을 만들지 않은) 에피소드가 사람 몫 원인으로 바뀌면 처음 알린다."""

    enqueued = _patch(monkeypatch, previous_state="RETRYING", opened_state="OPEN")

    await _open("MONTHLY_SOV_NO_MEASUREMENT_MANIFEST")

    assert [intent.notification_type for intent in enqueued] == ["INCIDENT_OPEN"]


async def test_a_touch_that_stays_machine_owned_does_not_page_again(monkeypatch):
    enqueued = _patch(monkeypatch, previous_state="RETRYING", opened_state="RETRYING")

    await _open("MONTHLY_SOV_MEASUREMENT_INCOMPLETE")

    assert enqueued == []


async def test_cost_guard_never_pages_even_when_handed_to_people(monkeypatch):
    enqueued = _patch(monkeypatch, previous_state="RETRYING", opened_state="OPEN")

    await _open("MONTHLY_SOV_COST_GUARD_BLOCKED")

    assert enqueued == []


def test_registry_window_matches_the_service_judgment_time():
    """레지스트리는 바닥 모듈이라 같은 시각을 자체 계산한다 — 두 정의가 어긋나지 않게 묶는다."""

    for year, month in ((2026, 9), (2026, 12), (2027, 2)):
        request = _request("MONTHLY_SOV_MEASUREMENT_INCOMPLETE", period=f"{year}-{month:02d}")
        start = datetime(year, month, 24, tzinfo=UTC)
        assert retry_deadline(request, episode_start=start, observed_at=start) == (
            monthly_final_judgment_at(year, month)
        )
