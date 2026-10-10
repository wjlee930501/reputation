"""더 늦은 달이 준비·전달됐다면 지나간 달은 사람을 부르지 않는다.

2026-10-09 `be540bc0` 배포 직후 7개 병원의 `monthly:{병원}:2026-08` 상태가 `READY:False`에서
`BLOCKED:True:False`로 바뀌어 `[업무 알림] 병원 7곳 · 2026년 8월 · 월간 리포트 차단`이 나갔다. 옛 8월
요약에 측정 가용성 필드가 없어서였고, 그 7곳의 9월 리포트는 이미 전달 준비 완료였다. AE가 할 일은
9월 전달이지 8월이 아니다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.monthly_control import ReportArtifactState
from app.services.notification_contracts import NotificationPayloadError
from app.services.notification_milestone_messages import MilestoneKind
from app.workers import milestone_monthly_projection
from app.workers.milestone_event_tasks import should_notify_milestone
from app.workers.milestone_monthly_facts import ReportFacts
from app.workers.milestone_monthly_projection import observe_monthly_milestones

_HOSPITAL_ID = uuid.UUID("b1400000-0000-0000-0000-000000000001")
_WINDOW = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
_AUG_KEY = f"monthly:{_HOSPITAL_ID}:2026-08"
_SEP_KEY = f"monthly:{_HOSPITAL_ID}:2026-09"


class _NoDeliveries:
    def scalars(self):
        return ()


class _NoDeliveryDB:
    async def execute(self, _statement):
        return _NoDeliveries()


def _report(month: int, *, quality: str, success: int, failed: int, sov_summary=None):
    return SimpleNamespace(
        id=uuid.uuid5(_HOSPITAL_ID, f"report-2026-{month:02d}"),
        version=1,
        supersedes_report_id=None,
        manifest_id=uuid.uuid5(_HOSPITAL_ID, f"manifest-2026-{month:02d}"),
        quality=quality,
        planned_count=20,
        success_count=success,
        failed_count=failed,
        excluded_count=0,
        created_at=_WINDOW - timedelta(days=5),
        period_year=2026,
        period_month=month,
        sov_summary=sov_summary,
    )


def _month_of(item) -> int:
    """투영의 관리 화면 경로는 그 달 리포트를 가리킨다."""

    return next(
        month
        for month in range(1, 13)
        if str(uuid.uuid5(_HOSPITAL_ID, f"report-2026-{month:02d}")) in item.admin_path
    )


def _hospital():
    return SimpleNamespace(id=_HOSPITAL_ID, name="서울W내과의원 위례점")


def _blocked(month: int, *, delivered: bool = False) -> ReportFacts:
    """옛 요약이라 측정 가용성을 확인하지 못해 막힌 달."""

    return ReportFacts(
        report=_report(month, quality="DEGRADED", success=3, failed=17),
        hospital=_hospital(),
        manifest=SimpleNamespace(closed_at=_WINDOW - timedelta(days=10)),
        artifact=None,
        artifact_state=ReportArtifactState.MISSING,
        ready=False,
        delivered=delivered,
        blockers=("report_blocked", "CURRENT_READINESS_BLOCKED"),
    )


def _ready(month: int, *, delivered: bool = False) -> ReportFacts:
    validated_at = _WINDOW - timedelta(hours=1)
    return ReportFacts(
        report=_report(
            month, quality="COMPLETE", success=20, failed=0, sov_summary={"sov_pct": 20.0}
        ),
        hospital=_hospital(),
        manifest=SimpleNamespace(closed_at=_WINDOW - timedelta(days=10)),
        artifact=SimpleNamespace(
            id=uuid.uuid5(_HOSPITAL_ID, f"artifact-2026-{month:02d}"),
            validated_at=validated_at,
            created_at=validated_at,
        ),
        artifact_state=ReportArtifactState.VALID,
        ready=True,
        delivered=delivered,
        blockers=(),
    )


async def _observe(monkeypatch, facts_list, previous_states, observed_at=_WINDOW):
    async def load(_db):
        return {facts.report.id: facts for facts in facts_list}

    monkeypatch.setattr(milestone_monthly_projection, "load_report_facts", load)
    return await observe_monthly_milestones(
        _NoDeliveryDB(), observed_at, previous_states, observed_at - timedelta(minutes=15)
    )


@pytest.mark.asyncio
async def test_august_turning_blocked_is_silent_when_september_is_ready(monkeypatch) -> None:
    before = {_AUG_KEY: "READY:False", _SEP_KEY: "READY:False"}

    scan = await _observe(monkeypatch, [_blocked(8), _ready(9)], before)

    assert scan.milestones == ()
    # 8월은 알리지 않되 기억은 그대로 넘긴다 — 9월이 다시 막혀도 같은 일을 다시 알리지 않도록.
    assert scan.states == before


@pytest.mark.asyncio
async def test_august_is_silent_when_september_was_delivered(monkeypatch) -> None:
    before = {_AUG_KEY: "READY:False", _SEP_KEY: "BLOCKED:True:True"}

    scan = await _observe(monkeypatch, [_blocked(8), _blocked(9, delivered=True)], before)

    assert scan.milestones == ()
    assert scan.states[_AUG_KEY] == "READY:False"


@pytest.mark.asyncio
async def test_august_transition_without_a_later_settled_month_notifies_once(monkeypatch) -> None:
    before = {_AUG_KEY: "READY:False"}

    first = await _observe(monkeypatch, [_blocked(8), _blocked(9)], before)
    again = await _observe(
        monkeypatch, [_blocked(8), _blocked(9)], first.states, _WINDOW + timedelta(minutes=15)
    )

    august = [item for item in first.milestones if _month_of(item) == 8]
    assert [item.kind for item in august] == [MilestoneKind.MONTHLY_BLOCKED]
    assert should_notify_milestone(august[0])
    assert _AUG_KEY in first.states
    assert again.milestones == ()


@pytest.mark.asyncio
async def test_september_newly_ready_notifies_and_august_leaves(monkeypatch) -> None:
    before = {_AUG_KEY: "BLOCKED:True:True", _SEP_KEY: "BLOCKED:True:True"}

    scan = await _observe(monkeypatch, [_blocked(8), _ready(9)], before)

    assert [(item.kind, _month_of(item)) for item in scan.milestones] == [
        (MilestoneKind.MONTHLY_CUSTOMER_READY, 9)
    ]
    assert scan.states[_AUG_KEY] == "BLOCKED:True:True"


@pytest.mark.asyncio
async def test_superseded_august_turning_ready_notifies_once(monkeypatch) -> None:
    """한 번도 전달하지 않은 8월이 9월 전달 뒤에 고쳐지면 AE가 전달할 일이다 — 한 번 알린다."""

    before = {_AUG_KEY: "BLOCKED:True:True", _SEP_KEY: "READY:True"}
    september = _ready(9, delivered=True)

    blocked = await _observe(monkeypatch, [_blocked(8), september], before)
    fixed = await _observe(
        monkeypatch, [_ready(8), september], blocked.states, _WINDOW + timedelta(minutes=15)
    )
    again = await _observe(
        monkeypatch, [_ready(8), september], fixed.states, _WINDOW + timedelta(minutes=30)
    )

    assert blocked.milestones == ()
    assert [(item.kind, _month_of(item)) for item in fixed.milestones] == [
        (MilestoneKind.MONTHLY_CUSTOMER_READY, 8)
    ]
    assert again.milestones == ()


@pytest.mark.asyncio
async def test_august_does_not_re_alert_when_september_blocks_again(monkeypatch) -> None:
    """8월 차단을 이미 알린 뒤 9월이 준비되면 8월은 빠진다. 9월이 다시 막혀 8월이 범위로 돌아와도
    기억이 남아 있으므로 8월 차단을 다시 알리지 않는다(원래 사고의 재현 경로)."""

    before = {_AUG_KEY: "BLOCKED:True:True", _SEP_KEY: "READY:False"}

    excluded = await _observe(monkeypatch, [_blocked(8), _ready(9)], before)
    returned = await _observe(
        monkeypatch, [_blocked(8), _blocked(9)], excluded.states, _WINDOW + timedelta(minutes=15)
    )

    assert excluded.milestones == ()
    assert excluded.states[_AUG_KEY] == "BLOCKED:True:True"
    assert all(_month_of(item) != 8 for item in returned.milestones)
    assert returned.states[_AUG_KEY] == "BLOCKED:True:True"


@pytest.mark.asyncio
async def test_undelivered_month_after_the_latest_ready_month_stays_in_scope(monkeypatch) -> None:
    """준비된 8월 뒤의 막힌 9월은 그대로 남는다 — 범위를 줄이는 것은 더 늦은 준비 달뿐이다."""

    scan = await _observe(monkeypatch, [_ready(8), _blocked(9)], {})

    assert set(scan.states) == {_AUG_KEY, _SEP_KEY}
    assert MilestoneKind.MONTHLY_BLOCKED in {item.kind for item in scan.milestones}


@pytest.mark.asyncio
async def test_skipped_report_keeps_its_memory_and_returns_silently(monkeypatch) -> None:
    first = await _observe(monkeypatch, [_blocked(9)], {})
    assert [item.kind for item in first.milestones] == [MilestoneKind.MONTHLY_BLOCKED]

    real_project = milestone_monthly_projection._project_observed_current

    def broken(*_args, **_kwargs):
        raise NotificationPayloadError("TEST_GATE_MISMATCH")

    monkeypatch.setattr(milestone_monthly_projection, "_project_observed_current", broken)
    skipped = await _observe(
        monkeypatch, [_blocked(9)], first.states, _WINDOW + timedelta(minutes=15)
    )
    monkeypatch.setattr(milestone_monthly_projection, "_project_observed_current", real_project)
    returned = await _observe(
        monkeypatch, [_blocked(9)], skipped.states, _WINDOW + timedelta(minutes=30)
    )

    assert skipped.milestones == ()
    assert skipped.states == first.states
    assert returned.milestones == ()
    assert returned.states == first.states
