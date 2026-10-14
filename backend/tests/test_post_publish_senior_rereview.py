"""교정으로 못 푼 UNCERTAIN만 남은 공개 글은 상위 모델이 한 번 더 본다(Phase 1-10)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReviewStatus,
    candidate_sha256,
)
from app.workers import post_publish_ai_review as sweep
from tests.test_post_publish_ai_review import _published, _review

NOW = datetime(2026, 10, 20, 3, 10, tzinfo=timezone.utc)


PHILOSOPHY = "본원은 환자 한 분 한 분의 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."


def _flagged(severity="UNCERTAIN", days_ago=8):
    item = _published()
    item.id = uuid.uuid4()
    item.body = f"## 안내\n{PHILOSOPHY}"
    item.hospital = SimpleNamespace(id=uuid.uuid4(), name="강남병원")
    item.essence_check_summary = {
        "post_publish_ai_review": {
            "status": "FLAGGED",
            "candidate_sha256": candidate_sha256(item),
            "checked_at": (NOW - timedelta(days=days_ago)).isoformat(),
            "findings": ["지적"],
            "structured_findings": [
                {"severity": severity, "kind": "MEDICAL_SAFETY", "quote": "q", "message": "지적"}
            ],
            "correction": {"finished": True, "passes": 2},
        }
    }
    return item


class _DB:
    def rollback(self):
        pass

    def commit(self):
        pass


def _wire(monkeypatch, item, review):
    calls = {"models": [], "recovered": 0, "needs_human": [], "alerts": []}
    monkeypatch.setattr(
        sweep, "_enqueue_notification", lambda _db, intent, _now: calls["alerts"].append(intent)
    )

    async def fake_review(**kwargs):
        calls["models"].append(kwargs.get("model"))
        return review

    monkeypatch.setattr(sweep, "review_generated_content", fake_review)
    monkeypatch.setattr(sweep, "_candidate_content", lambda _item: {})
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)
    monkeypatch.setattr(
        sweep, "_recover_flag_incident",
        lambda *_a, **_k: calls.__setitem__("recovered", calls["recovered"] + 1),
    )
    monkeypatch.setattr(
        sweep, "_open_critical_incident",
        lambda _item, _h, _r, **k: calls["needs_human"].append(k.get("needs_human_reason")),
    )
    return calls


def test_uncertain_only_and_age_gate():
    uncertain = _flagged().essence_check_summary["post_publish_ai_review"]
    hard = _flagged("HARD").essence_check_summary["post_publish_ai_review"]
    young = _flagged(days_ago=3).essence_check_summary["post_publish_ai_review"]
    assert sweep.uncertain_only(uncertain) is True
    assert sweep.uncertain_only(hard) is False
    assert sweep.settled_long_enough(young, NOW, 7) is False
    assert sweep.settled_long_enough(uncertain, NOW, 7) is True


def test_senior_pass_closes_the_flag_with_the_review_model(monkeypatch):
    item = _flagged()
    calls = _wire(monkeypatch, item, _review(item))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    assert result == "REVIEWED"
    assert calls["models"] == [None]  # None = settings.CLAUDE_MODEL_REVIEW
    assert calls["recovered"] == 1
    assert "post_publish_ai_review" not in item.essence_check_summary


def test_senior_still_uncertain_is_recorded_once_and_closes_quietly(monkeypatch):
    """상위 재검수 뒤에도 의료 내용 없는 확신 부족만 남았다 — 사고 없이 조용히 닫는다(D35)."""

    item = _flagged()
    uncertain = ContentAiFinding(
        ContentAiFindingSeverity.UNCERTAIN,
        ContentAiFindingKind.HOSPITAL_FACT,
        "확인할 수 없는 진료 원칙",
        quote=PHILOSOPHY,
        critical=False,
    )
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[uncertain]))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    marker = item.essence_check_summary["post_publish_ai_review"]
    assert result == "FLAGGED"
    assert marker["senior_rereview"]["model"] == settings.CLAUDE_MODEL_REVIEW
    assert marker["correction"]["finished"] is True
    assert calls["needs_human"] == []
    assert marker["closed"]["reason"] == "NONCRITICAL"
    assert item.post_publish_reviewed_by == sweep.POST_PUBLISH_NONCRITICAL_CLOSER


def test_senior_critical_finding_asks_the_marketer_once(monkeypatch):
    """상위 검수가 의료 안전 HARD(Critical)를 냈다 — 글은 내리지 않고 "마케터 검토 필요"(D35)."""

    item = _flagged()
    hard = ContentAiFinding(ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, "근거 없는 단정")
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[hard]))

    sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)
    sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW)  # 실행 끝의 병원별 전달

    assert len(calls["needs_human"]) == 1
    assert item.essence_check_summary[sweep.POST_PUBLISH_CRITICAL_ALERT_KEY]["enqueued_at"]
    assert len(calls["alerts"]) == 1


def test_senior_uncertain_medical_finding_is_critical_and_alerts(monkeypatch):
    """의료 안전 지적은 확신 부족이어도 Critical이다(fail closed) — 조용히 닫지 않고 알린다."""

    item = _flagged()
    uncertain = ContentAiFinding(
        ContentAiFindingSeverity.UNCERTAIN, ContentAiFindingKind.MEDICAL_SAFETY, "확신 부족"
    )
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[uncertain]))

    sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)
    sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW)  # 실행 끝의 병원별 전달

    assert len(calls["needs_human"]) == 1
    assert item.post_publish_reviewed_at is None


def test_senior_rereview_marker_keeps_the_item_out_of_tomorrows_selection(monkeypatch):
    item = _flagged()
    uncertain = ContentAiFinding(
        ContentAiFindingSeverity.UNCERTAIN, ContentAiFindingKind.MEDICAL_SAFETY, "확신 부족"
    )
    _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[uncertain]))

    sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    # 같은 본문을 매일 다시 사지 않는다 — 선택 SQL은 이 키가 있는 표시를 거른다.
    marker = item.essence_check_summary["post_publish_ai_review"]
    assert marker[sweep.POST_PUBLISH_SENIOR_REREVIEW_KEY]["at"] == NOW.isoformat()
    params = sweep._uncertain_only_stmt(5).compile().params.values()
    assert sweep.POST_PUBLISH_SENIOR_REREVIEW_KEY in params


def test_unavailable_senior_review_changes_nothing(monkeypatch):
    item = _flagged()
    before = dict(item.essence_check_summary["post_publish_ai_review"])
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.UNAVAILABLE))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    assert result == "UNAVAILABLE"
    assert item.essence_check_summary["post_publish_ai_review"] == before
    assert calls["recovered"] == 0 and calls["needs_human"] == []


def test_unavailable_rereview_does_not_spend_the_daily_cap(monkeypatch):
    """1a처럼 검수를 끝내지 못한 시도(UNAVAILABLE·SKIPPED)는 하루 상한을 쓰지 않는다."""

    import app.workers.tasks as tasks_module
    from tests.test_published_auto_correction import _hospital
    from tests.test_published_auto_correction_sweep import _SweepDb

    first, second = _flagged(), _flagged()
    for item in (first, second):
        item.hospital = _hospital()
        # 실행은 실제 시계를 쓴다 — 대기 일수가 넉넉히 지난 검수 시각으로 둔다.
        item.essence_check_summary["post_publish_ai_review"]["checked_at"] = (
            "2026-01-01T00:00:00+00:00"
        )

    class _Db(_SweepDb):
        def __init__(self):
            # Critical 백로그(없음) → 미전달 알림(없음) → FLAGGED 조회(없음) → 숫자 시각 표시(없음)
            # → 옛 표시 한 쪽 → 비공개 → 표본(선발행 단계는 표본 상한 0이라 돌지 않는다)
            self._batches = [[], [], [], [], [first, second], [], []]

    seen: list = []

    def fake_senior(_db, item, *_args):
        seen.append(item.id)
        return "UNAVAILABLE" if item is first else "REVIEWED"

    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", _Db)
    monkeypatch.setattr(sweep, "_load_uncertain_scan_cursor", lambda: None)
    monkeypatch.setattr(sweep, "_save_uncertain_scan_cursor", lambda _cursor: None)
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: object())
    monkeypatch.setattr(sweep, "senior_rereview_flagged", fake_senior)
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", 1)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS", 7)

    sweep.review_post_publish_samples.run()

    assert seen == [first.id, second.id]


def test_the_sweep_resumes_the_legacy_scan_where_the_last_run_stopped(monkeypatch):
    """옛 표시 훑기 커서를 저장소에서 읽어 넘기고, 이번 실행이 멈춘 자리를 다시 저장한다(R3-2)."""

    from tests.test_published_auto_correction_sweep import _SweepDb

    stored, resumed_at = uuid.uuid4(), uuid.uuid4()
    passed: list = []
    saved: list = []

    def fake_select(_db, _limit, *, now, days, after_id=None):
        passed.append(after_id)
        return sweep.UncertainSelection([], resumed_at)

    class _Db(_SweepDb):
        def __init__(self):
            self._batches = [[], [], []]  # FLAGGED → 비공개 → 표본 (선택은 가짜로 대신한다)

    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", _Db)
    monkeypatch.setattr(sweep, "_uncertain_settled_items", fake_select)
    monkeypatch.setattr(sweep, "_load_uncertain_scan_cursor", lambda: stored)
    monkeypatch.setattr(sweep, "_save_uncertain_scan_cursor", saved.append)
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", 1)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS", 7)

    sweep.review_post_publish_samples.run()

    assert passed == [stored]
    assert saved == [resumed_at]


def test_a_new_flag_carries_a_numeric_checked_at_for_sql_selection():
    review = _review(_flagged(), status=ContentAiReviewStatus.PASS)

    payload = sweep._flag_payload(review, "hash", NOW)

    assert payload["checked_at_epoch"] == int(NOW.timestamp())
    assert sweep.settled_long_enough(payload, NOW + timedelta(days=7), 7)
    assert not sweep.settled_long_enough(payload, NOW + timedelta(days=6), 7)


@pytest.fixture(autouse=True)
def _tiers_and_alerts_on(monkeypatch):
    """이 파일은 기본 꺼짐인 W4 등급·D35 알림을 켠 동작을 고정한다(꺼진 동작은 main과 같다)."""

    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    monkeypatch.setattr(_settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", True)
