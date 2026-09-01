"""언급률 비율 추정의 불확실성 — Wilson 점수 구간과 전월 대비 유의성 판정.

**왜 필요한가.** 월간 헤드라인은 이진 관측(언급됐다/아니다)의 평균이다. 표본이
얇으면 아무 일도 하지 않아도 수치가 오르내리고, 그 흔들림을 "성과"로 보고하면
다음 달 하락이 해지 대화가 된다. 이 모듈은 "이 변화가 표본 노이즈로 설명되는가"를
상수(예전의 ``NORMAL_FLUCTUATION = 5``)가 아니라 이번 달 실제 표본 크기로 답한다.

**왜 Wilson인가.** 정규근사(Wald) 구간은 p가 0이나 1에 가까울 때 폭이 0으로
붕괴해 "오차 없음"이라는 거짓말을 만든다. 월간 측정에는 언급 0회 셀이 흔하므로
그 구간은 쓸 수 없다. Wilson 구간은 k=0/k=n에서도 유한한 폭을 준다.

**한계(문서화된 낙관 편향).** 여기서 trials는 셀(질문×AI 서비스)당 반복 측정을
모두 편 개수다. 같은 셀의 반복은 서로 독립이 아니므로(같은 질문·같은 문서 풀),
이 구간은 진짜 불확실성의 **하한**이다 — 즉 실제 오차 범위는 이보다 넓다.
그러므로 유의성 판정은 보수적인 쪽(구간 비중첩)으로만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Literal

# 95% 양측 정규분위수. 리포트 문구가 "95%"를 말하므로 상수를 여기 하나만 둔다.
Z_95 = 1.959963984540054

DeltaSignificance = Literal["SIGNIFICANT_UP", "SIGNIFICANT_DOWN", "WITHIN_NOISE"]


@dataclass(frozen=True, slots=True)
class ProportionInterval:
    """비율 추정치와 95% Wilson 구간. 모든 값은 0~1 스케일이다."""

    successes: int
    trials: int
    point: float
    low: float
    high: float

    @property
    def half_width(self) -> float:
        """구간 절반 폭 — "±X" 표기에 쓴다."""
        return (self.high - self.low) / 2

    @property
    def point_pct(self) -> float:
        return round(self.point * 100, 2)

    @property
    def low_pct(self) -> float:
        return round(self.low * 100, 2)

    @property
    def high_pct(self) -> float:
        return round(self.high * 100, 2)

    @property
    def margin_of_hundred(self) -> int:
        """'100번 중 N번' 단위의 오차 범위(±번)."""
        return round(self.half_width * 100)


def wilson_interval(
    successes: int, trials: int, *, z: float = Z_95
) -> ProportionInterval | None:
    """k/n의 Wilson 점수 구간. 표본이 없으면 구간을 지어내지 않고 None을 준다."""
    if trials <= 0:
        return None
    if successes < 0 or successes > trials:
        raise ValueError(  # copy-guard: internal-only
            f"successes out of range: {successes}/{trials}"
        )
    k = float(successes)
    n = float(trials)
    z_sq = z * z
    denominator = n + z_sq
    center = (k + z_sq / 2) / denominator
    half = (z / denominator) * sqrt(k * (n - k) / n + z_sq / 4)
    return ProportionInterval(
        successes=successes,
        trials=trials,
        point=k / n,
        low=max(0.0, center - half),
        high=min(1.0, center + half),
    )


def delta_significance(
    current_successes: int,
    current_trials: int,
    prior_successes: int,
    prior_trials: int,
    *,
    z: float = Z_95,
) -> DeltaSignificance | None:
    """전월 대비 변화가 표본 노이즈로 설명되는지.

    판정은 **두 95% Wilson 구간이 겹치지 않을 때만** 유의하다고 본다. 이는
    두 비율 z-검정보다 보수적이다(같은 α에서 기각 영역이 더 좁다) — 셀 내부
    반복의 상관 때문에 trials가 실제보다 크게 잡히는 낙관 편향을 상쇄한다.

    어느 한쪽이라도 표본이 없으면 판정하지 않고 None을 준다.
    """
    current = wilson_interval(current_successes, current_trials, z=z)
    prior = wilson_interval(prior_successes, prior_trials, z=z)
    if current is None or prior is None:
        return None
    if current.low > prior.high:
        return "SIGNIFICANT_UP"
    if current.high < prior.low:
        return "SIGNIFICANT_DOWN"
    return "WITHIN_NOISE"
