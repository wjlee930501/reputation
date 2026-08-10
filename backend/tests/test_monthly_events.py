from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.monthly_control import ReportArtifactState
from app.services.monthly_events import (
    MonthlyEvent,
    MonthlyEventType,
    MonthlyRunStage,
    monthly_run_operator_copy,
    project_monthly_event,
)
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import (
    MilestoneBatch,
    build_milestone_action_notification,
    build_milestone_recovery_notification,
    build_milestone_summary_notification,
)
from app.services.onboarding_events import (
    OnboardingEvent,
    OnboardingEventType,
    project_onboarding_event,
)

_ADMIN = "http://localhost:3000"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)
MonthlyValue = (
    str
    | uuid.UUID
    | datetime
    | MonthlyEventType
    | ReportArtifactState
    | int
    | bool
    | tuple[str, ...]
    | None
)


def _monthly(event_type: MonthlyEventType, **overrides: MonthlyValue) -> MonthlyEvent:
    values = {
        "event_id": uuid.UUID("a1310000-0000-0000-0000-000000000001"),
        "event_type": event_type,
        "report_id": uuid.UUID("c1310000-0000-0000-0000-000000000001"),
        "hospital_id": uuid.UUID("b1310000-0000-0000-0000-000000000001"),
        "hospital_name": "장편한외과의원",
        "period_year": 2026,
        "period_month": 7,
        "quality": "COMPLETE",
        "planned_count": 20,
        "success_count": 20,
        "failed_count": 0,
        "manifest_closed": True,
        "artifact_state": ReportArtifactState.VALID,
        "doctor_artifact_id": uuid.UUID("d1310000-0000-0000-0000-000000000001"),
        "delivery_ready": True,
        "blocker_codes": (),
        "owner_label": "담당 AE",
        "sla_due_at": _NOW + timedelta(hours=8),
        "occurred_at": _NOW,
    }
    values.update(overrides)
    return MonthlyEvent(**values)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {"artifact_state": ReportArtifactState.MISSING, "doctor_artifact_id": None},
            "CUSTOMER_READY_GATE_BLOCKED",
        ),
        ({"manifest_closed": False}, "CUSTOMER_READY_GATE_BLOCKED"),
        ({"delivery_ready": False}, "CUSTOMER_READY_GATE_BLOCKED"),
        ({"blocker_codes": ("CURRENT_ESSENCE_BLOCKED",)}, "CUSTOMER_READY_GATE_BLOCKED"),
    ],
)
def test_customer_ready_requires_closed_manifest_valid_doctor_artifact_and_server_gate(
    overrides: dict[str, MonthlyValue], code: str
) -> None:
    # Given: coverage is COMPLETE but one server-owned readiness fact is absent
    event = _monthly(MonthlyEventType.CUSTOMER_READY, **overrides)

    # When/Then: CUSTOMER_READY projection fails closed
    with pytest.raises(NotificationPayloadError, match=code):
        project_monthly_event(event)


def test_complete_coverage_without_artifact_is_internal_pending_not_customer_ready() -> None:
    # Given: the denominator is closed and complete, but doctor artifact validation is pending
    event = _monthly(
        MonthlyEventType.ARTIFACT_VALIDATION_PENDING,
        artifact_state=ReportArtifactState.MISSING,
        doctor_artifact_id=None,
        delivery_ready=False,
    )

    # When: it is projected for internal operations
    projection = project_monthly_event(event)

    # Then: it requires an internal action and never claims CUSTOMER_READY
    assert projection.kind.value == "MONTHLY_ARTIFACT_PENDING"
    assert projection.requires_action is True
    assert "CUSTOMER_READY" not in projection.stable_id


@pytest.mark.parametrize(
    ("stage", "expected_action"),
    [
        (MonthlyRunStage.QUEUED, "잠시 기다린 뒤 이 화면에서 진행 상태를 다시 확인해 주세요."),
        (MonthlyRunStage.RUNNING, "완료될 때까지 기다린 뒤 새 리포트를 검수해 주세요."),
        (
            MonthlyRunStage.BLOCKED,
            "운영 센터에서 차단 사유를 확인하고 해결한 뒤 ‘리포트 다시 만들기’를 눌러 주세요.",
        ),
        (
            MonthlyRunStage.ARTIFACT_VALIDATION_PENDING,
            "원장 전달용 PDF를 열어 글자·페이지·내용을 확인해 주세요.",
        ),
        (
            MonthlyRunStage.ARTIFACT_VALIDATED,
            "리포트 화면에서 최신 자료와 전달 가능 상태를 확인해 주세요.",
        ),
        (
            MonthlyRunStage.FAILED,
            "‘리포트 다시 만들기’를 눌러 주세요. 다시 실패하면 ‘개발팀 문의용 정보 복사’로 전달해 주세요.",
        ),
    ],
)
def test_monthly_run_copy_is_plain_korean_and_actionable(
    stage: MonthlyRunStage, expected_action: str
) -> None:
    # Given: an internal monthly execution stage
    # When: it is projected for a non-technical operator
    copy = monthly_run_operator_copy(stage)

    # Then: it explains the issue, impact, and exact next action without raw terms
    assert copy.next_action == expected_action
    rendered = " ".join((copy.what_happened, copy.customer_impact, copy.next_action))
    assert all(term not in rendered for term in ("SLA", "CUSTOMER_READY", "PARTIAL"))


def test_artifact_validated_stage_does_not_claim_final_customer_readiness() -> None:
    copy = monthly_run_operator_copy(MonthlyRunStage.ARTIFACT_VALIDATED)

    assert copy.status_label == "원장 전달용 PDF 검증 완료"
    assert "전달할 수 있습니다" not in " ".join(
        (copy.what_happened, copy.customer_impact, copy.next_action)
    )


@pytest.mark.parametrize(
    "event_type",
    [
        MonthlyEventType.DELIVERY_CORRECTED,
        MonthlyEventType.DELIVERY_RESCINDED,
        MonthlyEventType.DELIVERY_REDELIVERED,
    ],
)
def test_delivery_history_events_project_even_if_current_readiness_later_changes(
    event_type: MonthlyEventType,
) -> None:
    # Given: an append-only delivery event remains true after current readiness changes
    event = _monthly(
        event_type,
        delivery_ready=False,
        blocker_codes=("CURRENT_ESSENCE_BLOCKED",),
    )

    # When: the persisted delivery history is projected
    projection = project_monthly_event(event)

    # Then: history is not erased or blocked by newer readiness facts
    assert projection.kind.value == event_type.value


@pytest.mark.parametrize(
    ("event_type", "status_fragment", "impact_fragment", "action_text", "forbidden_claims"),
    [
        (
            MonthlyEventType.DELIVERY_CORRECTED,
            "기록 추가",
            "실제 전달 여부",
            "수정된 전달 기록 확인",
            ("전달 완료", "전달됐습니다"),
        ),
        (
            MonthlyEventType.DELIVERY_RESCINDED,
            "기록 무효 처리",
            "이미 보낸 파일은 회수되지 않습니다",
            "무효 처리 기록 확인",
            ("파일 회수 완료", "파일이 회수됐습니다"),
        ),
        (
            MonthlyEventType.DELIVERY_REDELIVERED,
            "기록 추가",
            "실제 수신 여부",
            "재전달 기록 확인",
            ("재전달 완료", "다시 전달됐습니다"),
        ),
    ],
)
def test_delivery_history_copy_describes_records_without_claiming_transport(
    event_type: MonthlyEventType,
    status_fragment: str,
    impact_fragment: str,
    action_text: str,
    forbidden_claims: tuple[str, ...],
) -> None:
    event = _monthly(event_type)
    projection = project_monthly_event(event)
    intent = (
        build_milestone_action_notification(projection, _ADMIN)
        if projection.requires_action
        else build_milestone_recovery_notification(projection, _ADMIN)
    )
    payload_json = intent.message.payload_json()

    visible_copy = " ".join(
        (projection.status_label, projection.customer_impact, projection.next_action)
    )
    assert status_fragment in projection.status_label
    assert impact_fragment in projection.customer_impact
    assert all(claim not in visible_copy for claim in forbidden_claims)
    assert intent.dedupe_key.endswith(projection.stable_id)
    assert action_text in payload_json
    assert projection.stable_id not in payload_json
    assert "복구 대상" not in payload_json
    if projection.is_recovery:
        assert projection.recovery_of == f"report:{event.report_id}:delivery"


def test_blocked_monthly_action_and_link_point_to_the_same_report_work() -> None:
    event = _monthly(
        MonthlyEventType.BLOCKED,
        quality="BLOCKED",
        success_count=3,
        failed_count=17,
        artifact_state=ReportArtifactState.MISSING,
        doctor_artifact_id=None,
        delivery_ready=False,
        blocker_codes=("MEASUREMENT_FAILED",),
    )
    projection = project_monthly_event(event)
    intent = build_milestone_action_notification(projection, _ADMIN)
    payload_json = intent.message.payload_json()

    assert projection.admin_path == (
        f"/hospitals/{event.hospital_id}/reports?report={event.report_id}"
    )
    assert "리포트 화면에서 차단 사유" in projection.next_action
    assert "개발팀 문의용 정보 복사" in projection.next_action
    assert "차단 사유 확인" in payload_json
    assert f"{_ADMIN}{projection.admin_path}" in payload_json


def test_mixed_daily_summary_has_stable_constituents_and_one_operations_link() -> None:
    # Given: one overdue handoff and one blocked monthly report in the same window
    overdue = project_onboarding_event(
        OnboardingEvent(
            event_id=uuid.UUID("a1310000-0000-0000-0000-000000000010"),
            event_type=OnboardingEventType.HANDOFF_OVERDUE,
            hospital_id=uuid.UUID("b1310000-0000-0000-0000-000000000010"),
            hospital_name="한결의원",
            owner_label="담당 AE",
            occurred_at=_NOW,
            sla_due_at=_NOW - timedelta(hours=2),
        )
    )
    blocked = project_monthly_event(
        _monthly(
            MonthlyEventType.BLOCKED,
            event_id=uuid.UUID("a1310000-0000-0000-0000-000000000011"),
            quality="BLOCKED",
            planned_count=20,
            success_count=3,
            failed_count=17,
            artifact_state=ReportArtifactState.MISSING,
            doctor_artifact_id=None,
            delivery_ready=False,
            blocker_codes=("MEASUREMENT_FAILED",),
        )
    )

    # When: order is reversed across two builds
    first = build_milestone_summary_notification(
        MilestoneBatch((overdue, blocked), _NOW, _NOW + timedelta(days=1)), _ADMIN
    )
    second = build_milestone_summary_notification(
        MilestoneBatch((blocked, overdue), _NOW, _NOW + timedelta(days=1)), _ADMIN
    )

    # Then: one deterministic summary hides internal IDs and has exactly one deep link
    payload_json = first.message.payload_json()
    assert first.dedupe_key == second.dedupe_key
    assert first.message.fallback_text.startswith("무슨 문제인지: 운영 마일스톤 2건")
    assert all(
        label in first.message.fallback_text
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:", "처리 기한:")
    )
    assert overdue.stable_id not in payload_json
    assert blocked.stable_id not in payload_json
    assert payload_json.count(f"{_ADMIN}/operations") == 1
    assert len(first.message.blocks) <= 50
    assert json.loads(payload_json)["text"] == first.message.fallback_text
    assert all(
        label in payload_json
        for label in ("무슨 문제인지:", "고객 영향:", "지금 할 일:")
    )
    assert "처리 기한:" in payload_json
    assert "SLA:" not in payload_json
    assert "T08:00:00" not in payload_json
    assert "관련 작업 모아보기" in payload_json
    assert "MEASUREMENT_FAILED" not in payload_json
    assert "HANDOFF_OVERDUE" not in payload_json
    assert "BLOCKED" not in payload_json
