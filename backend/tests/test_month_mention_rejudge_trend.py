"""재판정한 달과 추이 그래프 — 판정 기준이 바뀐 것만으로 생긴 증감을 추이로 잇지 않는다."""

from datetime import datetime, timezone

import arrow

from app.workers import tasks

NEW = {"judge_prompt_fingerprint": "new", "judge_model": "haiku"}
OLD = {"judge_prompt_fingerprint": "old", "judge_model": "gpt-4o-mini"}


def _matched(prior):
    return {"comparison": {"status": "COMPARABLE", "reason": "MATCHED_COHORT", "prior_sov_pct": prior}}


class _Rows:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _DB:
    """달마다 저장된 최신 보고서(요약, 만든 시각)를 돌려준다."""

    def __init__(self, reports):
        self.reports = reports
        self.calls = []

    def execute(self, stmt):
        params = stmt.compile().params
        key = (params["period_year_1"], params["period_month_1"])
        self.calls.append(key)
        return _Rows(self.reports.get(key))


BUILT = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)


def test_trend_stops_at_a_month_judged_with_another_policy(monkeypatch):
    policies = {(2026, 11): NEW, (2026, 10): NEW, (2026, 9): NEW, (2026, 8): OLD}
    monkeypatch.setattr(tasks, "month_judge_policy_for", lambda _db, _h, y, m: policies.get((y, m)))
    monkeypatch.setattr(tasks, "month_rejudged_at", lambda *_args: None)
    db = _DB({(2026, 10): (_matched(30.0), BUILT), (2026, 9): (_matched(10.0), BUILT)})

    points = tasks._comparable_trend_points(db, "h", arrow.get("2026-11-01"), _matched(40.0))

    # 8월은 옛 판정이라 잇지 않는다.
    assert points == [("2026-09", 30.0), ("2026-10", 40.0)]


def test_trend_ignores_values_saved_before_the_months_were_rejudged(monkeypatch):
    monkeypatch.setattr(tasks, "month_judge_policy_for", lambda *_args: NEW)
    rejudged = datetime(2026, 11, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(
        tasks,
        "month_rejudged_at",
        lambda _db, _h, y, m: rejudged if (y, m) in {(2026, 9), (2026, 8)} else None,
    )
    # 10월 보고서의 9월 대비 값은 재판정 뒤에 만들었지만, 9월 보고서의 8월 대비 값은 재판정 전에 만들었다.
    db = _DB({(2026, 10): (_matched(30.0), BUILT.replace(day=12)), (2026, 9): (_matched(10.0), BUILT)})

    points = tasks._comparable_trend_points(db, "h", arrow.get("2026-11-01"), _matched(40.0))

    assert points == [("2026-09", 30.0), ("2026-10", 40.0)]
