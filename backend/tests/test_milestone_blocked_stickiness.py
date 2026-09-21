"""차단이 이어지는 동안 같은 Slack Error가 관측 창마다 다시 나가지 않는다.

2026-09-21 `#mkt-reputation`에서 한 병원의 월간 리포트 차단이 15분 요약 창 하나에 여섯 줄
올라왔고, 창마다 같은 사유가 반복됐다. 상태 지문이 표본 수·PDF 행처럼 차단 문구를 바꾸지
않는 값까지 담고 있어 차단이 유지되는 동안에도 매번 '새 상태'로 보였기 때문이다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.monthly_control import ReportArtifactState
from app.services.monthly_events import MonthlyEventType, project_monthly_event
from app.services.notification_milestone_messages import (
    MilestoneBatch,
    MilestoneKind,
    build_milestone_summary_notification,
)
from app.workers import milestone_monthly_projection
from app.workers.milestone_event_tasks import should_notify_milestone
from app.workers.milestone_monthly_facts import ReportFacts
from app.workers.milestone_monthly_projection import observe_monthly_milestones

_ADMIN = "http://localhost:3000"
_HOSPITAL_ID = uuid.UUID("b1390000-0000-0000-0000-000000000001")
_REPORT_ID = uuid.UUID("c1390000-0000-0000-0000-000000000001")
_WINDOW = datetime(2026, 9, 21, 18, tzinfo=UTC)


class _NoDeliveries:
    def scalars(self):
        return ()


class _NoDeliveryDB:
    async def execute(self, _statement):
        return _NoDeliveries()


def _blocked_facts(
    *,
    success: int = 3,
    failed: int = 17,
    gate_code: str = "coverage_incomplete",
    artifact_state: ReportArtifactState = ReportArtifactState.MISSING,
    artifact_id: uuid.UUID | None = None,
    period: tuple[int, int] = (2026, 8),
    delivered: bool = False,
    report_id: uuid.UUID = _REPORT_ID,
) -> ReportFacts:
    """현재 근거 자료가 막힌 한 달치 리포트. 차단 문구를 바꾸지 않는 값만 인자로 연다."""

    return ReportFacts(
        report=SimpleNamespace(
            id=report_id,
            quality="DEGRADED",
            planned_count=20,
            success_count=success,
            failed_count=failed,
            created_at=_WINDOW - timedelta(days=20),
            period_year=period[0],
            period_month=period[1],
            sov_summary=None,
        ),
        hospital=SimpleNamespace(id=_HOSPITAL_ID, name="서울W내과의원 위례점"),
        manifest=SimpleNamespace(closed_at=_WINDOW - timedelta(days=10)),
        artifact=(
            None
            if artifact_id is None
            else SimpleNamespace(
                id=artifact_id,
                validated_at=None,
                created_at=_WINDOW - timedelta(days=5),
            )
        ),
        artifact_state=artifact_state,
        ready=False,
        delivered=delivered,
        blockers=(gate_code, "CURRENT_READINESS_BLOCKED"),
    )


def _ready_facts() -> ReportFacts:
    """같은 리포트가 전달 준비 완료로 넘어간 모습."""

    validated_at = _WINDOW - timedelta(hours=1)
    return ReportFacts(
        report=SimpleNamespace(
            id=_REPORT_ID,
            quality="COMPLETE",
            planned_count=20,
            success_count=20,
            failed_count=0,
            created_at=_WINDOW - timedelta(days=20),
            period_year=2026,
            period_month=8,
            sov_summary={"sov_pct": 20.0},
        ),
        hospital=SimpleNamespace(id=_HOSPITAL_ID, name="서울W내과의원 위례점"),
        manifest=SimpleNamespace(closed_at=_WINDOW - timedelta(days=10)),
        artifact=SimpleNamespace(
            id=uuid.UUID("d1390000-0000-0000-0000-000000000001"),
            validated_at=validated_at,
            created_at=validated_at,
        ),
        artifact_state=ReportArtifactState.VALID,
        ready=True,
        delivered=False,
        blockers=(),
    )


async def _observe(monkeypatch, facts_list, previous_states, observed_at=_WINDOW):
    async def load(_db):
        return {facts.report.id: facts for facts in facts_list}

    monkeypatch.setattr(milestone_monthly_projection, "load_report_facts", load)
    return await observe_monthly_milestones(
        _NoDeliveryDB(),
        observed_at,
        previous_states,
        observed_at - timedelta(minutes=15),
    )


@pytest.mark.asyncio
async def test_first_entry_into_blocked_still_notifies(monkeypatch) -> None:
    scan = await _observe(monkeypatch, [_blocked_facts()], {})

    assert [item.kind for item in scan.milestones] == [MilestoneKind.MONTHLY_BLOCKED]
    assert should_notify_milestone(scan.milestones[0])


@pytest.mark.asyncio
async def test_sticky_blocked_does_not_renotify_while_volatile_facts_move(monkeypatch) -> None:
    first = await _observe(monkeypatch, [_blocked_facts()], {})
    assert first.milestones

    # 같은 차단이 이어지는 동안 표본 수·재생성된 PDF 행·게이트 코드는 계속 움직인다.
    still_blocked = _blocked_facts(
        success=11,
        failed=9,
        gate_code="doctor_artifact_invalid",
        artifact_state=ReportArtifactState.INVALID,
        artifact_id=uuid.uuid4(),
    )
    second = await _observe(
        monkeypatch,
        [still_blocked],
        first.states,
        observed_at=_WINDOW + timedelta(minutes=15),
    )
    third = await _observe(
        monkeypatch,
        [_blocked_facts(success=14, failed=6, artifact_id=uuid.uuid4())],
        second.states,
        observed_at=_WINDOW + timedelta(minutes=30),
    )

    assert second.milestones == ()
    assert third.milestones == ()
    assert second.states == first.states == third.states


@pytest.mark.asyncio
async def test_blocked_to_customer_ready_transition_notifies(monkeypatch) -> None:
    blocked = await _observe(monkeypatch, [_blocked_facts()], {})
    ready = await _observe(
        monkeypatch,
        [_ready_facts()],
        blocked.states,
        observed_at=_WINDOW + timedelta(minutes=15),
    )

    assert [item.kind for item in ready.milestones] == [MilestoneKind.MONTHLY_CUSTOMER_READY]
    assert ready.states != blocked.states


@pytest.mark.asyncio
async def test_blocked_operator_copy_change_still_notifies(monkeypatch) -> None:
    """차단이 유지돼도 사람이 할 일이 바뀌면 그건 새 알림이다."""

    readiness_blocked = await _observe(monkeypatch, [_blocked_facts()], {})
    measurement_blocked = _blocked_facts()
    measurement_blocked = ReportFacts(
        report=measurement_blocked.report,
        hospital=measurement_blocked.hospital,
        manifest=measurement_blocked.manifest,
        artifact=measurement_blocked.artifact,
        artifact_state=measurement_blocked.artifact_state,
        ready=False,
        delivered=False,
        blockers=("coverage_incomplete",),
    )
    later = await _observe(
        monkeypatch,
        [measurement_blocked],
        readiness_blocked.states,
        observed_at=_WINDOW + timedelta(minutes=15),
    )

    assert [item.kind for item in later.milestones] == [MilestoneKind.MONTHLY_BLOCKED]
    assert "누락된 측정" in later.milestones[0].next_action


@pytest.mark.asyncio
async def test_delivered_past_months_leave_the_current_state_scan(monkeypatch) -> None:
    """이미 전달한 지난 계약 월은 병원 공통 차단이 켜져도 다시 사람을 부르지 않는다."""

    delivered_old = _blocked_facts(
        period=(2026, 3),
        delivered=True,
        report_id=uuid.UUID("c1390000-0000-0000-0000-000000000003"),
    )
    undelivered_old = _blocked_facts(
        period=(2026, 4),
        report_id=uuid.UUID("c1390000-0000-0000-0000-000000000004"),
    )
    delivered_recent = _blocked_facts(
        period=(2026, 8),
        delivered=True,
        report_id=uuid.UUID("c1390000-0000-0000-0000-000000000008"),
    )

    scan = await _observe(
        monkeypatch,
        [delivered_old, undelivered_old, delivered_recent],
        {},
    )

    assert set(scan.states) == {
        f"monthly:{undelivered_old.report.id}",
        f"monthly:{delivered_recent.report.id}",
    }


def test_summary_collapses_one_hospitals_repeated_blocked_months() -> None:
    """같은 병원·같은 차단 문구는 여섯 달치여도 한 줄로 접힌다."""

    months = (3, 4, 5, 6, 7, 8)
    projections = tuple(
        project_monthly_event(
            milestone_monthly_projection._monthly_event(
                milestone_monthly_projection._MonthlyEventRequest(
                    _blocked_facts(period=(2026, month), report_id=uuid.uuid4()),
                    MonthlyEventType.BLOCKED,
                    _WINDOW,
                    uuid.uuid4(),
                )
            )
        )
        for month in months
    )
    batch = MilestoneBatch(projections, _WINDOW - timedelta(minutes=15), _WINDOW)

    intent = build_milestone_summary_notification(batch, _ADMIN)
    lines = "".join(
        str(block["text"]["text"])
        for block in intent.message.blocks
        if block["type"] == "section"
    )

    assert lines.count("• *서울W내과의원 위례점*") == 1
    assert "2026년 3월 외 5개월" in lines
    # 접기는 표시에만 적용한다 — 실제 건수와 dedupe 키는 여섯 건을 그대로 센다.
    assert "6건" in intent.message.fallback_text
    assert len({item.stable_id for item in projections}) == 6
