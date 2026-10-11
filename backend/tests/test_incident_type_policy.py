"""사고 종류 소유 정책 레지스트리(Phase 1-1) — 대상·조용함·스윕 소유·기한 창."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.operations import IncidentSeverity
from app.services.incident_types import (
    DEFAULT_RETRYING_DEADLINE,
    QUIET_INCIDENT_TYPES,
    SWEEP_OWNED_INCIDENT_TYPES,
    IncidentAudience,
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_audience,
    incident_is_quiet,
    incident_policy,
    retry_deadline,
)
from app.services.operator_action import is_operator_todo

KST = ZoneInfo("Asia/Seoul")
HOSPITAL = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _request(incident_type: str, source_id: str | None, retry_until: datetime | None = None):
    return IncidentOpenRequest(
        pipeline="policy_test",
        object_type="hospital_month",
        object_id=str(source_id),
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type=incident_type,
        severity=IncidentSeverity.HIGH,
        customer_impact="테스트",
        source_type="POLICY_TEST",
        next_action="테스트",
        admin_path="/operations",
        source_id=source_id,
        retry_until=retry_until,
    )


@pytest.mark.parametrize(
    "incident_type",
    [
        "BACKGROUND_TASK_FAILED",
        "OPERATION_TERMINAL_FAILED",
        "BROKER_UNAVAILABLE",
        "UNSAFE_STORED_DISPATCH",
        "OPERATION_RECOVERY_EXHAUSTED",
        "NOTIFICATION_DELIVERY_FAILED",
        "NOTIFICATION_DELIVERY_UNKNOWN",
        "CACHE_REVALIDATION_FAILED",
        "MONTHLY_DOCTOR_PDF_BLOCKED",
        "PUBLIC_SITE_UNAVAILABLE",
    ],
)
def test_existing_developer_types_keep_their_audience(incident_type):
    assert incident_audience(incident_type) is IncidentAudience.DEVELOPER


def test_existing_quiet_type_is_preserved():
    assert "POST_PUBLISH_REVIEW_FLAGGED" in QUIET_INCIDENT_TYPES
    assert incident_is_quiet("post_publish_review_flagged") is True


def test_unregistered_types_are_people_owned_and_open():
    policy = incident_policy("BRAND_NEW_TYPE")
    assert policy.audience is IncidentAudience.OPERATOR
    assert policy.quiet is False and policy.sweep_owned is False
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    assert retry_deadline(_request("BRAND_NEW_TYPE", "x"), episode_start=now, observed_at=now) is None


def test_sweep_owned_types_are_registered():
    assert {
        "MONTHLY_SLOT_GENERATION_FAILED",
        "MONTHLY_SOV_MEASUREMENT_FAILED",
        "SITE_BUILD_DISPATCH_FAILED",
        "CONTENT_DISPATCH_FAILED",
        "ESSENCE_AUTO_REVIEW_ESCALATED",
    } <= SWEEP_OWNED_INCIDENT_TYPES
    # 콘텐츠 생성은 자체 사다리와 아침 알림 계약이 있어 등록하지 않는다.
    assert "CONTENT_GENERATION_FAILED" not in SWEEP_OWNED_INCIDENT_TYPES


def test_next_month_slot_failure_gets_one_day_capped_at_month_start():
    start = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)  # 31일 18:00 KST
    deadline = retry_deadline(
        _request("MONTHLY_SLOT_GENERATION_FAILED", f"{HOSPITAL}:2026-11"),
        episode_start=start,
        observed_at=start,
    )
    assert deadline == datetime(2026, 11, 1, tzinfo=KST)


def test_current_month_slot_failure_waits_one_reconciliation():
    start = datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    deadline = retry_deadline(
        _request("MONTHLY_SLOT_GENERATION_FAILED", f"{HOSPITAL}:2026-10"),
        episode_start=start,
        observed_at=start,
    )
    assert deadline == start + DEFAULT_RETRYING_DEADLINE


def test_monthly_sov_deadline_is_the_recovery_horizon():
    from app.workers.tasks import _monthly_sov_recovery_deadline

    start = datetime(2026, 10, 25, tzinfo=UTC)
    request = _request("MONTHLY_SOV_MEASUREMENT_FAILED", f"{HOSPITAL}:2026-10")
    deadline = retry_deadline(request, episode_start=start, observed_at=start)
    assert deadline == datetime(2026, 11, 7, 23, 59, 59, tzinfo=KST)
    assert deadline == _monthly_sov_recovery_deadline(2026, 10)
    late = deadline + timedelta(seconds=1)
    assert retry_deadline(request, episode_start=start, observed_at=late) is None


def test_caller_supplied_deadline_wins_and_absence_means_people():
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    due = now + timedelta(hours=24)
    essence = "ESSENCE_AUTO_REVIEW_ESCALATED"
    assert retry_deadline(_request(essence, "h:s", due), episode_start=now, observed_at=now) == due
    assert retry_deadline(_request(essence, "h:s"), episode_start=now, observed_at=now) is None


def test_unparseable_period_is_people_owned():
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    request = _request("MONTHLY_SOV_MEASUREMENT_FAILED", "garbage")
    assert retry_deadline(request, episode_start=now, observed_at=now) is None


def test_every_sweep_owned_type_has_a_way_to_close():
    """스윕 소유 종류는 근거(resolver) 또는 명시적 복구 경로가 있어야 영원히 RETRYING에 남지 않는다."""

    from app.workers.incident_backlog import RESOLVERS

    # 명시적 복구 경로: 슬롯 재조정 성공(`monthly_slot_incident_control.recover_monthly_slot_failure`),
    # 운영 기준 승인(`ops_incident_alerts.recover_ops_incidents_for_hospital`, tasks의 Essence 처리).
    explicit_recovery = {"MONTHLY_SLOT_GENERATION_FAILED", "ESSENCE_AUTO_REVIEW_ESCALATED"}
    assert SWEEP_OWNED_INCIDENT_TYPES - set(RESOLVERS) <= explicit_recovery


@pytest.mark.parametrize(
    "incident_type",
    [
        "UNVERIFIED_ADMIN_ACTOR",
        "MONTHLY_SOV_COHORT_OVER_LIMIT",
        "SITE_BUILD_RETRIES_EXHAUSTED",
    ],
)
def test_work_an_ae_cannot_do_goes_to_the_developer(incident_type):
    assert incident_audience(incident_type) is IncidentAudience.DEVELOPER
    assert is_operator_todo(incident_type, "OPEN", None, datetime(2026, 10, 26, tzinfo=UTC)) is False


@pytest.mark.parametrize(
    "incident_type",
    ["SOV_HIGH_PRIORITY_CAP_EXCEEDED", "WEEKLY_SOV_MEASUREMENT_FAILED", "NOTIFICATION_DELIVERY_UNKNOWN"],
)
def test_state_only_kinds_are_quiet(incident_type):
    assert incident_is_quiet(incident_type) is True


def test_people_owned_kinds_stay_with_the_operator():
    # 비용 한도는 대표 결정이라 운영 큐에 남는다(D6). 자료 처리 실패는 유일한 신호라 강등하지 않는다(D7).
    # 사람이 누른 사이트 재시도의 실패(SITE_BUILD_FAILED)는 운영 담당의 일이다.
    for incident_type in (
        "COST_GUARD_LIMIT_REACHED",
        "SITE_BUILD_FAILED",
        "SOURCE_PROCESSING_FAILED",
        "MONTHLY_SOV_COHORT_GAP",
    ):
        assert incident_audience(incident_type) is IncidentAudience.OPERATOR
        assert incident_is_quiet(incident_type) is False
