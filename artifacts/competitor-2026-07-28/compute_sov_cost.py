"""Turn the measured token counts into a cost model for the public diagnosis funnel.

Token counts come from sov_cost_measurement.json (real API responses, not estimates).
Unit prices are the vendors' published rates, verified 2026-07-28:
  gpt-4o            $2.50 / 1M input, $10.00 / 1M output
  web_search tool   $10.00 / 1k calls  (search content tokens billed at model rates,
                                        i.e. already inside the measured input count)
  gemini-2.5-flash  $0.30 / 1M input,  $2.50 / 1M output
  Google Search grounding  1,500 requests/day free on the paid tier, then $35 / 1k
"""
from __future__ import annotations

import json
from pathlib import Path

USD_KRW = 1400  # rough working rate for sizing, not accounting

PRICE = {
    "gpt-4o": {"in": 2.50 / 1e6, "out": 10.00 / 1e6, "tool": 10.00 / 1e3},
    # Same measured token profile, cheaper model — the single biggest lever.
    "gpt-4o-mini": {"in": 0.15 / 1e6, "out": 0.60 / 1e6, "tool": 10.00 / 1e3},
    "gemini-flash": {"in": 0.30 / 1e6, "out": 2.50 / 1e6, "tool": 0.0},
}

GEMINI_GROUNDING_FREE_RPD = 1500


def load_averages() -> dict[str, dict[str, float]]:
    rows = json.loads(
        (Path(__file__).with_name("sov_cost_measurement.json")).read_text(encoding="utf-8")
    )
    out: dict[str, dict[str, float]] = {}
    for platform in ("openai", "gemini"):
        subset = [r for r in rows if r["platform"] == platform]
        n = len(subset)
        out[platform] = {
            "n": n,
            "in": sum(r["input_tokens"] for r in subset) / n,
            "out": sum(r["output_tokens"] for r in subset) / n,
            "in_min": min(r["input_tokens"] for r in subset),
            "in_max": max(r["input_tokens"] for r in subset),
        }
    return out


def per_call(model: str, avg: dict[str, float]) -> float:
    p = PRICE[model]
    return avg["in"] * p["in"] + avg["out"] * p["out"] + p["tool"]


def krw(usd: float) -> str:
    return f"{usd * USD_KRW:,.0f}원"


def main() -> None:
    avg = load_averages()
    oa, gm = avg["openai"], avg["gemini"]

    print("측정값 (실제 API 응답, n=4씩)")
    print(f"  ChatGPT+웹서치  input {oa['in']:>8,.0f} tok  (범위 {oa['in_min']:,}~{oa['in_max']:,})"
          f"  output {oa['out']:>5,.0f} tok")
    print(f"  Gemini+검색     input {gm['in']:>8,.0f} tok  (범위 {gm['in_min']:,}~{gm['in_max']:,})"
          f"  output {gm['out']:>5,.0f} tok")

    print("\n콜당 단가")
    rows = [
        ("gpt-4o + web_search", per_call("gpt-4o", oa)),
        ("gpt-4o-mini + web_search", per_call("gpt-4o-mini", oa)),
        ("gemini-flash + grounding(무료구간)", per_call("gemini-flash", gm)),
        ("gemini-flash + grounding(유료구간)", per_call("gemini-flash", gm) + 35.0 / 1e3),
    ]
    for name, cost in rows:
        print(f"  {name:38s} ${cost:.5f}  ({krw(cost)})")

    c_oa = per_call("gpt-4o", oa)
    c_oa_mini = per_call("gpt-4o-mini", oa)
    c_gm = per_call("gemini-flash", gm)

    print("\n진단 1건 시나리오")
    scenarios = [
        ("A. 균형 12질의×2플랫폼×3회 (72콜)", 36 * c_oa + 36 * c_gm, 36),
        ("B. 반복 2회로 축소 (48콜)", 24 * c_oa + 24 * c_gm, 24),
        ("C. Gemini 주력 — GPT 6질의×1회 + Gemini 12질의×3회 (42콜)", 6 * c_oa + 36 * c_gm, 6),
        ("D. GPT를 mini로 교체, 12×2×3 (72콜)", 36 * c_oa_mini + 36 * c_gm, 36),
        ("E. 최소 8질의×2플랫폼×2회 (32콜)", 16 * c_oa + 16 * c_gm, 16),
    ]
    for name, cost, _ in scenarios:
        print(f"  {name:52s} ${cost:>6.3f}  ({krw(cost)})")

    print("\n월간 비용 (시나리오별 · 진단 건수)")
    header = f"  {'시나리오':52s}" + "".join(f"{n:>12}건" for n in (50, 100, 300, 500))
    print(header)
    for name, cost, _ in scenarios:
        cells = "".join(f"{krw(cost * n):>13}" for n in (50, 100, 300, 500))
        print(f"  {name:52s}{cells}")

    print("\n제약 확인")
    for name, cost, gm_calls in scenarios:
        # Gemini grounding free tier is a per-day request cap.
        daily_cap = GEMINI_GROUNDING_FREE_RPD // max(gm_calls, 1) if gm_calls else 0
        print(f"  {name[:2]} · Gemini 무료 grounding 소진 전 최대 {daily_cap:>3}건/일")

    print(f"\n  COST_GUARD 월 20,000 질의 기준 상한:")
    for name, _, _ in scenarios:
        pass
    for name, cost, _ in scenarios:
        calls = int(name.split("(")[-1].rstrip("콜)")) if "(" in name else 0
        if calls:
            print(f"    {name[:2]} · {20000 // calls:>4}건/월 에서 차단  (비용 {krw(cost * (20000 // calls))})")


if __name__ == "__main__":
    main()
