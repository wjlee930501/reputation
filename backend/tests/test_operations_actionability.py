"""Operator-facing copy stays aligned with the controls the Admin actually exposes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin import operations_center_today_queries as today_queries
from app.api.admin.operations_center_serializers import next_onboarding_step
from app.models.hospital import Hospital
from app.services import notifier as legacy_notifier
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.readiness_operator_copy import readiness_next_actions
from app.workers import generation_incident_control


def test_generation_failure_names_customer_impact_and_existing_recovery_controls() -> None:
    # Given / When
    impact, action = generation_incident_control._generation_operator_copy("PROVIDER_TIMEOUT")

    # Then
    assert "병원 채널" in impact
    assert "작업 다시 시도" in action
    assert "개발팀 문의용 정보 복사" in action


def test_generation_prerequisites_do_not_promise_an_unavailable_retry() -> None:
    # Given / When
    _, essence_action = generation_incident_control._generation_operator_copy(
        "MISSING_APPROVED_ESSENCE"
    )
    _, cost_action = generation_incident_control._generation_operator_copy("COST_BLOCKED")
    _, lease_action = generation_incident_control._generation_operator_copy(
        "GENERATION_LEASE_ACTIVE"
    )

    # Then
    assert "운영 기준" in essence_action
    assert "보이면" in essence_action
    assert "비용·자동 작업 안전장치" in cost_action
    assert "중지 해제" in cost_action
    assert "계정 소유자" in cost_action
    assert "오늘 한도 2배" in cost_action
    assert "작업 다시 시도”를 누르세요" not in cost_action
    assert "기다린 뒤" in lease_action


def test_generation_safe_cause_hides_raw_provider_messages() -> None:
    assert generation_incident_control._generation_safe_cause("PROVIDER_TIMEOUT").endswith(
        "오지 않았습니다."
    )


def test_publication_blockers_name_the_exact_operator_recovery() -> None:
    expected = {
        "CONTENT_NOT_GENERATED": "01시·04시·07시·07시 45분",
        "MISSING_REFERENCES": "참고 자료",
        "FORBIDDEN_EXPRESSION": "의료광고 금지 표현",
        "ESSENCE_NOT_ALIGNED": "운영 기준",
        "CONTENT_IMAGE_NOT_READY": "대표 이미지 다시 생성",
    }

    for code, instruction in expected.items():
        impact, action = generation_incident_control._generation_operator_copy(code)
        cause = generation_incident_control._generation_safe_cause(code)

        assert "제때 공개되지 않습니다" in impact
        assert instruction in action
        assert "개발팀 문의용 정보 복사" in action
        assert cause.endswith("습니다.")
    assert "PROVIDER" not in generation_incident_control._generation_safe_cause(
        "PROVIDER_TIMEOUT"
    )


async def test_generation_incident_persists_korean_cause_instead_of_raw_message(
    monkeypatch,
) -> None:
    # Given
    captured = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def commit(self) -> None:
            return None

    async def capture_request(_db, request, **_kwargs):
        captured["request"] = request
        return SimpleNamespace(
            id=uuid.uuid4(),
            severity="HIGH",
            customer_impact=request.customer_impact,
            next_action=request.next_action,
            admin_path=request.admin_path,
            hospital_id=request.hospital_id,
            version=1,
        )

    async def accept_notification(_db, _intent) -> None:
        return None

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: FakeSession(),
    )
    monkeypatch.setattr(
        generation_incident_control, "open_or_touch_incident", capture_request
    )
    monkeypatch.setattr(
        generation_incident_control, "enqueue_notification", accept_notification
    )
    monkeypatch.setattr(
        generation_incident_control,
        "build_open_incident_notification",
        lambda _projection, _base_url: SimpleNamespace(),
    )

    # When
    await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital_name="테스트의원",
        run_id=uuid.uuid4(),
        code="PROVIDER_TIMEOUT",
        message="provider timed out: secret upstream detail",
    )

    # Then
    request = captured["request"]
    assert request.safe_error_message == "콘텐츠 생성 서비스의 응답이 제시간에 오지 않았습니다."
    assert "provider" not in request.safe_error_message


def test_today_queue_guidance_uses_the_content_check_link_for_both_states() -> None:
    # Given / When
    review_impact, review_action = today_queries._today_operator_copy(review=True)
    publish_impact, publish_action = today_queries._today_operator_copy(review=False)

    # Then
    assert "운영 검수" in review_impact
    assert "병원 채널" in publish_impact
    assert "콘텐츠 확인" in review_action
    assert "콘텐츠 확인" in publish_action


def test_report_queue_guides_the_operator_to_the_real_generation_control() -> None:
    # Given / When
    missing_impact, missing_action = report_queries._report_operator_copy("MISSING")
    pending_impact, pending_action = report_queries._report_operator_copy("DELIVERY_PENDING")

    # Then
    assert "원장 보고" in missing_impact
    assert "보고서 확인" in missing_action
    assert "리포트 생성" in missing_action
    assert "개발팀" in missing_action
    assert "원장 전달 검수" in pending_impact
    assert "보고서 확인" in pending_action


def test_onboarding_steps_name_the_exact_saved_or_verified_outcome() -> None:
    hospital = Hospital(id=uuid.uuid4(), name="테스트의원", slug="test-clinic")

    assert "병원 기본 정보 탭" in next_onboarding_step(hospital)
    assert "저장" in next_onboarding_step(hospital)
    hospital.profile_complete = True
    assert "초기 진단 리포트" in next_onboarding_step(hospital)
    assert "확인" in next_onboarding_step(hospital)
    hospital.v0_report_done = True
    assert "공개 정보" in next_onboarding_step(hospital)
    hospital.site_built = True
    assert "일정" in next_onboarding_step(hospital)
    assert "저장" in next_onboarding_step(hospital)
    hospital.schedule_set = True
    assert "도메인" in next_onboarding_step(hospital)
    assert "검증" in next_onboarding_step(hospital)


def test_readiness_guidance_always_names_customer_impact_and_support_fallback() -> None:
    # Given / When
    actions = readiness_next_actions()

    # Then
    assert len(actions) == 13
    assert all("개발팀" in action for action in actions.values())
    assert all("없으면" in action for action in actions.values())
    assert "“저장”" in actions["core_profile"]
    assert "병원 기본 정보 탭" in actions["core_profile"]
    assert all("프로파일" not in action for action in actions.values())
    assert "“근거 추출”" in actions["essence_sources"]
    assert "“승인”" in actions["essence_philosophy"]
    assert "“스케줄 저장 및 슬롯 생성”" in actions["schedule"]
    assert "“DNS 확인하고 운영 시작”" in actions["domain"]


def test_incident_payload_expands_unassigned_owner_and_missing_deadline() -> None:
    # Given
    incident = IncidentSlackProjection(
        incident_id=uuid.uuid4(),
        hospital_name="테스트의원",
        severity="HIGH",
        customer_impact="오늘 콘텐츠 공개가 늦어집니다.",
        next_action="운영 센터에서 조치하세요.",
        admin_path="/operations",
        owner_label="미지정",
        sla_label="확인 필요",
    )

    # When
    intent = build_open_incident_notification(incident, "https://admin.example.test")

    # Then
    payload = intent.message.payload_json()
    assert "담당: 미지정(담당자 지정 필요)" in payload
    assert "처리 기한: 운영 센터에서 확인" in payload
    assert "SLA" not in payload


async def test_auto_publish_slack_uses_plain_public_content_language(monkeypatch) -> None:
    # Given
    captured = {}

    async def capture_send(**payload):
        captured.update(payload)
        return True

    monkeypatch.setattr(legacy_notifier, "_send", capture_send)

    # When
    await legacy_notifier.notify_content_auto_published(
        hospital_name="테스트의원",
        title="진료 안내",
        content_type="FAQ",
        sequence_no=1,
        total_count=12,
        scheduled_date="2026-08-10",
        public_url="https://clinic.example.test/contents/1",
        admin_url="https://admin.example.test/hospitals/1/content",
    )

    # Then
    body = captured["blocks"][0]["text"]["text"]
    assert "공개 내용 확인 필요" in captured["text"]
    assert "Admin에서 공개 내용 확인" in body
    assert "후행" not in body
