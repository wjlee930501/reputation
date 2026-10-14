"""사후 검수 일일 실행: FLAGGED 공개 글을 먼저, 별도 일일 상한 안에서 자동 교정한다."""

from __future__ import annotations

from app.core.config import settings
from app.services import published_correction as pc
from app.workers import post_publish_ai_review as sweep
from tests.test_published_auto_correction import _flagged_item, _hospital, _philosophy


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def unique(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _SweepDb:
    """호출 순서: Critical 백로그 조회 → 미전달 Critical 알림 조회 → 선발행 글(D37) 조회(실제 첫 단계)
    → FLAGGED 조회 →
    (교정이 켜져 있으면) UNCERTAIN 상위 재검수 조회 두 번(숫자 시각 표시, 옛 표시 키셋 한 쪽) → 비공개
    조회 → 표본 조회."""

    def __init__(self, flagged, deferred=()):
        self._batches = [[], [], list(deferred), flagged, [], [], [], []]
        self.order: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _stmt):
        return _Rows(self._batches.pop(0))

    def rollback(self):
        pass

    def commit(self):
        pass

    def get(self, _model, _id):
        return None


def _sweep_item():
    item = _flagged_item()
    item.hospital = _hospital()
    return item


def _run_sweep(monkeypatch, items, *, cap=2, deferred=(), order=None):
    import app.workers.tasks as tasks_module

    processed: list = []
    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", lambda: _SweepDb(items, deferred))

    def fake_review(db, item, hospital, philosophy, run_async, now):
        if order is not None:
            order.append(("deferred_review", item.id))
        return "REVIEWED"

    monkeypatch.setattr(sweep, "_review_and_apply", fake_review)
    monkeypatch.setattr(sweep, "_recover_flag_incident", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "_load_uncertain_scan_cursor", lambda: None)
    monkeypatch.setattr(sweep, "_save_uncertain_scan_cursor", lambda _cursor: None)
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: _philosophy())
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", cap)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 20)

    def fake_process(db, item, hospital, philosophy, *, run_async, now=None):
        processed.append(item.id)
        if order is not None:
            order.append(("flagged_correction", item.id))
        return "CORRECTED"

    monkeypatch.setattr(sweep, "process_flagged_post", fake_process)
    counts = sweep.review_post_publish_samples.run()
    return processed, counts


def test_daily_run_corrects_flagged_posts_within_its_own_cap(monkeypatch):
    items = [_sweep_item() for _ in range(3)]

    processed, counts = _run_sweep(monkeypatch, items, cap=2)

    assert processed == [items[0].id, items[1].id]
    assert counts["corrected"] == 2


def test_daily_run_skips_posts_whose_correction_is_finished(monkeypatch):
    done, fresh = _sweep_item(), _sweep_item()
    done.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["correction"] = {
        "finished": True,
        "rules_version": pc.CORRECTION_RULES_VERSION,
    }

    processed, _counts = _run_sweep(monkeypatch, [done, fresh], cap=5)

    assert processed == [fresh.id]


def test_cap_zero_turns_correction_off_but_keeps_sample_review(monkeypatch):
    processed, counts = _run_sweep(monkeypatch, [], cap=0)

    assert processed == []
    assert counts["corrected"] == 0


def test_d37_pre_published_posts_are_reviewed_before_any_flagged_correction(monkeypatch):
    """Codex P1-5: 선발행 글은 실제 첫 단계다 — FLAGGED 교정·상위 재검수보다 먼저 본다."""

    flagged, deferred = _sweep_item(), _sweep_item()
    order: list = []

    _run_sweep(monkeypatch, [flagged], cap=5, deferred=[deferred], order=order)

    assert order[0] == ("deferred_review", deferred.id)
    assert ("flagged_correction", flagged.id) in order


def test_the_d37_phase_has_its_own_failure_budget(monkeypatch):
    """선발행 단계가 연속 장애로 멈춰도 뒤 단계(교정)는 자기 예산으로 돈다."""

    deferred = [_sweep_item() for _ in range(5)]
    flagged = _sweep_item()
    order: list = []
    import app.workers.tasks as tasks_module

    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", lambda: _SweepDb([flagged], deferred))
    monkeypatch.setattr(sweep, "_load_uncertain_scan_cursor", lambda: None)
    monkeypatch.setattr(sweep, "_save_uncertain_scan_cursor", lambda _cursor: None)
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: _philosophy())
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", 2)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 20)

    def unavailable(db, item, *_a):
        order.append(("deferred_review", item.id))
        return "UNAVAILABLE"

    def fake_process(db, item, *_a, **_k):
        order.append(("flagged_correction", item.id))
        return "CORRECTED"

    monkeypatch.setattr(sweep, "_review_and_apply", unavailable)
    monkeypatch.setattr(sweep, "process_flagged_post", fake_process)

    sweep.review_post_publish_samples.run()

    assert [entry for entry in order if entry[0] == "deferred_review"] == [
        ("deferred_review", item.id) for item in deferred[:3]
    ]
    assert ("flagged_correction", flagged.id) in order
