"""저장된 원자료로 단가를 다시 계산한다 — 재측정 없이.

하네스 v2가 실행마다 토큰 수·검색 횟수·해석된 모델을 전부 남기므로, 요금표가
바뀌거나 계산 방식이 틀렸던 게 밝혀져도 API를 다시 부르지 않고 재계산할 수 있다.
v1에서 검색 과금을 콜당 1회로 잘못 매겼을 때 원자료가 덮어써져 재감사가 불가능했던
문제가 이 스크립트의 존재 이유다.

    ../../backend/.venv/bin/python recompute.py [--diagnosis-calls 12] [--monthly 300]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from harness import (  # noqa: E402
    GEMINI_FREE_SEARCHES_PER_MONTH,
    PRICING,
    SEARCH_USD,
    USD_KRW,
    unit_cost,
)

RUNS = HERE / "runs"


def load_runs() -> list[dict]:
    if not RUNS.exists():
        print(f"{RUNS} 없음 — campaign.py를 먼저 실행하라.", file=sys.stderr)
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RUNS.glob("*.json"))]


def analyze(run: dict, diagnosis_calls: int, monthly: int) -> dict | None:
    ok = [c for c in run["calls"] if not c.get("error")]
    if not ok:
        return None
    n = len(ok)
    avg_in = sum(c["input_tokens"] for c in ok) / n
    avg_out = sum(c["output_tokens"] for c in ok) / n
    avg_search = sum(c["search_calls"] for c in ok) / n
    resolved = sorted({c["resolved_model"] for c in ok if c["resolved_model"]})

    priced_as = run["requested_model"]
    if len(resolved) == 1 and resolved[0] in PRICING:
        priced_as = resolved[0]
    try:
        cost = unit_cost(priced_as, avg_in, avg_out, avg_search, run["platform"])
    except KeyError:
        cost = None

    freq = Counter(h for c in ok for h in c["names"])
    once = sum(1 for v in freq.values() if v == 1)

    row = {
        "model": run["requested_model"],
        "resolved": ",".join(resolved) or "?",
        "priced_as": priced_as if cost is not None else None,
        "platform": run["platform"],
        "variant": run["variant"],
        "n": n,
        "avg_search": round(avg_search, 2),
        "avg_names": round(sum(len(c["names"]) for c in ok) / n, 2),
        "zero_calls": sum(1 for c in ok if not c["names"]),
        "distinct": len(freq),
        "noise_pct": round(once / len(freq) * 100) if freq else None,
        "cost_call_krw": round(cost * USD_KRW, 1) if cost is not None else None,
        "cost_diag_krw": round(cost * diagnosis_calls * USD_KRW) if cost is not None else None,
    }

    if cost is not None:
        monthly_calls = diagnosis_calls * monthly
        monthly_usd = cost * monthly_calls
        # Gemini는 월 5,000 검색이 무료다. 콜당 단가에는 유료 단가를 넣어 두었으므로
        # 여기서 무료 구간만큼 환급한다.
        if run["platform"] == "gemini":
            total_searches = avg_search * monthly_calls
            free = min(total_searches, GEMINI_FREE_SEARCHES_PER_MONTH)
            monthly_usd -= free * SEARCH_USD["gemini"]
        row["cost_month_krw"] = round(max(monthly_usd, 0) * USD_KRW)
    else:
        row["cost_month_krw"] = None
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diagnosis-calls", type=int, default=12, help="진단 1건당 콜 수")
    ap.add_argument("--monthly", type=int, default=300, help="월 진단 건수")
    args = ap.parse_args()

    rows = [r for run in load_runs() if (r := analyze(run, args.diagnosis_calls, args.monthly))]
    if not rows:
        return 1

    rows.sort(key=lambda r: (r["variant"], r["cost_call_krw"] or 9e9))

    print(f"진단 1건 = {args.diagnosis_calls}콜 · 월 {args.monthly}건 기준")
    print("검색 과금: OpenAI $10/1k · Gemini $14/1k (월 5,000회 무료)\n")
    hdr = (f"{'모델':20s} {'조건':10s} {'검색':>5s} {'병원':>5s} {'잡음':>5s} "
           f"{'콜당':>8s} {'진단':>9s} {'월':>11s}  해석")
    print(hdr)
    print("─" * len(hdr))
    for r in rows:
        c = f"{r['cost_call_krw']:,.0f}원" if r["cost_call_krw"] is not None else "미상"
        d = f"{r['cost_diag_krw']:,}원" if r["cost_diag_krw"] is not None else "미상"
        m = f"{r['cost_month_krw']:,}원" if r["cost_month_krw"] is not None else "미상"
        noise = f"{r['noise_pct']}%" if r["noise_pct"] is not None else "-"
        print(
            f"{r['model']:20s} {r['variant']:10s} {r['avg_search']:>5.1f} "
            f"{r['avg_names']:>5.1f} {noise:>5s} {c:>8s} {d:>9s} {m:>11s}  {r['resolved']}"
        )

    print("\n※ '미상' = 별칭이라 해석 대상의 공식 요금을 특정할 수 없음. 고정 모델로 재측정 필요.")
    (HERE / "recomputed_costs.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
