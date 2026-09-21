"""Actionable Korean copy and bounded noise, without any live Slack transport."""

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services.content_publish_notifications import (
    build_generation_blocked_digest_intent,
    enqueue_generation_blocked_digest_sync,
)
from app.services.monthly_events import monthly_headline_label
from app.services.notification_contracts import (
    IncidentSlackProjection,
    NotificationPayloadError,
    validate_message,
)
from app.services.notification_messages import (
    build_open_incident_notification,
    build_recovered_incident_notification,
    build_summary_notification,
)
from app.services.notification_milestone_messages import (
    MilestoneBatch,
    MilestoneKind,
    MilestoneProjection,
    build_milestone_summary_notification,
)
from app.services.notification_milestone_rendering import operator_deadline
from app.workers.milestone_event_tasks import should_notify_milestone
from app.workers.milestone_monthly_facts import latest_report_facts


def incident(**overrides):
    values = dict(
        incident_id=uuid4(),
        hospital_name="검증 의원",
        severity="HIGH",
        customer_impact="공개 페이지가 열리지 않을 수 있습니다.",
        next_action="DNS를 확인하세요.",
        admin_path="/operations",
        owner_label="미지정",
        sla_label="확인 필요",
        incident_type="DOMAIN_UNHEALTHY",
        problem="연결 제한 시간 안에 응답하지 않았습니다.",
    )
    return IncidentSlackProjection(**{**values, **overrides})


def test_recovery_does_not_repeat_the_outage_or_assign_a_new_task():
    intent = build_recovered_incident_notification(incident(), "https://admin.example.test")
    body = intent.message.payload_json()
    assert "추가 조치가 필요하지 않습니다" in body
    assert "공개 페이지가 열리지" not in body
    assert "담당자 지정" not in body and "DNS를 확인" not in body
    assert "복구 기록 보기" in body


def test_content_findings_stay_in_admin_instead_of_becoming_slack_instructions():
    value = incident(
        incident_type="CONTENT_GENERATION_FAILED",
        problem='treatment_narratives {evidence_note_ids: "긴 모델 설명"}',
        next_action="계속 재생성하세요",
    )
    intent = build_open_incident_notification(value, "https://admin.example.test")
    text = intent.message.payload_json()
    assert "treatment_narratives" not in text and "계속 재생성" not in text
    assert "콘텐츠에서" in text and "근거" in text and "검증 의원" in intent.message.fallback_text
    assert len(intent.message.blocks) == 4
    assert "개발팀 문의용 정보 복사" not in text
    assert (
        build_open_incident_notification(
            replace(value, version=99), "https://admin.example.test"
        ).dedupe_key
        == intent.dedupe_key
    )
    assert (
        build_open_incident_notification(
            replace(value, episode_seq=2), "https://admin.example.test"
        ).dedupe_key
        != intent.dedupe_key
    )


@pytest.mark.parametrize("owner", ["미지정", "담당자 미배정", ""])
def test_missing_owner_is_not_a_fake_named_assignee(owner):
    text = build_open_incident_notification(
        incident(owner_label=owner), "https://admin.example.test"
    ).message.payload_json()
    assert "담당: 병원 운영 담당자" in text and "처리 기한 운영 센터에서 확인" not in text


def milestone(kind, *, actionable=False, period="2026년 8월"):
    return MilestoneProjection(
        f"milestone:v1:{uuid4()}",
        kind,
        uuid4(),
        "검증 의원",
        "레포트 전달 준비 완료",
        "검증된 파일이 준비됐습니다.",
        "고객용 PDF를 확인해 전달한 뒤 전달 기록을 남겨 주세요.",
        "담당 AE",
        "기한 없음",
        "/operations",
        actionable,
        False,
        period_label=period,
    )


@pytest.mark.parametrize(
    "kind",
    [
        MilestoneKind.HOSPITAL_ACTIVE,
        MilestoneKind.HANDOFF_ACCEPTED,
        MilestoneKind.MONTHLY_ARTIFACT_PENDING,
        MilestoneKind.MONTHLY_BLOCKED,
    ],
)
def test_automatic_progress_is_recorded_but_does_not_create_an_operator_message(kind):
    assert not should_notify_milestone(milestone(kind))


@pytest.mark.parametrize(
    "kind",
    [
        MilestoneKind.MONTHLY_CUSTOMER_READY,
        MilestoneKind.HANDOFF_OVERDUE,
        MilestoneKind.ACTIVATION_READY,
        MilestoneKind.DELIVERY_RESCINDED,
    ],
)
def test_human_handoffs_and_urgent_corrections_remain_visible(kind):
    assert should_notify_milestone(milestone(kind, actionable=True))


def test_monthly_summary_labels_actual_period_and_korean_local_time():
    event = milestone(MilestoneKind.MONTHLY_CUSTOMER_READY, actionable=True)
    batch = MilestoneBatch(
        (event,), datetime(2026, 9, 16, 23, 45, tzinfo=UTC), datetime(2026, 9, 17, tzinfo=UTC)
    )
    result = build_milestone_summary_notification(batch, "https://admin.example.test")
    body = result.message.payload_json()
    assert "2026년 8월" in body and "09/17 08:45 KST" in body
    assert "이번 달 결과" not in body and "기한 없음" not in body
    assert "검증 의원" in result.message.fallback_text and "전달 기록" in body


def test_old_report_versions_do_not_compete_with_current_truth():
    hospital = SimpleNamespace(id=uuid4())
    rows = [
        SimpleNamespace(
            hospital=hospital,
            report=SimpleNamespace(
                id=uuid4(),
                version=i,
                period_year=2026,
                period_month=8,
                created_at=datetime(2026, 9, 1, tzinfo=UTC),
            ),
        )
        for i in range(30)
    ]
    selected = latest_report_facts({item.report.id: item for item in reversed(rows)})
    assert selected == (rows[-1],)
    other = SimpleNamespace(
        hospital=hospital,
        report=SimpleNamespace(
            id=uuid4(),
            version=1,
            period_year=2026,
            period_month=7,
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )
    assert len(latest_report_facts({item.report.id: item for item in [*rows, other]})) == 2


def test_image_reuse_is_not_a_blocker_or_an_excuse_to_repeat_an_alert():
    db = Mock()
    reused = [
        {
            "hospital_id": uuid4(),
            "hospital_name": "검증 의원",
            "content_id": uuid4(),
            "image_failure_class": "POLICY_REJECTED",
        }
    ]
    assert (
        enqueue_generation_blocked_digest_sync(db, date(2026, 9, 17), "morning", [], reused) is None
    )
    db.execute.assert_not_called()
    blocked = [
        {
            "hospital_id": uuid4(),
            "hospital_name": "검증 의원",
            "content_id": uuid4(),
            "code": "GENERATION_REJECTED",
            "cause": "model_raw_field: 긴 근거 없는 설명",
        }
    ]
    first = build_generation_blocked_digest_intent(date(2026, 9, 17), "first", blocked)
    later = build_generation_blocked_digest_intent(date(2026, 9, 18), "later", blocked, reused)
    assert first.dedupe_key == later.dedupe_key
    assert "model_raw_field" not in first.message.payload_json()
    assert "대표 이미지 대체 발행" not in later.message.payload_json()
    validate_message(later.message, allowed_admin_base_url=later.message.admin_url)


def test_display_deadline_converts_utc_to_korean_time():
    assert operator_deadline(datetime(2026, 9, 16, 23, 0, tzinfo=UTC)) == "09/17 08:00 KST"


@pytest.mark.parametrize("value", [None, True, False, "47", float("nan"), float("inf"), -1, 101])
def test_report_headline_rejects_invalid_observations(value):
    assert monthly_headline_label({"sov_pct": value}) is None


@pytest.mark.parametrize(
    "comparison", [None, {}, {"status": "NON_COMPARABLE"}, {"significance": "SIGNIFICANT_UP"}]
)
def test_missing_comparison_proof_never_becomes_a_slack_performance_claim(comparison):
    text = monthly_headline_label({"sov_pct": 47, "prev_sov_pct": 39, "comparison": comparison})
    assert text == "확정 답변 100번 기준 47번 언급"


def test_explicit_comparison_uses_only_descriptive_change():
    text = monthly_headline_label(
        {
            "sov_pct": 47,
            "prev_sov_pct": 99,
            "comparison": {
                "status": "COMPARABLE",
                "prior_sov_pct": 39,
                "significance": "SIGNIFICANT_UP",
            },
        }
    )
    assert text == "확정 답변 100번 기준 47번 언급 · 전월 대비 +8번"
    assert "의미 있는" not in text


def test_developer_and_operator_incidents_cannot_be_mixed_in_one_message():
    start, end = datetime(2026, 9, 17, tzinfo=UTC), datetime(2026, 9, 18, tzinfo=UTC)
    with pytest.raises(NotificationPayloadError, match="SUMMARY_AUDIENCE_CONFLICT"):
        build_summary_notification(
            (incident(), incident(incident_type="BROKER_UNAVAILABLE")),
            start,
            end,
            "mixed",
            "https://admin.example.test",
        )
    high = build_open_incident_notification(
        incident(severity="CRITICAL"), "https://admin.example.test"
    )
    assert high.message.fallback_text.startswith("[Error : 오류 발생] [긴급]")


@pytest.mark.parametrize("closed,expected", [(False, False), (True, True)])
def test_final_report_failure_is_not_hidden_with_automatic_progress(closed, expected):
    from test_monthly_events import _monthly

    from app.models.monthly_control import ReportArtifactState
    from app.services.monthly_events import MonthlyEventType, project_monthly_event

    event = _monthly(
        MonthlyEventType.BLOCKED,
        manifest_closed=closed,
        coverage_final=False,
        delivery_ready=False,
        artifact_state=ReportArtifactState.MISSING,
        doctor_artifact_id=None,
        blocker_codes=("MEASUREMENT_FAILED",),
    )
    projection = project_monthly_event(event)
    assert projection.requires_action is expected
    assert should_notify_milestone(projection) is expected
    assert "아직 고객에게 전달할 수 없습니다" in projection.customer_impact


def test_large_group_stays_inside_slack_limits_and_omits_raw_findings():
    rows = [
        {
            "hospital_id": uuid4(),
            "hospital_name": "검증 의원 " + "가" * 150,
            "content_id": uuid4(),
            "code": "GENERATION_REJECTED",
            "cause": "internal_field: " + "나" * 900,
        }
        for _ in range(40)
    ]
    result = build_generation_blocked_digest_intent(date(2026, 9, 17), "test", rows)
    validate_message(result.message, allowed_admin_base_url=result.message.admin_url)
    assert "그 외 28곳" in result.message.payload_json()
    assert "internal_field" not in result.message.payload_json()
    assert all(
        len(block.get("text", {}).get("text", "")) <= 3000 for block in result.message.blocks
    )


def test_human_source_blocker_takes_precedence_over_pdf_progress():
    from app.services.monthly_events import MonthlyEventType
    from app.workers.milestone_monthly_projection import _current_state

    facts = SimpleNamespace(
        report=SimpleNamespace(),
        ready=False,
        artifact=None,
        blockers=("CURRENT_READINESS_BLOCKED",),
    )
    assert _current_state(facts) == MonthlyEventType.BLOCKED


@pytest.mark.asyncio
async def test_lead_message_rejects_an_external_action_link_and_mentions(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import notifier
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(notifier, "_send", sender)
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")
    await notifier.notify_lead_created(clinic_name="<@U123> <!channel> 검증 의원", contact="010-1234-5678",
                                      admin_url="https://unrelated.example.test/leads")
    payload = sender.await_args.kwargs
    assert len(payload["blocks"]) == 2
    assert payload["blocks"][1]["elements"][0]["url"] == "https://admin.example.test/operations?queue=INCIDENTS"
    assert "<@U123>" not in str(payload) and "<!channel>" not in str(payload)
    assert "010-1234-5678" not in str(payload)


@pytest.mark.asyncio
async def test_latest_report_preserves_old_delivery_correction(monkeypatch):
    from unittest.mock import AsyncMock

    from test_milestone_projector_postgres import _observed_facts

    from app.workers import milestone_monthly_projection as projector
    old = _observed_facts(quality="COMPLETE", sov_summary={"sov_pct": 20})
    new = _observed_facts(quality="COMPLETE", sov_summary={"sov_pct": 25})
    new = replace(new, hospital=old.hospital)
    old.report.version, new.report.version = 1, 2
    facts = {old.report.id: old, new.report.id: new}
    correction = replace(milestone(MilestoneKind.DELIVERY_CORRECTED), hospital_id=old.hospital.id)
    history = AsyncMock(return_value=(correction,))
    monkeypatch.setattr(projector, "load_report_facts", AsyncMock(return_value=facts))
    monkeypatch.setattr(projector, "_project_delivery_events", history)
    scan = await projector.observe_monthly_milestones(object(), datetime(2026, 9, 17, tzinfo=UTC), {}, datetime(2026, 9, 16, tzinfo=UTC))
    ready = [item for item in scan.milestones if item.kind is MilestoneKind.MONTHLY_CUSTOMER_READY]
    assert len(ready) == 1 and str(new.report.id) in ready[0].admin_path
    assert correction in scan.milestones
    assert old.report.id in history.await_args.args[1]
    assert len(scan.states) == 1


def test_headline_rejects_huge_integer_without_exception():
    assert monthly_headline_label({"sov_pct": 10 ** 1000}) is None
