"""W10 일일 SLO 요약 줄 — 놓친 병원만 한 줄, 상한을 넘으면 접고 Slack 블록 한도 안에 든다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.services.autonomy_slo import SLO_SUMMARY_LINE_LIMIT, HospitalSlo, slo_summary_lines
from app.services.fleet_heartbeat import FleetFacts, build_fleet_heartbeat
from app.services.pipeline_watchdog import WatchdogReport
from app.workers.content_stall_detector import STALL_IDLE, is_stalled, last_progress_at

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _slo(name: str, **values) -> HospitalSlo:
    return HospitalSlo(hospital_id=uuid.uuid4(), hospital_name=name, **values)


def test_only_missed_hospitals_get_a_line() -> None:
    healthy = _slo(
        "정상의원",
        due_today=2,
        published_today=2,
        month_target=12,
        month_published=5,
        month_remaining=7,
    )
    short = _slo("부족의원", month_target=12, month_published=3, month_remaining=7)
    report = _slo("보고서의원", report_missing=True)
    assert not healthy.missed
    assert short.month_shortfall == 2
    lines = slo_summary_lines([healthy, short, report])
    assert lines == [
        "• 부족의원 — 이번 달 약속 12편 중 남은 일정으로 2편 부족",
        "• 보고서의원 — 지난달 보고서 전달 준비 안 됨",
    ]


def test_many_misses_fold_and_fit_the_slack_block() -> None:
    slos = [
        _slo(
            "가" * 90,
            due_today=3,
            published_today=0,
            month_target=20,
            month_published=0,
            report_missing=True,
        )
        for _ in range(SLO_SUMMARY_LINE_LIMIT + 3)
    ]
    lines = slo_summary_lines(slos)
    assert len(lines) == SLO_SUMMARY_LINE_LIMIT + 1
    assert lines[-1] == "그 외 3곳은 운영센터에서 볼 수 있습니다."
    report = WatchdogReport(
        observed_at=NOW,
        kst_date="2026-10-14",
        redis_available=True,
        database_available=True,
        queue_canaries_current=True,
        stale_queues=(),
        stale_critical_queues=(),
        beat_alive=True,
        beat_lock_held=True,
        beat_last_schedule_run_at=NOW,
        beat_evidence="test",
        publish_checked=True,
        publish_due_remaining=0,
        publish_published_today=3,
        publish_missing=False,
        publish_gate_residual=False,
        publish_blocked_today=(),
        publish_partial=False,
        last_generation_batch_at=NOW,
        generation_batch_stale=False,
    )
    facts = FleetFacts(2, 0, 0, 0, 2, 2, slo_lines=tuple(lines), stall_actions_today=4)
    intent = build_fleet_heartbeat(
        report, facts, now=NOW, admin_base_url="https://admin.example.com"
    )
    text = intent.message.fallback_text
    assert "발행·보고서 약속을 놓친 병원" in text
    assert "정체 감지기 자동 조치 오늘 4건" in text
    assert lines[0] in text


def test_stall_needs_six_idle_hours_and_no_live_claim() -> None:
    from types import SimpleNamespace

    item = SimpleNamespace(
        generated_at=None,
        body_updated_at=None,
        generation_claimed_at=None,
        generation_claim_token=None,
        created_at=NOW - timedelta(days=2),
        essence_check_summary={
            "generation_attempt": {"observed_at": (NOW - timedelta(hours=7)).isoformat()}
        },
        topic_swap_history=[],
    )
    assert last_progress_at(item) == NOW - timedelta(hours=7)
    assert is_stalled(item, now=NOW)
    item.topic_swap_history = [{"swapped_at": (NOW - timedelta(hours=1)).isoformat()}]
    assert not is_stalled(item, now=NOW)
    item.topic_swap_history = []
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = NOW - STALL_IDLE - timedelta(hours=1)
    # 살아 있는 claim(TTL 2시간 안)은 아니므로 진전 시각으로만 판정한다.
    assert is_stalled(item, now=NOW)
    item.generation_claimed_at = NOW - timedelta(minutes=10)
    assert not is_stalled(item, now=NOW)
