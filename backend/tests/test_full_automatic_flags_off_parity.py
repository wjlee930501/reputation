"""W4 등급·D35 알림 플래그가 둘 다 꺼진 기본값에서 사후 검수·게이트가 main과 같은가(Codex 4차).

이 파일은 플래그를 켜지 않는다 — 운영 기본값(`CONTENT_FINDING_TIERS_ENABLED=False`,
`POST_PUBLISH_CRITICAL_ALERTS_ENABLED=False`)을 그대로 두고 main이 기대하는 동작을 고정한다.
D36(참고자료 없이 발행)·D37(판정 없이 먼저 발행)은 켜진 채다.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import Settings, settings
from app.services import content_publication
from app.workers import incident_backlog
from app.workers import post_publish_ai_review as sweep
from app.workers.generation_retry_policy import has_model_declared_hard_finding
from tests.test_published_auto_correction import _flagged_item, _hospital, _philosophy


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", False)
    monkeypatch.setattr(settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", False)


def test_both_flags_default_on_and_this_module_switches_them_off() -> None:
    # 2026-10-14 재설계 뒤 기본 켜짐 — 이 모듈은 롤백 스위치(끔)가 main과 같은지 본다.
    assert Settings.model_fields["CONTENT_FINDING_TIERS_ENABLED"].default is True
    assert Settings.model_fields["POST_PUBLISH_CRITICAL_ALERTS_ENABLED"].default is True
    assert settings.CONTENT_FINDING_TIERS_ENABLED is False
    assert settings.POST_PUBLISH_CRITICAL_ALERTS_ENABLED is False


# ── 사후 검수 스윕 ─────────────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def unique(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Db:
    """플래그가 꺼져 있으면 Critical 백로그·미전달 알림 조회가 없다. 호출 순서: 선발행(D37) →
    FLAGGED → UNCERTAIN 상위 재검수 두 번 → 비공개 → 표본. 교정 상한이 0이면 가운데 셋이 없다."""

    def __init__(self, *, deferred=(), flagged=(), samples=(), correction=True):
        middle = [list(flagged), [], []] if correction else []
        self._batches = [list(deferred), *middle, [], list(samples)]
        self._items = {item.id: item for item in [*deferred, *flagged, *samples]}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _stmt):
        return _Rows(self._batches.pop(0) if self._batches else [])

    def rollback(self):
        pass

    def commit(self):
        pass

    def get(self, _model, item_id):
        return self._items.get(item_id)


def _post():
    item = _flagged_item()
    item.hospital = _hospital()
    return item


@pytest.fixture
def run(monkeypatch):
    import app.workers.tasks as tasks_module

    calls: dict[str, list] = {"review": [], "correct": [], "incident": []}
    monkeypatch.setattr(sweep, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "_recover_flag_incident", lambda *_a, **_k: None)
    monkeypatch.setattr(sweep, "_load_uncertain_scan_cursor", lambda: None)
    monkeypatch.setattr(sweep, "_save_uncertain_scan_cursor", lambda _cursor: None)
    monkeypatch.setattr(tasks_module, "_generation_philosophy_sync", lambda *_a: _philosophy())
    monkeypatch.setattr(settings, "POST_PUBLISH_AI_REVIEW_DAILY_CAP", 20)
    monkeypatch.setattr(
        sweep,
        "_open_flag_incident",
        lambda item, *_a, **_k: calls["incident"].append(item.id),
    )
    for forbidden in ("send_critical_backlog_digest", "deliver_pending_critical_alerts"):
        monkeypatch.setattr(
            sweep, forbidden, lambda *_a, **_k: pytest.fail("alerts must stay off")
        )

    def _run(db, *, correction_cap, review, correct, clock=None):
        monkeypatch.setattr(sweep, "SyncSessionLocal", lambda: db)
        monkeypatch.setattr(settings, "POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP", correction_cap)
        if clock is not None:
            monkeypatch.setattr(sweep.time, "monotonic", lambda: clock[0])

        def fake_review(_db, item, *_a):
            calls["review"].append(item.id)
            return review(item)

        def fake_correct(_db, item, *_a, **_k):
            calls["correct"].append(item.id)
            return correct(item)

        monkeypatch.setattr(sweep, "_review_and_apply", fake_review)
        monkeypatch.setattr(sweep, "process_flagged_post", fake_correct)
        return sweep.review_post_publish_samples.run()

    _run.calls = calls
    return _run


def test_three_correction_outages_also_stop_the_sample_phase(run) -> None:
    """main: 교정 검수가 연달아 3번 못 보면 그날 표본 검수도 접는다(계수를 다시 세지 않는다)."""

    flagged = [_post() for _ in range(3)]
    samples = [_post() for _ in range(2)]

    run(
        _Db(flagged=flagged, samples=samples),
        correction_cap=10,
        review=lambda _item: "REVIEWED",
        correct=lambda _item: "UNAVAILABLE",
    )

    assert run.calls["correct"] == [item.id for item in flagged]
    assert run.calls["review"] == []


def test_the_d37_phase_does_not_spend_the_existing_2000_second_budget(run) -> None:
    """D37 단계의 600초는 자기 예산이다 — 뒤 교정 단계는 main처럼 2,000초를 다 쓴다."""

    deferred = [_post()]
    flagged = [_post() for _ in range(2)]
    clock = [0]
    ticks = iter([2100, 2200])

    def review(_item):
        clock[0] = 600
        return "REVIEWED"

    def correct(_item):
        clock[0] = next(ticks)
        return "CORRECTED"

    run(
        _Db(deferred=deferred, flagged=flagged),
        correction_cap=2,
        review=review,
        correct=correct,
        clock=clock,
    )

    assert run.calls["correct"] == [item.id for item in flagged]


@pytest.mark.parametrize("correction_cap", [0, 2])
def test_a_d37_post_flagged_after_publishing_opens_the_quiet_incident(run, correction_cap) -> None:
    """main의 표본 경로처럼 교정이 아직 맡지 않은 FLAGGED는 조용한 사고를 연다."""

    deferred = _post()

    counts = run(
        _Db(deferred=[deferred], correction=correction_cap > 0),
        correction_cap=correction_cap,
        review=lambda _item: "FLAGGED",
        correct=lambda _item: "CORRECTED",
    )

    assert counts["deferred_flagged"] == 1
    assert run.calls["incident"] == [deferred.id]


def test_a_sample_flagged_without_correction_opens_the_quiet_incident(run) -> None:
    sample = _post()

    run(
        _Db(samples=[sample], correction=False),
        correction_cap=0,
        review=lambda _item: "FLAGGED",
        correct=lambda _item: "CORRECTED",
    )

    assert run.calls["incident"] == [sample.id]


def test_a_corrected_sample_opens_no_incident(run) -> None:
    sample = _post()

    run(
        _Db(samples=[sample]),
        correction_cap=2,
        review=lambda _item: "FLAGGED",
        correct=lambda _item: "CORRECTED",
    )

    assert run.calls["correct"] == [sample.id]
    assert run.calls["incident"] == []


# ── 사고 근거 정리 ─────────────────────────────────────────────────────────────


def test_backlog_scan_does_not_include_the_post_publish_types() -> None:
    resolvers = incident_backlog.active_resolvers()

    assert resolvers is incident_backlog.RESOLVERS
    assert "POST_PUBLISH_REVIEW_FLAGGED" not in resolvers
    assert "POST_PUBLISH_CRITICAL_FINDING" not in resolvers


def test_backlog_scan_includes_them_only_with_alerts_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", True)

    resolvers = incident_backlog.active_resolvers()

    assert resolvers["POST_PUBLISH_REVIEW_FLAGGED"] is incident_backlog._post_publish_flag_settled
    assert resolvers["POST_PUBLISH_CRITICAL_FINDING"] is incident_backlog._post_publish_flag_settled


# ── Essence 재검증 ────────────────────────────────────────────────────────────


def _revalidate(monkeypatch):
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda *_a: SimpleNamespace(status="ALIGNED", summary={"blocking": False}),
    )
    item = _post()
    item.essence_check_summary[content_publication.POST_PUBLISH_CRITICAL_ALERT_KEY] = {
        "requested_at": "2026-10-14T00:00:00+00:00"
    }
    content_publication.apply_essence_revalidation(item, SimpleNamespace(id=uuid.uuid4()))
    return item.essence_check_summary


def test_essence_revalidation_drops_the_flag_like_main(monkeypatch) -> None:
    summary = _revalidate(monkeypatch)

    assert sweep.POST_PUBLISH_FLAG_KEY not in summary
    assert content_publication.POST_PUBLISH_CRITICAL_ALERT_KEY not in summary


@pytest.mark.parametrize("flag", ["CONTENT_FINDING_TIERS_ENABLED", "POST_PUBLISH_CRITICAL_ALERTS_ENABLED"])
def test_essence_revalidation_keeps_the_flag_only_when_a_flag_is_on(monkeypatch, flag) -> None:
    monkeypatch.setattr(settings, flag, True)

    summary = _revalidate(monkeypatch)

    assert summary[sweep.POST_PUBLISH_FLAG_KEY]["status"] == "FLAGGED"
    assert content_publication.POST_PUBLISH_CRITICAL_ALERT_KEY in summary


# ── 생성 게이트 ───────────────────────────────────────────────────────────────


def test_any_model_hard_finding_counts_like_main() -> None:
    review = {
        "status": "REVISE",
        "blocking": True,
        "findings": [{"severity": "HARD", "kind": "STYLE", "message": "문체", "quote": "x"}],
    }

    assert has_model_declared_hard_finding(review)
