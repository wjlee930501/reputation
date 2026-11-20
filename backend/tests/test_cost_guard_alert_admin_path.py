"""비용 한도 사고는 설정 화면 「자동 작업 한도」(/accounts)를 가리킨다(2026-11-20).

비용 안전장치 패널이 처리 목록에서 설정 화면으로 옮겨졌다. 새 사고는 그 경로·안내로 열리고,
옛 경로(/operations)·옛 안내로 저장된 사고도 읽는 시점에 지금 위치를 가리킨다.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.operations import IncidentSeverity, IncidentState
from app.services.incident_safety import (
    COST_GUARD_ADMIN_PATH,
    COST_GUARD_NEXT_ACTION,
    current_incident_guidance,
    normalize_admin_path,
    project_incident_labels,
)


def test_accounts_is_an_allowed_admin_path():
    assert normalize_admin_path("/accounts") == "/accounts"


def test_stored_cost_incident_points_at_the_settings_screen():
    old = SimpleNamespace(
        incident_type="COST_GUARD_LIMIT_REACHED",
        state=IncidentState.OPEN,
        severity=IncidentSeverity.HIGH,
        owner_id=None,
        sla_due_at=None,
        next_action="처리 목록의 비용 가드 사용량을 확인하고, 필요한 경우 킬스위치나 일일 상한을 조정한 뒤 차단된 작업을 재시도하세요.",
        admin_path="/operations",
    )
    labels = project_incident_labels(old, now=datetime.now(UTC))
    assert labels.admin_path == COST_GUARD_ADMIN_PATH
    assert "자동 작업 한도" in labels.next_action


def test_other_incidents_keep_their_guidance():
    assert current_incident_guidance("SITE_BUILD_FAILED", "그대로", "/hospitals/1") == ("그대로", "/hospitals/1")
    assert COST_GUARD_NEXT_ACTION.startswith("설정 화면")


def test_stored_cost_incident_slack_link_points_at_settings():
    import uuid

    from app.services.notification_contracts import IncidentSlackProjection
    from app.services.notification_messages import build_open_incident_notification

    projection = IncidentSlackProjection(
        incident_id=uuid.uuid4(),
        hospital_name="비용 한도",
        severity="HIGH",
        customer_impact="자동 AI 호출이 차단됩니다.",
        next_action="처리 목록의 비용 가드 사용량을 확인하세요.",
        admin_path="/operations",
        owner_label="담당자 미배정",
        sla_label="기한 내",
        incident_type="COST_GUARD_LIMIT_REACHED",
    )
    intent = build_open_incident_notification(projection, "https://admin.example.com")
    text = str(intent.message.blocks) + intent.message.fallback_text
    assert "https://admin.example.com/accounts" in text
    assert "자동 작업 한도" in text
    assert "/operations" not in text
