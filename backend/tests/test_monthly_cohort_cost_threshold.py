"""코호트 비용 경고선은 비용 한도에서 계산한다 — 손으로 맞추는 병원 수 상수가 아니다(Phase 1-6c, D8)."""

from __future__ import annotations

from app.core.config import Settings
from app.services import sov_tracking_set


def test_threshold_is_monthly_budget_over_one_fixed_set(monkeypatch):
    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", 4260)
    # 15문항 × 2플랫폼 × 5반복 = 150호출 → 4260 // 150 = 28곳
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 28

    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", 100)
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
