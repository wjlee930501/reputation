"""교정으로 못 푼 UNCERTAIN만 남은 공개 글은 상위 모델이 한 번 더 본다(Phase 1-10)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

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


def _flagged(severity="UNCERTAIN", days_ago=8):
    item = _published()
    item.id = uuid.uuid4()
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
    calls = {"models": [], "recovered": 0, "needs_human": []}

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
        sweep, "_open_flag_incident",
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


def test_senior_still_uncertain_is_recorded_once_and_stays_quiet(monkeypatch):
    item = _flagged()
    uncertain = ContentAiFinding(
        ContentAiFindingSeverity.UNCERTAIN, ContentAiFindingKind.MEDICAL_SAFETY, "확신 부족"
    )
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[uncertain]))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    marker = item.essence_check_summary["post_publish_ai_review"]
    assert result == "FLAGGED"
    assert marker["senior_rereview"]["model"] == settings.CLAUDE_MODEL_REVIEW
    assert marker["correction"]["finished"] is True
    assert calls["needs_human"] == []


def test_senior_hard_finding_refreshes_the_quiet_flag_incident(monkeypatch):
    """상위 검수의 HARD도 사람의 Slack·할 일로 올리지 않는다(D2, 10/8 지시 유지).

    조용한 FLAGGED 사고의 문구만 '사람이 직접 고쳐야 한다'로 갱신하고 콘텐츠 탭에 남긴다.
    """

    item = _flagged()
    hard = ContentAiFinding(ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, "근거 없는 단정")
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[hard]))

    sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    assert calls["needs_human"] == ["SENIOR_REREVIEW_HARD"]


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
            # FLAGGED 조회(없음) → 상위 재검수 대상 → 비공개 → 표본
            self._batches = [[], [first, second], [], []]

    seen: list = []

    def fake_senior(_db, item, *_args):
        seen.append(item.id)
        return "UNAVAILABLE" if item is first else "REVIEWED"

    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", _Db)
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: object())
    monkeypatch.setattr(sweep, "senior_rereview_flagged", fake_senior)
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", 1)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS", 7)

    sweep.review_post_publish_samples.run()

    assert seen == [first.id, second.id]
