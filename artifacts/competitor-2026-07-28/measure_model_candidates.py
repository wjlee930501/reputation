"""Find a cheaper model that answers like gpt-4o.

gpt-4o-mini already failed this test (0.229 recall — it named only 2 of every 10
hospitals gpt-4o named). But the account has newer families that did not exist
when gpt-4o shipped, and input price is what matters: measured calls run ~20.8k
input tokens against ~0.9k output, so 73% of the bill is input.

Each candidate answers the same 12 queries the gpt-4o baseline already answered
(model_recall_measurement.json), so the comparison costs one run per candidate
rather than two.

Recall = of the hospitals gpt-4o named, how many did the candidate also name.
That is the number that decides whether swapping models shifts every hospital's
measured mention rate.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_model_candidates.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from measure_model_recall import ask  # noqa: E402

# input $/1M, output $/1M — verified from the OpenAI pricing page 2026-07-28.
PRICING = {
    "gpt-4o": (2.50, 10.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.4-mini": (0.75, 4.50),
}
WEB_SEARCH_CALL = 10.00 / 1000  # $10 / 1k calls
USD_KRW = 1400

CANDIDATES = ["gpt-5-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5"]
if len(sys.argv) > 1:
    CANDIDATES = sys.argv[1:]
    # 소비자 ChatGPT가 서빙하는 모델은 요금표에 따로 실리지 않는 경우가 있다.
    # 미등재 모델은 gpt-5 단가로 가정해 비용을 과소평가하지 않는다.
    for _m in CANDIDATES:
        PRICING.setdefault(_m, PRICING["gpt-5"])


def load_baseline() -> dict[str, set[str]]:
    rows = json.loads((HERE / "model_recall_measurement.json").read_text(encoding="utf-8"))
    return {r["query"]: set(r["names"]) for r in rows if r["model"] == "gpt-4o"}


def unit_cost(model: str, avg_in: float, avg_out: float) -> float:
    pin, pout = PRICING[model]
    return avg_in * pin / 1e6 + avg_out * pout / 1e6 + WEB_SEARCH_CALL


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    baseline = load_baseline()
    queries = list(baseline)
    print(f"기준: gpt-4o, 질의 {len(queries)}개 (기존 측정 재사용)\n")

    results: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for model in CANDIDATES:
        print(f"── {model}", flush=True)
        # GPT-5 계열은 추론 시간이 길어 순차 호출이면 수십 분이 걸린다. 질의는 서로
        # 독립이므로 동시에 던지되, 레이트리밋을 피해 4개로 제한한다.
        sem = asyncio.Semaphore(4)

        async def one(q: str) -> dict[str, Any] | str:
            async with sem:
                try:
                    return await ask(client, model, q)
                except Exception as exc:  # noqa: BLE001
                    return f"{type(exc).__name__}: {str(exc)[:90]}"

        settled = await asyncio.gather(*(one(q) for q in queries))
        rows = [r for r in settled if isinstance(r, dict)]
        for err in (r for r in settled if isinstance(r, str)):
            print(f"   FAIL {err}", flush=True)
        results.extend(rows)
        if not rows:
            print("   전부 실패 — 이 모델은 web_search 경로를 지원하지 않는 것으로 보임\n")
            continue

        n = len(rows)
        avg_in = sum(r["input_tokens"] for r in rows) / n
        avg_out = sum(r["output_tokens"] for r in rows) / n
        avg_names = sum(len(r["names"]) for r in rows) / n
        avg_src = sum(r["sources"] for r in rows) / n

        recalls, jaccards = [], []
        for r in rows:
            base, cand = baseline.get(r["query"], set()), set(r["names"])
            if base:
                recalls.append(len(base & cand) / len(base))
            if base | cand:
                jaccards.append(len(base & cand) / len(base | cand))

        cost = unit_cost(model, avg_in, avg_out)
        base_cost = unit_cost("gpt-4o", 20836, 906)
        rec = sum(recalls) / len(recalls) if recalls else 0.0
        summary.append(
            {
                "model": model,
                "n": n,
                "recall": rec,
                "jaccard": sum(jaccards) / len(jaccards) if jaccards else 0.0,
                "avg_names": avg_names,
                "avg_sources": avg_src,
                "avg_in": avg_in,
                "avg_out": avg_out,
                "cost": cost,
                "saving": base_cost / cost if cost else 0.0,
            }
        )
        print(
            f"   회수율 {rec:.3f}  병원 {avg_names:.1f}개  출처 {avg_src:.1f}개  "
            f"in {avg_in:,.0f} tok  콜당 ${cost:.5f} ({cost*USD_KRW:,.0f}원)\n"
        )

    (HERE / "model_candidates_measurement.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    base_cost = unit_cost("gpt-4o", 20836, 906)
    print("=" * 78)
    print(f"{'모델':14s} {'회수율':>7s} {'Jaccard':>8s} {'병원수':>7s} {'콜당':>10s} {'4o대비':>8s}")
    print(f"{'gpt-4o (기준)':14s} {1.000:>7.3f} {1.000:>8.3f} {4.6:>7.1f} "
          f"{base_cost*USD_KRW:>9,.0f}원 {'1.0x':>8s}")
    for s in sorted(summary, key=lambda x: -x["recall"]):
        print(
            f"{s['model']:14s} {s['recall']:>7.3f} {s['jaccard']:>8.3f} {s['avg_names']:>7.1f} "
            f"{s['cost']*USD_KRW:>9,.0f}원 {s['saving']:>7.1f}x"
        )
    print("\n판정 기준: 회수율 0.8 이상이어야 지표 기준선이 흔들리지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
