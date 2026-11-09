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

    def scalar_one_or_none(self):
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





def test_trend_stops_at_a_month_judged_with_another_policy(monkeypatch):
    policies = {(2026, 11): NEW, (2026, 10): NEW, (2026, 9): NEW, (2026, 8): OLD}
    monkeypatch.setattr(tasks, "month_judge_policy_for", lambda _db, _h, y, m: policies.get((y, m)))
    monkeypatch.setattr(tasks, "month_rejudged_at", lambda *_args: None)
    db = _DB({(2026, 10): _matched(30.0), (2026, 9): _matched(10.0)})

    points = tasks._comparable_trend_points(db, "h", arrow.get("2026-11-01"), _matched(40.0))

    # 8월은 옛 판정이라 잇지 않는다(옛 요약은 판정 정책이 남아 있지 않다).
    assert points == [("2026-09", 30.0), ("2026-10", 40.0)]


def test_trend_ignores_old_summaries_of_rejudged_months(monkeypatch):
    monkeypatch.setattr(tasks, "month_judge_policy_for", lambda *_args: NEW)
    rejudged = datetime(2026, 11, 9, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(
        tasks,
        "month_rejudged_at",
        lambda _db, _h, y, m: rejudged if (y, m) in {(2026, 9), (2026, 8)} else None,
    )
    # 10월 보고서는 재판정 뒤 새 정책으로 계산했다. 9월 보고서(템플릿 갱신으로 옮긴 행 포함)는 재판정 전에 센 옛 요약이다.
    db = _DB({(2026, 10): {**_matched(30.0), "judge_policy": NEW}, (2026, 9): _matched(10.0)})

    points = tasks._comparable_trend_points(
        db, "h", arrow.get("2026-11-01"), {**_matched(40.0), "judge_policy": NEW}
    )

    assert points == [("2026-09", 30.0), ("2026-10", 40.0)]


def test_old_reports_without_rejudge_keep_their_trend(monkeypatch):
    monkeypatch.setattr(tasks, "month_judge_policy_for", lambda *_args: OLD)
    monkeypatch.setattr(tasks, "month_rejudged_at", lambda *_args: None)
    db = _DB({(2026, 10): _matched(30.0), (2026, 9): _matched(10.0), (2026, 8): {}})

    points = tasks._comparable_trend_points(db, "h", arrow.get("2026-11-01"), _matched(40.0))

    assert points == [("2026-08", 10.0), ("2026-09", 30.0), ("2026-10", 40.0)]
