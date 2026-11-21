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
    """min(일·월 상한) ÷ (30문항 × 플랫폼 × 반복 × 1.25단위) — 답변 1 + 가끔 자사 보조 판정.

    경쟁 병원은 규칙 대조라 호출이 없다(2026-11-09). 예전 ×3은 30문항이면 일 9,000으로 10곳만
    잡아 12/1에 거짓 '한도 초과' 사고를 연다(측정 키워드 30개 설계 §4).
    """

    _threshold_settings(monkeypatch)
    # 30×2×5×1.25 = 375호출 → 4260 // 375 = 11곳
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 11

    _threshold_settings(monkeypatch, daily=3000, monthly=9000)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 8

    # 운영 일 9,000 · 월 13,500 — 새벽 하루가 일 한도에 묶인다.
    _threshold_settings(monkeypatch, daily=9000, monthly=13500)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 24

    # 반복 10회면 세트 1건이 750호출 — 같은 상한으로 12곳.
    _threshold_settings(monkeypatch, daily=9000, monthly=9000, repeats=10)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 12

    # 게이트웨이 키가 없으면 플랫폼 하나 — 187호출 → 4260 // 187 = 22곳.
    _threshold_settings(monkeypatch, key=None)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 22

    # 옛 15문항 세트로 물으면 그만큼 여유가 크다.
    _threshold_settings(monkeypatch, daily=9000, monthly=9000)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold(15) == 48

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
