"""Operator-facing copy stays aligned with the controls the Admin actually exposes."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.api.admin import operations_center_report_queries as report_queries
from app.api.admin import operations_center_today_queries as today_queries
from app.api.admin.operations_center_serializers import next_onboarding_step
from app.models.hospital import Hospital
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.post_publish_review_policy import (
    auto_publish_due_predicate,
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)
from app.services.readiness_operator_copy import readiness_next_actions
from app.workers import generation_incident_control


def test_today_queue_and_post_publish_sampling_share_automatic_operation_boundaries() -> None:
    operational_sql = str(
        publicly_operational_hospital_predicate().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    review_sql = str(
        human_post_publish_review_predicate().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "hospitals.status = 'ACTIVE'" in operational_sql
    assert "hospitals.site_live IS true" in operational_sql
    assert "content_items.sequence_no = 1" in review_sql
    assert "content_items.body_updated_at > content_items.published_at" in review_sql
    # Remediation alone no longer pulls an item into the sample — a rewrite the automatic
    # safety gate already applied is evidence the gate worked, not a reason to re-sample it.
    assert "automatic_remediation_attempts" not in review_sql


def test_generation_failure_names_customer_impact_and_one_recovery_control() -> None:
    # Given / When
    impact, action = generation_incident_control._generation_operator_copy("PROVIDER_TIMEOUT")

    # Then
    assert "병원 채널" in impact
    assert "자동으로 다시 시도" in action
    assert "지금은 기다리세요" in action
    assert "개발팀 문의용 정보 복사" not in action


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
    assert "한 번 승인" in essence_action
    assert "승인 전에는 재시도할 필요가 없" in essence_action
    assert "자동으로 다시 생성" in essence_action
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
        assert "개발팀 문의용 정보 복사" not in action
        assert cause.endswith("습니다.")
    assert "PROVIDER" not in generation_incident_control._generation_safe_cause("PROVIDER_TIMEOUT")


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

        async def scalar(self, _statement):
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
            safe_error_code=request.safe_error_code,
            safe_error_message=request.safe_error_message,
            episode_seq=1,
        )

    async def accept_notification(_db, _intent) -> None:
        return None

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: FakeSession(),
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "enqueue_notification", accept_notification)
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


async def test_generation_gate_inherits_open_legacy_episode_without_repaging(
    monkeypatch,
) -> None:
    hospital_id = uuid.uuid4()
    legacy = SimpleNamespace(state="OPEN")
    scalar_results = iter((None, legacy))
    captured = {"opened": 0, "notified": 0}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def commit(self) -> None:
            return None

        async def scalar(self, _statement):
            return next(scalar_results)

    async def capture_request(_db, request, **_kwargs):
        captured["opened"] += 1
        return SimpleNamespace(
            id=uuid.uuid4(),
            severity="MEDIUM",
            customer_impact=request.customer_impact,
            next_action=request.next_action,
            admin_path=request.admin_path,
            hospital_id=request.hospital_id,
            version=1,
            safe_error_code=request.safe_error_code,
            safe_error_message=request.safe_error_message,
        )

    async def capture_notification(_db, _intent) -> None:
        captured["notified"] += 1

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: FakeSession(),
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "enqueue_notification", capture_notification)

    await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=hospital_id,
        hospital_name="테스트의원",
        run_id=uuid.uuid4(),
        code="MISSING_APPROVED_ESSENCE",
        message="raw detail",
    )

    assert captured == {"opened": 1, "notified": 0}


def test_open_generation_episode_can_wake_at_morning_cutoff_once() -> None:
    should_send = generation_incident_control._should_send_generation_notification

    assert should_send(
        notify_requested=True,
        previous_state="OPEN",
        notification_already_enqueued=False,
    )
    assert not should_send(
        notify_requested=True,
        previous_state="OPEN",
        notification_already_enqueued=True,
    )


async def test_open_cause_pages_its_korean_gate_instead_of_empty_slot_symptom(
    monkeypatch,
) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 8, 18, 22, 45, tzinfo=UTC)
            return value if tz is None else value.astimezone(tz)

    cause_id = uuid.uuid4()
    cause = SimpleNamespace(
        id=cause_id,
        state="OPEN",
        severity="HIGH",
        customer_impact="예정 공개 콘텐츠가 제때 공개되지 않습니다.",
        next_action="운영 센터에서 가격·지역·검색 구조 게이트를 확인하세요.",
        admin_path="/operations",
        hospital_id=uuid.uuid4(),
        version=1,
        episode_seq=1,
        safe_error_code="GENERATION_REJECTED",
        safe_error_message="가격·지역·검색 구조 자동 검수 게이트가 통과되지 않았습니다.",
    )
    scalar_results = iter((None, cause, None))
    captured = {"notifications": []}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def scalar(self, _statement):
            return next(scalar_results)

        async def get(self, _model, _item_id):
            return SimpleNamespace(
                scheduled_date=datetime(2026, 8, 19).date(),
                body=None,
                image_url=None,
            )

        async def commit(self):
            return None

    async def fake_open(_db, request, **_kwargs):
        raise AssertionError("the stored gate must reuse the exact open cause")

    async def capture_notification(_db, intent):
        captured["notifications"].append(intent)

    monkeypatch.setattr(
        generation_incident_control, "get_async_sessionmaker", lambda: lambda: FakeSession()
    )
    monkeypatch.setattr(generation_incident_control, "datetime", FrozenDatetime)
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(generation_incident_control, "enqueue_notification", capture_notification)

    await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital_name="테스트의원",
        run_id=uuid.uuid4(),
        code="CONTENT_NOT_GENERATED",
        message="발행 시각까지 제목·본문 미준비",
        notify=True,
    )

    assert len(captured["notifications"]) == 1
    payload = captured["notifications"][0].message.payload_json()
    assert "가격·지역·검색 구조 자동 검수 게이트" in payload
    assert "발행 시각까지 콘텐츠 제목과 본문" not in payload


def test_generation_projection_uses_the_specific_safe_cause() -> None:
    incident = SimpleNamespace(
        id=uuid.uuid4(),
        severity="MEDIUM",
        customer_impact="예정 공개가 늦어질 수 있습니다.",
        next_action="운영 기준을 승인하세요.",
        admin_path="/operations",
        hospital_id=uuid.uuid4(),
        version=1,
        safe_error_code="MISSING_APPROVED_ESSENCE",
        safe_error_message="승인된 콘텐츠 운영 기준이 없어 자동 생성을 시작하지 않았습니다.",
        episode_seq=1,
    )

    projection = generation_incident_control._projection(
        incident,
        "테스트의원",
        uuid.uuid4(),
        "병원 운영 담당자",
        "예정 공개 전",
    )

    assert projection.problem == "승인된 콘텐츠 운영 기준이 없어 자동 생성을 시작하지 않았습니다."
    assert projection.owner_label == "병원 운영 담당자"
    assert projection.sla_label == "예정 공개 전"


def test_generation_gate_is_hospital_scoped_and_paged_once_per_state_episode() -> None:
    hospital_id = uuid.uuid4()
    first = generation_incident_control._incident_identity(
        "MISSING_APPROVED_ESSENCE", uuid.uuid4(), hospital_id
    )
    second = generation_incident_control._incident_identity(
        "MISSING_APPROVED_ESSENCE", uuid.uuid4(), hospital_id
    )

    assert first == second
    assert first == ("hospital", str(hospital_id), f"/hospitals/{hospital_id}/essence")
    assert (
        generation_incident_control._generation_severity("MISSING_APPROVED_ESSENCE").value
        == "MEDIUM"
    )
    assert generation_incident_control._should_send_generation_notification(
        notify_requested=True,
        previous_state=None,
    )
    assert not generation_incident_control._should_send_generation_notification(
        notify_requested=True,
        previous_state="OPEN",
        notification_already_enqueued=True,
    )


def test_generation_incident_pages_once_until_it_recovers() -> None:
    should_send = generation_incident_control._should_send_generation_notification

    assert should_send(notify_requested=True, previous_state=None)
    assert not should_send(
        notify_requested=True,
        previous_state="OPEN",
        notification_already_enqueued=True,
    )
    assert not should_send(notify_requested=False, previous_state=None)
    assert should_send(notify_requested=True, previous_state="RECOVERED")
    assert not should_send(notify_requested=True, previous_state="ACKNOWLEDGED")


async def test_acknowledged_generation_cause_keeps_the_same_episode(monkeypatch) -> None:
    incident_id = uuid.uuid4()
    acknowledged = SimpleNamespace(
        id=incident_id,
        state="ACKNOWLEDGED",
        safe_error_code="GENERATION_REJECTED",
        episode_seq=4,
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return None

        async def scalar(self, _statement):
            return acknowledged

    async def reject_reopen(*_args, **_kwargs):
        raise AssertionError("ACKNOWLEDGED must not reopen the same slot and cause")

    monkeypatch.setattr(
        generation_incident_control, "get_async_sessionmaker", lambda: lambda: FakeSession()
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", reject_reopen)

    result = await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital_name="테스트의원",
        run_id=uuid.uuid4(),
        code="GENERATION_REJECTED",
        message="raw gate detail",
        notify=True,
    )

    assert result == incident_id
    assert acknowledged.state == "ACKNOWLEDGED"
    assert acknowledged.episode_seq == 4


def test_generation_notification_candidates_wait_for_morning_readiness_proof() -> None:
    assert generation_incident_control._IMMEDIATE_GENERATION_NOTIFICATION_CODES == frozenset()

    morning_codes = (
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_REJECTED",
        "GENERATION_FAILED",
        "CONTENT_NOT_GENERATED",
        "FORBIDDEN_EXPRESSION",
        "ESSENCE_NOT_ALIGNED",
        "MISSING_REFERENCES",
        "CONTENT_IMAGE_NOT_READY",
        "IMAGE_GENERATION_FAILED",
        "GENERATION_LEASE_ACTIVE",
        "STALE_GENERATION_CLAIM",
    )
    for code in morning_codes:
        assert generation_incident_control.generation_notify_requested(code)
    assert not generation_incident_control.generation_notify_requested("COST_BLOCKED")
    assert not generation_incident_control.generation_notify_requested(
        "MISSING_APPROVED_ESSENCE"
    )


def test_human_now_generation_pages_once_per_episode_then_again_after_recovery() -> None:
    should_send = generation_incident_control._should_send_generation_notification
    code = "GENERATION_FAILED"

    assert generation_incident_control.generation_notify_requested(code)
    assert should_send(notify_requested=True, previous_state=None, code=code)
    assert not should_send(
        notify_requested=True,
        previous_state="OPEN",
        code=code,
        notification_already_enqueued=True,
    )
    assert not should_send(
        notify_requested=True,
        previous_state="RETRYING",
        code=code,
        notification_already_enqueued=True,
    )
    assert should_send(notify_requested=True, previous_state="RECOVERED", code=code)


def test_morning_cutoff_requires_due_date_and_exact_missing_artifact() -> None:
    due = SimpleNamespace(
        scheduled_date=datetime(2026, 8, 19).date(),
        body=None,
        image_url=None,
    )
    before = datetime(2026, 8, 18, 22, 44, tzinfo=UTC)  # 07:44 KST
    cutoff = datetime(2026, 8, 18, 22, 45, tzinfo=UTC)  # 07:45 KST

    for code in ("PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "GENERATION_REJECTED"):
        assert not generation_incident_control._morning_notification_due(
            code=code, item=due, observed_at=before
        )
        assert generation_incident_control._morning_notification_due(
            code=code, item=due, observed_at=cutoff
        )

    due.body = "이미 저장된 본문"
    assert not generation_incident_control._morning_notification_due(
        code="PROVIDER_TIMEOUT", item=due, observed_at=cutoff
    )
    assert generation_incident_control._morning_notification_due(
        code="CONTENT_IMAGE_NOT_READY", item=due, observed_at=cutoff
    )
    due.image_url = "https://cdn.example.test/image.jpg"
    assert not generation_incident_control._morning_notification_due(
        code="CONTENT_IMAGE_NOT_READY", item=due, observed_at=cutoff
    )

    due.scheduled_date = datetime(2026, 8, 20).date()
    due.body = None
    assert not generation_incident_control._morning_notification_due(
        code="GENERATION_REJECTED", item=due, observed_at=cutoff
    )


def test_generation_notification_has_one_developer_fallback() -> None:
    impact, action = generation_incident_control._generation_operator_copy("GENERATION_REJECTED")
    incident = IncidentSlackProjection(
        incident_id=uuid.uuid4(),
        hospital_name="테스트의원",
        severity="HIGH",
        customer_impact=impact,
        next_action=action,
        admin_path="/operations",
        owner_label="병원 운영 담당자",
        sla_label="예정 공개 전",
        problem="콘텐츠 생성 서비스가 이번 요청을 처리하지 못했습니다.",
        incident_type="CONTENT_GENERATION_FAILED",
    )

    payload = build_open_incident_notification(
        incident, "https://admin.example.test"
    ).message.payload_json()

    assert payload.count("개발팀 문의용 정보 복사") == 1


def test_today_queue_guidance_uses_the_content_check_link_for_both_states() -> None:
    # Given / When
    review_impact, review_action = today_queries._today_operator_copy(review=True)
    publish_impact, publish_action = today_queries._today_operator_copy(review=False)

    # Then
    assert "운영 검수" in review_impact
    assert "병원 채널" in publish_impact
    assert "콘텐츠 확인" in review_action
    assert "콘텐츠 확인" in publish_action


_TODAY = date(2026, 8, 19)


def test_todays_publication_slot_is_not_operator_work_before_the_eight_am_publisher() -> None:
    # Given: 07:30 KST — the automatic publisher has not run yet
    before_publisher = datetime(2026, 8, 18, 22, 30, tzinfo=UTC)

    # Then: today's slot is context, a slot whose date already passed is real work
    assert not today_queries.publish_due_requires_operator_action(
        _TODAY, _TODAY, before_publisher
    )
    assert today_queries.publish_due_requires_operator_action(
        _TODAY - timedelta(days=1), _TODAY, before_publisher
    )


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 8, 18, 23, 0, tzinfo=UTC),  # 08:00 KST
        datetime(2026, 8, 19, 4, 0, tzinfo=UTC),  # 13:00 KST
    ],
)
def test_publication_slot_becomes_operator_work_once_the_publisher_has_had_its_turn(
    moment: datetime,
) -> None:
    # Given / When: at or after 08:00 KST the slot should have been published

    # Then: today's unpublished slot counts as operator work again
    assert today_queries.publish_due_requires_operator_action(_TODAY, _TODAY, moment)


def test_due_publish_query_predicate_never_drops_the_pre_eight_am_rows() -> None:
    """The fold is a per-row flag, not a WHERE clause — the row must stay in the page.

    Dropping it from the query removed it from `total` too, so the FE could not
    collapse a row it never received and the count silently disagreed with the list.
    """
    sql = str(
        auto_publish_due_predicate(_TODAY).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.scheduled_date <= '2026-08-19'" in sql
    assert "content_items.scheduled_date < '2026-08-19'" not in sql


def test_report_queue_guides_the_operator_to_the_real_generation_control() -> None:
    # Given / When
    missing_impact, missing_action = report_queries._report_operator_copy("MISSING")
    pending_impact, pending_action = report_queries._report_operator_copy("DELIVERY_PENDING")
    hospital_id = uuid.uuid4()
    missing_control = report_queries._report_action(
        hospital_id,
        state="MISSING",
        year=2026,
        month=7,
    )
    pending_control = report_queries._report_action(
        hospital_id,
        state="DELIVERY_PENDING",
        year=2026,
        month=7,
    )

    # Then
    assert "원장 보고" in missing_impact
    assert "지난달 리포트 생성" in missing_action
    assert "진행 상태" in missing_action
    assert missing_control.kind == "GENERATE_MONTHLY_REPORT"
    assert missing_control.method == "POST"
    assert missing_control.requires_idempotency_key is True
    assert missing_control.reason_required is True
    assert missing_control.path == (
        f"/hospitals/{hospital_id}/operations/generate-monthly-report?year=2026&month=7"
    )
    assert "원장 전달 검수" in pending_impact
    assert "보고서 확인" in pending_action
    assert pending_control.kind == "OPEN_REPORT"
    assert pending_control.method == "GET"


def test_report_queue_uses_historical_service_interval_scope() -> None:
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    period_end = datetime(2026, 8, 1, tzinfo=UTC)

    sql = str(
        report_queries._eligible_hospital_ids_stmt(period_start, period_end).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "hospital_service_intervals" in sql
    assert "started_at <" in sql
    assert "ended_at IS NULL" in sql
    assert "ended_at >" in sql
    assert "hospitals.status" not in sql
    assert "hospitals.created_at" not in sql


def test_report_queue_does_not_hide_stale_monthly_runs() -> None:
    now = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)

    sql = str(
        report_queries._fresh_active_monthly_run_predicate(now).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "coalesce" in sql.lower()
    assert "heartbeat_at" in sql
    assert "started_at" in sql
    assert "queued_at" in sql
    assert "requested_at" in sql
    assert str(now - timedelta(hours=1)).replace("+00:00", "")[:19] in sql


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
    assert "도메인" in next_onboarding_step(hospital)
    assert "검증" in next_onboarding_step(hospital)
    hospital.site_live = True
    assert "근거 자료 처리" in next_onboarding_step(hospital)
    assert "운영 기준 자동 승인" in next_onboarding_step(hospital)
    assert "일정" in next_onboarding_step(hospital)
    assert "저장" in next_onboarding_step(hospital)
    hospital.schedule_set = True
    assert "첫 발행" in next_onboarding_step(hospital)


def test_readiness_guidance_names_real_controls_without_dead_end_button_copy() -> None:
    # Given / When
    actions = readiness_next_actions()

    # Then
    assert len(actions) == 13
    assert all("해당 버튼이 없으면" not in action for action in actions.values())
    assert all("개발팀에 병원명" not in action for action in actions.values())
    assert "“저장”" in actions["core_profile"]
    assert "병원 기본 정보 탭" in actions["core_profile"]
    assert all("프로파일" not in action for action in actions.values())
    assert "“근거 추출”" in actions["essence_sources"]
    assert "시스템 자동 검수" in actions["essence_philosophy"]
    assert "보류된 예외만" in actions["essence_philosophy"]
    assert "“승인”을 누르세요" not in actions["essence_philosophy"]
    assert "“스케줄 저장 및 슬롯 생성”" in actions["schedule"]
    assert "“DNS 확인하고 운영 시작”" in actions["domain"]
    assert "지금 발행" not in actions["published_content"]
    assert "스케줄 탭" in actions["published_content"]
    assert "예약 콘텐츠" in readiness_next_actions(has_content_slots=True)["published_content"]


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
        incident_type="CONTENT_GENERATION_FAILED",
    )

    # When
    intent = build_open_incident_notification(incident, "https://admin.example.test")

    # Then
    payload = intent.message.payload_json()
    assert "담당: 미지정(담당자 지정 필요)" in payload
    assert "처리 기한: 운영 센터에서 확인" in payload
    assert "SLA" not in payload
