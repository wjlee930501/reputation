"""코호트 비용 경고선은 비용 한도에서 계산한다 — 손으로 맞추는 병원 수 상수가 아니다(Phase 1-6c, D8)."""

from __future__ import annotations

from app.core.config import Settings
from app.services import sov_tracking_set


def _threshold_settings(monkeypatch, *, daily=4260, monthly=4260, repeats=5, key="k"):
    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_DAILY_SOV_QUERIES", daily)
    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", monthly)
    monkeypatch.setattr(sov_tracking_set.settings, "SOV_REPEAT_COUNT_WEEKLY", repeats)
    monkeypatch.setattr(sov_tracking_set.settings, "OPENROUTER_API_KEY", key)


def test_threshold_is_min_budget_over_one_fixed_set(monkeypatch):
    """min(일·월 상한) ÷ (15문항 × 플랫폼 × 반복 × 3단계) — 답변·자사·경쟁 판정을 묶는다."""

    _threshold_settings(monkeypatch)
    # 15×2×5×3 = 450호출 → 4260 // 450 = 9곳
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 9

    _threshold_settings(monkeypatch, daily=3000, monthly=9000)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 6

    _threshold_settings(monkeypatch, daily=9000, monthly=9000)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 20

    # 반복 10회면 세트 1건이 900호출 — 같은 상한으로 10곳.
    _threshold_settings(monkeypatch, daily=9000, monthly=9000, repeats=10)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 10

    # 게이트웨이 키가 없으면 플랫폼 하나 — 225호출 → 4260 // 225 = 18곳.
    _threshold_settings(monkeypatch, key=None)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 18

    _threshold_settings(monkeypatch, daily=100, monthly=100)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 1


def test_hand_tuned_cohort_limit_setting_is_gone():
    assert "SOV_MONTHLY_COHORT_LIMIT" not in Settings.model_fields


def test_left_over_env_value_is_ignored(monkeypatch):
    """운영 env에 남은 옛 값은 무해하다(Settings extra="ignore") — 배포 기록에서 지운다."""

    monkeypatch.setenv("SOV_MONTHLY_COHORT_LIMIT", "7")
    assert not hasattr(Settings(), "SOV_MONTHLY_COHORT_LIMIT")


def test_over_limit_incident_advice_names_the_budget_not_a_hand_limit():
    import inspect

    from app.workers import monthly_cohort_incidents

    source = inspect.getsource(monthly_cohort_incidents.open_monthly_cohort_over_limit)
    assert "SOV_MONTHLY_COHORT_LIMIT" not in source
    assert "COST_GUARD_MONTHLY_SOV_QUERIES" in source
