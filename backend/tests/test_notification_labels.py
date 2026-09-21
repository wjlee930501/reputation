"""알림 종류 라벨 계약 — 모든 `#mkt-reputation` 투영의 첫 토큰을 종류별로 고정한다.

여기서 확인하는 것은 세 가지다. 라벨 문구가 한 글자도 바뀌지 않는다. 종류마다 표본
메시지의 fallback text와 Block Kit header가 **같은** 라벨로 시작한다. 라벨을 붙이면서
행동 안내·버튼·`OPS-` 참조를 유지하고 정상 자동 처리 알림은 억제한다.
"""

from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.models.operations import Incident
from app.services import notifier
from app.services.content_batch_messages import (
    ContentBatchSummary,
    build_content_batch_message,
)
from app.services.content_publish_notifications import (
    build_generation_blocked_digest_intent,
    build_generation_rejection_weekly_rollup_intent,
    build_missing_approved_essence_digest_intent,
    build_publish_notification_intent,
)
from app.services.cost_guard import _build_cost_alert_intent
from app.services.fleet_heartbeat import FleetFacts, build_fleet_heartbeat
from app.services.monthly_report_gap_notifications import (
    MonthlyReportGap,
    build_monthly_report_gap_summary,
)
from app.services.naver_handoff_contracts import NaverHandoffItem, NaverHandoffState
from app.services.naver_handoff_incidents import NaverIncidentContext, _recovery_intent
from app.services.naver_handoff_messages import NaverWeeklyEntry, build_naver_weekly_digest
from app.services.notification_contracts import IncidentSlackProjection, SlackMessage
from app.services.notification_labels import (
    ERROR_LABEL,
    EVENT_LABELS,
    LEAD_LABEL,
    REPORT_LABEL,
    NotificationLabel,
    label_for_event,
    prefixed,
    prefixed_for_event,
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
    build_milestone_action_notification,
    build_milestone_recovery_notification,
    build_milestone_summary_notification,
)
from app.services.onboarding_notifications import (
    build_hospital_activated_notification,
    build_site_built_notification,
    build_v0_ready_notification,
)
from app.services.pipeline_watchdog import (
    AUDIENCE_DEVELOPER,
    AUDIENCE_OPERATOR,
    WatchdogReport,
    build_alert_text,
    build_recovery_text,
)

_ADMIN = "http://localhost:3000"
_NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
_HOSPITAL = uuid.UUID("b1000000-0000-0000-0000-000000000001")


def test_label_wording_is_exact() -> None:
    # 이 세 문자열은 채널 계약이다. 공백·콜론·괄호를 포함해 그대로 유지한다.
    assert LEAD_LABEL == "[Lead : 도입 문의]"
    assert ERROR_LABEL == "[Error : 오류 발생]"
    assert REPORT_LABEL == "[Report : 운영 현황 보고]"
    assert {label.value for label in NotificationLabel} == {
        LEAD_LABEL,
        ERROR_LABEL,
        REPORT_LABEL,
    }


def test_every_registered_kind_maps_to_one_of_the_three_labels() -> None:
    assert EVENT_LABELS
    assert all(isinstance(label, NotificationLabel) for label in EVENT_LABELS.values())


def test_unregistered_kind_still_delivers_with_the_error_label() -> None:
    # 라벨이 없다고 전달을 실패시키지 않는다. 분류 누락은 채널에서 보이게 남긴다.
    assert label_for_event("SOME_NEW_KIND_2026") is NotificationLabel.ERROR


def test_prefix_keeps_the_sentence_and_never_doubles_the_label() -> None:
    once = prefixed(NotificationLabel.REPORT, "무슨 문제인지: 확인 필요")
    assert once == f"{REPORT_LABEL} 무슨 문제인지: 확인 필요"
    assert prefixed(NotificationLabel.ERROR, once) == once
    assert prefixed_for_event("INCIDENT_OPEN", "확인 필요") == f"{ERROR_LABEL} 확인 필요"


def _notification_type_literals() -> set[str]:
    """`notification_type=`로 넘어가는 문자열을 소스에서 직접 모은다.

    새 알림 종류를 추가하면서 라벨 등록을 잊는 것이 이 계약이 깨지는 유일한 현실적인
    경로다. 런타임 호출을 기다리지 않고 소스에서 잡는다.
    """

    def strings(node: ast.expr) -> set[str]:
        match node:
            case ast.Constant(value=str() as literal):
                return {literal}
            case ast.IfExp(body=body, orelse=orelse):
                return strings(body) | strings(orelse)
            case _:
                return set()

    found: set[str] = set()
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assigned: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            values = strings(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name) and values:
                    assigned.setdefault(target.id, set()).update(values)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "notification_type":
                    continue
                found |= strings(keyword.value)
                if isinstance(keyword.value, ast.Name):
                    found |= assigned.get(keyword.value.id, set())
    return found


def test_every_enqueued_notification_type_is_registered() -> None:
    literals = _notification_type_literals()
    # 스캔이 헐거워지면 이 계약은 아무것도 지키지 못한다 — 알려진 종류부터 확인한다.
    assert {"INCIDENT_OPEN", "INCIDENT_RECOVERED", "FLEET_HEARTBEAT"} <= literals
    assert literals - set(EVENT_LABELS) == set()


def _incident(**overrides: object) -> IncidentSlackProjection:
    projection = IncidentSlackProjection(
        incident_id=uuid.UUID("a1000000-0000-0000-0000-000000000001"),
        hospital_name="장편한외과의원",
        severity="HIGH",
        customer_impact="콘텐츠 발행이 지연되고 있습니다.",
        next_action="재시도를 확인해 주세요.",
        admin_path="/operations?state=OPEN",
        owner_label="김효진 팀장",
        sla_label="오늘 18:00",
        hospital_id=_HOSPITAL,
        incident_type="CONTENT_GENERATION_FAILED",
    )
    return replace(projection, **overrides) if overrides else projection


def _milestone(*, requires_action: bool, recovery: bool = False) -> MilestoneProjection:
    return MilestoneProjection(
        stable_id="HANDOFF_OVERDUE:1",
        kind=MilestoneKind.HANDOFF_OVERDUE,
        hospital_id=_HOSPITAL,
        hospital_name="한결의원",
        status_label="고객 인계 기한이 지났습니다",
        customer_impact="온보딩 시작이 지연됩니다.",
        next_action="운영센터에서 인계를 수락해 주세요.",
        owner_label="담당 AE",
        sla_label="오늘 18:00",
        admin_path="/operations",
        requires_action=requires_action,
        is_recovery=recovery,
        recovery_of="HANDOFF_OVERDUE:0" if recovery else None,
    )


@dataclass(frozen=True)
class _PublishedItem:
    id: uuid.UUID
    hospital_id: uuid.UUID
    title: str | None
    published_at: datetime


@dataclass(frozen=True)
class _Hospital:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class _YieldFact:
    hospital_name: str
    due: int
    published: int
    published_with_reused_image: int
    retrying: int
    topic_swapped: int
    operator_required: int


def _watchdog_report(**overrides: object) -> WatchdogReport:
    report = WatchdogReport(
        observed_at=_NOW,
        kst_date="2026-09-20",
        redis_available=True,
        database_available=True,
        queue_canaries_current=True,
        stale_queues=(),
        stale_critical_queues=(),
        beat_alive=True,
        beat_lock_held=True,
        beat_last_schedule_run_at=_NOW,
        beat_evidence="fresh",
        publish_checked=True,
        publish_due_remaining=4,
        publish_published_today=0,
        publish_missing=True,
        publish_gate_residual=False,
        publish_blocked_today=(),
        publish_partial=False,
        last_generation_batch_at=_NOW,
        generation_batch_stale=False,
    )
    return replace(report, **overrides) if overrides else report


def _blocked_outcome() -> dict[str, object]:
    return {
        "hospital_id": str(_HOSPITAL),
        "hospital_name": "장편한외과의원",
        "content_id": str(uuid.UUID("c1000000-0000-0000-0000-000000000001")),
        "scheduled_date": "2026-09-20",
        "code": "GENERATION_FAILED",
        "cause": "원고가 저장되지 않았습니다.",
        "title": "무릎 통증 자주 묻는 질문",
        "attempt_fingerprint": "fp-1",
    }


def _naver_recovery_intent():
    context = NaverIncidentContext(
        hospital_id=_HOSPITAL,
        hospital_name="장편한외과의원",
        operation_run_id=uuid.UUID("d1000000-0000-0000-0000-000000000001"),
        item=NaverHandoffItem(
            url="https://blog.naver.com/example/1",
            url_hash="hash-1",
            state=NaverHandoffState.INGESTED,
        ),
    )
    incident = Incident(
        id=uuid.UUID("a1000000-0000-0000-0000-000000000002"),
        hospital_id=_HOSPITAL,
        episode_seq=1,
    )
    return _recovery_intent(context, incident)


def _message_of(built: object) -> SlackMessage:
    return getattr(built, "message", built)


# 종류 하나에 표본 하나. 왼쪽이 기대 라벨, 오른쪽이 그 종류의 실제 투영이다.
_SAMPLES: tuple[tuple[str, NotificationLabel, object], ...] = (
    (
        "INCIDENT_OPEN",
        NotificationLabel.ERROR,
        lambda: build_open_incident_notification(_incident(), _ADMIN),
    ),
    (
        "INCIDENT_RECOVERED",
        NotificationLabel.REPORT,
        lambda: build_recovered_incident_notification(_incident(), _ADMIN),
    ),
    (
        "INCIDENT_SUMMARY",
        NotificationLabel.ERROR,
        lambda: build_summary_notification(
            (_incident(),), _NOW, _NOW + timedelta(hours=1), "INCIDENT_SUMMARY", _ADMIN
        ),
    ),
    (
        "MILESTONE_ACTION",
        NotificationLabel.ERROR,
        lambda: build_milestone_action_notification(
            _milestone(requires_action=True), _ADMIN
        ),
    ),
    (
        "MILESTONE_RECOVERED",
        NotificationLabel.REPORT,
        lambda: build_milestone_recovery_notification(
            _milestone(requires_action=False, recovery=True), _ADMIN
        ),
    ),
    (
        "MILESTONE_SUMMARY",
        NotificationLabel.ERROR,
        lambda: build_milestone_summary_notification(
            MilestoneBatch(
                (_milestone(requires_action=True),), _NOW, _NOW + timedelta(days=1)
            ),
            _ADMIN,
        ),
    ),
    (
        "CONTENT_PUBLISHED",
        NotificationLabel.ERROR,
        lambda: build_publish_notification_intent(
            _PublishedItem(
                uuid.UUID("c1000000-0000-0000-0000-000000000001"),
                _HOSPITAL,
                "무릎 통증 자주 묻는 질문",
                _NOW,
            ),
            _Hospital(_HOSPITAL, "장편한외과의원"),
        ),
    ),
    (
        "MISSING_APPROVED_ESSENCE_DIGEST",
        NotificationLabel.ERROR,
        lambda: build_missing_approved_essence_digest_intent(
            date(2026, 9, 20), ({"hospital_id": str(_HOSPITAL)},)
        ),
    ),
    (
        "GENERATION_BLOCKED_DIGEST",
        NotificationLabel.ERROR,
        lambda: build_generation_blocked_digest_intent(
            date(2026, 9, 20), "0800", (_blocked_outcome(),)
        ),
    ),
    (
        "GENERATION_REJECTION_WEEKLY_ROLLUP",
        NotificationLabel.REPORT,
        lambda: build_generation_rejection_weekly_rollup_intent(
            date(2026, 9, 14),
            (),
            (_YieldFact("장편한외과의원", 5, 4, 1, 0, 0, 0),),
        ),
    ),
    (
        "ONBOARDING_V0_READY",
        NotificationLabel.REPORT,
        lambda: build_v0_ready_notification(
            hospital_id=_HOSPITAL,
            hospital_name="장편한외과의원",
            report_id=uuid.UUID("e1000000-0000-0000-0000-000000000001"),
            sov_pct=12.5,
            platforms=["chatgpt", "gemini"],
            admin_base_url=_ADMIN,
        ),
    ),
    (
        "ONBOARDING_SITE_BUILT",
        NotificationLabel.ERROR,
        lambda: build_site_built_notification(
            hospital_id=_HOSPITAL,
            hospital_name="장편한외과의원",
            blocked_reason="자기 도메인 확인 대기",
            admin_base_url=_ADMIN,
        ),
    ),
    (
        "ONBOARDING_HOSPITAL_ACTIVATED",
        NotificationLabel.REPORT,
        lambda: build_hospital_activated_notification(
            hospital_id=_HOSPITAL,
            hospital_name="장편한외과의원",
            public_url="https://clinic.example.com",
            admin_base_url=_ADMIN,
        ),
    ),
    (
        "MONTHLY_REPORT_GAP_SUMMARY",
        NotificationLabel.ERROR,
        lambda: build_monthly_report_gap_summary(
            period_key="2026-08",
            summary_date="2026-09-03",
            gaps=[MonthlyReportGap("장편한외과의원", "MISSING")],
        ),
    ),
    (
        "FLEET_HEARTBEAT",
        NotificationLabel.REPORT,
        lambda: build_fleet_heartbeat(
            _watchdog_report(publish_due_remaining=0, publish_missing=False),
            FleetFacts(2, 1, 0, 0, 2, 2),
            now=_NOW,
            admin_base_url=_ADMIN,
        ),
    ),
    (
        "COST_GUARD_LIMIT_REACHED",
        NotificationLabel.ERROR,
        lambda: _build_cost_alert_intent(
            "content", "daily", "2026-09-20", 100, 100, hard=True, incident=None
        ),
    ),
    (
        "COST_GUARD_SOFT_WARNING",
        NotificationLabel.ERROR,
        lambda: _build_cost_alert_intent(
            "content", "daily", "2026-09-20", 80, 100, hard=False, incident=None
        ),
    ),
    (
        "NAVER_WEEKLY_HANDOFF",
        NotificationLabel.REPORT,
        lambda: build_naver_weekly_digest(
            (NaverWeeklyEntry("장편한외과의원", 2, 3, 0),), date(2026, 9, 20)
        ),
    ),
    ("NAVER_SOURCE_RECOVERED", NotificationLabel.REPORT, _naver_recovery_intent),
    (
        "CONTENT_BATCH_BLOCKED",
        NotificationLabel.ERROR,
        lambda: build_content_batch_message(
            ContentBatchSummary(_HOSPITAL, "장편한외과의원", "2026-09-21", 1, failed=1),
            admin_base_url=_ADMIN,
        ),
    ),
    (
        "CONTENT_BATCH_PREPARED",
        NotificationLabel.REPORT,
        lambda: build_content_batch_message(
            ContentBatchSummary(_HOSPITAL, "장편한외과의원", "2026-09-21", 2),
            admin_base_url=_ADMIN,
        ),
    ),
)


@pytest.mark.parametrize(
    ("kind", "expected", "build"),
    _SAMPLES,
    ids=[sample[0] for sample in _SAMPLES],
)
def test_projection_body_and_header_start_with_the_same_label(
    kind: str, expected: NotificationLabel, build
) -> None:
    message = _message_of(build())

    assert message.fallback_text.startswith(expected.value), kind
    headers = [
        block["text"]["text"]
        for block in message.blocks
        if block.get("type") == "header"
    ]
    for header in headers:
        assert header.startswith(expected.value), kind
    # 한 메시지 안에서 두 라벨이 섞이지 않는다.
    other_labels = {LEAD_LABEL, ERROR_LABEL, REPORT_LABEL} - {expected.value}
    assert not any(other in message.fallback_text for other in other_labels), kind


def test_sample_set_covers_every_registered_kind_with_a_projection() -> None:
    sampled = {sample[0] for sample in _SAMPLES}
    projection_kinds = set(EVENT_LABELS) - {
        # 직접 전송(outbox 밖)이라 아래 전용 테스트가 덮는다.
        "LEAD_CREATED",
        "LEAD_DIAGNOSIS_RECEIVED",
        "PRIVACY_RETENTION_FAILED",
        "PRIVACY_RETENTION_COMPLETED",
        "PIPELINE_WATCHDOG_ALERT",
        "PIPELINE_WATCHDOG_RECOVERY",
    }
    assert projection_kinds == sampled


def test_incident_open_keeps_its_copy_buttons_and_developer_reference() -> None:
    intent = build_open_incident_notification(_incident(), _ADMIN)
    payload = intent.message.payload_json()

    assert f"{ERROR_LABEL} [조치 필요]" in payload
    assert "지금 할 일" in payload and "예정 글 발행 보류" in payload
    assert "무슨 문제인지" not in payload
    assert "멈춘 글 확인" in payload
    assert "콘텐츠에서 해당 글의 차단 사유" in payload
    assert "OPS-" in payload
    assert payload.count(f"{_ADMIN}/operations") == 1


def test_recovered_incident_is_a_report_not_an_error() -> None:
    recovered = build_recovered_incident_notification(_incident(), _ADMIN)

    assert recovered.notification_type == "INCIDENT_RECOVERED"
    assert recovered.message.fallback_text.startswith(REPORT_LABEL)
    assert f"{REPORT_LABEL} [복구]" in recovered.message.payload_json()
    assert "추가 조치가 필요하지 않습니다" in recovered.message.payload_json()
    assert ERROR_LABEL not in recovered.message.payload_json()


def test_watchdog_alert_is_an_error_and_its_recovery_is_a_report() -> None:
    alert = _watchdog_report()
    recovered = _watchdog_report(
        publish_due_remaining=2, publish_published_today=3, publish_missing=False
    )

    for audience in (AUDIENCE_OPERATOR, AUDIENCE_DEVELOPER):
        assert build_alert_text(alert, audience).startswith(ERROR_LABEL)
        assert build_recovery_text(recovered, audience).startswith(REPORT_LABEL)
    # 라벨은 첫 줄 앞에만 붙고 본문 순서는 그대로다.
    operator = build_alert_text(alert, AUDIENCE_OPERATOR)
    assert operator.index("무슨 문제인지") < operator.index("고객 영향")
    assert "오늘 예정된 글이 아직 한 건도 공개되지 않았습니다." in operator.splitlines()[0]


async def test_lead_intake_messages_carry_the_lead_label(monkeypatch) -> None:
    sent: dict[str, object] = {}

    async def fake_send(text, blocks=None):
        sent["text"] = text
        sent["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)

    await notifier.notify_lead_created(clinic_name="장편한외과의원", contact="010-0000-0000")
    assert str(sent["text"]).startswith(LEAD_LABEL)
    assert sent["blocks"][0]["text"]["text"].startswith(LEAD_LABEL)
    assert "[새 문의]" in sent["blocks"][0]["text"]["text"]

    await notifier.notify_lead_diagnosis_received(
        clinic_name="장편한외과의원",
        clinic_type="정형외과",
        region="서울 강남",
        keywords=["무릎"],
        contact="010-0000-0000",
        email="doctor@example.com",
        slot_no=1,
        admin_url=f"{_ADMIN}/leads",
    )
    assert str(sent["text"]).startswith(LEAD_LABEL)
    assert sent["blocks"][0]["text"]["text"].startswith(LEAD_LABEL)
    assert "무료 AI 노출 진단" in sent["blocks"][0]["text"]["text"]


async def test_privacy_purge_failure_is_an_error_and_success_is_silent(monkeypatch) -> None:
    sent: dict[str, object] = {}

    async def fake_send(text, blocks=None):
        sent["text"] = text
        sent["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)

    await notifier.notify_lead_purge_result(purged=0, error="파기 결과 확정 실패")
    assert str(sent["text"]).startswith(ERROR_LABEL)
    assert sent["blocks"][0]["text"]["text"].startswith(ERROR_LABEL)
    assert "PRIVACY-RETENTION" in sent["blocks"][0]["text"]["text"]

    sent.clear()
    assert await notifier.notify_lead_purge_result(purged=3) is False
    assert sent == {}


def test_heartbeat_label_sits_in_front_of_the_geo_summary_line() -> None:
    message = build_fleet_heartbeat(
        _watchdog_report(publish_due_remaining=0, publish_missing=False),
        FleetFacts(2, 1, 0, 0, 2, 2),
        now=_NOW,
        admin_base_url=_ADMIN,
    ).message
    first_line = message.fallback_text.splitlines()[0]

    assert first_line.startswith(f"{REPORT_LABEL} [일일 요약] 2026-09-20")
    assert "점검 이상 없음" in first_line
    # 본문 섹션과 fallback이 같은 문구를 쓴다 — 목록과 펼친 메시지가 같은 분류를 말한다.
    assert message.blocks[0]["text"]["text"] == first_line


def test_blocked_digest_separates_successful_image_reuse_from_errors() -> None:
    intent = build_generation_blocked_digest_intent(
        date(2026, 9, 20),
        "0800",
        (_blocked_outcome(),),
        reused_outcomes=(
            {
                "hospital_id": str(_HOSPITAL),
                "hospital_name": "장편한외과의원",
                "content_id": str(uuid.UUID("c1000000-0000-0000-0000-000000000002")),
                "image_failure_class": "PROVIDER_QUOTA",
                "reused_from": "HOSPITAL_HERO",
            },
        ),
    )
    payload = intent.message.payload_json()

    assert f"{ERROR_LABEL} [조치 필요]" in payload
    assert "병원 대표 이미지 사용" not in payload
    assert "이미지 생성 공급자 크레딧" not in payload


def test_weekly_rollup_stays_a_report_when_blocks_exist() -> None:
    intent = build_generation_rejection_weekly_rollup_intent(
        date(2026, 9, 14),
        ({"hospital_id": str(_HOSPITAL), "hospital_name": "장편한외과의원", "code": "CONTENT_AI_HARD_FINDING", "reason": "금지 표현 검사 차단"},),
        (_YieldFact("장편한외과의원", 5, 3, 0, 1, 0, 1),),
    )
    payload = intent.message.payload_json()

    assert intent.message.fallback_text.startswith(REPORT_LABEL)
    assert f"{REPORT_LABEL} 주간 콘텐츠 발행 요약" in payload
    assert "발행 3/5" in payload and "본문·근거 확인 필요 1건" in payload
