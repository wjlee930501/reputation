"""사후 검수 일일 실행: FLAGGED 공개 글을 먼저, 별도 일일 상한 안에서 자동 교정한다."""

from __future__ import annotations

from app.core.config import settings
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
    """호출 순서: FLAGGED 조회 → 비공개 조회 → 표본 조회."""

    def __init__(self, flagged):
        self._batches = [flagged, [], []]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _stmt):
        return _Rows(self._batches.pop(0))


def _sweep_item():
    item = _flagged_item()
    item.hospital = _hospital()
    return item


def _run_sweep(monkeypatch, items, *, cap=2):
    import app.workers.tasks as tasks_module

    processed: list = []
    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "SyncSessionLocal", lambda: _SweepDb(items))
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: _philosophy())
    monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", cap)
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 20)

    def fake_process(db, item, hospital, philosophy, *, run_async, now=None):
        processed.append(item.id)
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
    done.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["correction"] = {"finished": True}

    processed, _counts = _run_sweep(monkeypatch, [done, fresh], cap=5)

    assert processed == [fresh.id]


def test_cap_zero_turns_correction_off_but_keeps_sample_review(monkeypatch):
    processed, counts = _run_sweep(monkeypatch, [], cap=0)

    assert processed == []
    assert counts["corrected"] == 0
