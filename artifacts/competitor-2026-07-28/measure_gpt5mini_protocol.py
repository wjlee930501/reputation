"""Validate the shipping protocol on gpt-5-mini: 4 canonical queries × 3 repeats.

Recall-against-gpt-4o was the right question while gpt-4o was the reference. It
is the wrong question now: gpt-5-mini names *more* hospitals than gpt-4o (6.2 vs
4.6), so a low overlap says the two disagree, not that mini is worse. What the
published protocol actually needs is gpt-5-mini's stability with itself — how
much the same question moves between repeats, and whether 3 repeats separate the
hospitals that reliably appear from the ones that appear once.

Run twice over two region/specialty pairs so the answer does not rest on one.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_gpt5mini_protocol.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from measure_model_recall import ask  # noqa: E402
from query_mapper import build_queries  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5-mini"
# input $/1M, output $/1M — 검증된 공개 요금.
# 공식 요금표 확인값 (developers.openai.com/api/docs/pricing, 2026-07-28).
UNIT = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-mini-2025-08-07": (0.25, 2.00),
    "gpt-5.3-chat-latest": (1.25, 10.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-mini-2026-03-17": (0.75, 4.50),
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5.6-terra": (2.50, 15.00),
    "gpt-5.6-sol": (5.00, 30.00),
}
# 소비자 ChatGPT 서빙 모델은 요금표에 별도로 실리지 않는다. 미등재 모델은
# gpt-5 단가로 가정해 비용을 과소평가하지 않는다.
UNIT.setdefault(MODEL, (1.25, 10.00))
REPEATS = 3
CASES = [
    ("분당 · 외과", "정자동", ["탈장", "치질 수술"]),
    ("수서역 · 내과", "수서역", ["대장내시경", "허리디스크"]),
]


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


async def run_case(client: Any, label: str, region: str, keywords: list[str]) -> list[dict]:
    plan = build_queries(region, keywords)
    print(f"\n【{label}】 질의 {len(plan)}개 × {REPEATS}회 = {len(plan)*REPEATS}콜")
    for i, item in enumerate(plan, 1):
        print(f"  {i}. [{item['kind']}] {item['query']}")

    sem = asyncio.Semaphore(4)

    async def one(query: str, rep: int) -> dict | None:
        async with sem:
            try:
                row = await ask(client, MODEL, query)
                row["repeat"] = rep
                return row
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {query[:22]} {rep}회 {type(exc).__name__}")
                return None

    jobs = [one(item["query"], r + 1) for item in plan for r in range(REPEATS)]
    rows = [r for r in await asyncio.gather(*jobs) if r]
    if not rows:
        return []

    # 반복 간 안정성 — 질의별로 같은 질문을 3번 던졌을 때 얼마나 흔들리는가.
    per_query_jac: list[float] = []
    for item in plan:
        runs = [set(r["names"]) for r in rows if r["query"] == item["query"]]
        if len(runs) >= 2:
            per_query_jac.extend(jaccard(a, b) for a, b in combinations(runs, 2))

    freq = Counter(h for r in rows for h in r["names"])
    total = len(rows)
    zero = sum(1 for r in rows if not r["names"])
    once = sum(1 for c in freq.values() if c == 1)
    repeated = [h for h, c in freq.items() if c >= 2]

    print(f"\n  총 {total}회 실행 · 병원명 0개 실행 {zero}회")
    print(f"  반복 간 Jaccard 평균 {statistics.mean(per_query_jac):.3f}" if per_query_jac else "")
    print(f"  등장 병원 {len(freq)}곳 · 2회 이상 등장 {len(repeated)}곳 · 1회만 {once}곳 "
          f"({once/len(freq)*100:.0f}%)")
    print("  상위 등장:")
    for hospital, count in freq.most_common(6):
        bar = "█" * round(count / total * 20)
        print(f"    {hospital:24s} {count:>2}/{total:<3d} {count/total*100:>5.1f}% {bar}")

    for r in rows:
        r["case"] = label
    return rows


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print(f"모델 {MODEL} — 출시 예정 규약 검증")
    all_rows: list[dict] = []
    for label, region, keywords in CASES:
        all_rows.extend(await run_case(client, label, region, keywords))

    if all_rows:
        avg_in = sum(r["input_tokens"] for r in all_rows) / len(all_rows)
        avg_out = sum(r["output_tokens"] for r in all_rows) / len(all_rows)
        pin, pout = UNIT.get(MODEL, (0.25, 2.00))
        cost = avg_in * pin / 1e6 + avg_out * pout / 1e6 + 10.0 / 1e3
        per_diag = cost * 12
        print(f"\n실측 단가 — in {avg_in:,.0f} tok · out {avg_out:,.0f} tok")
        print(f"  콜당 ${cost:.5f} ({cost*1400:,.0f}원) · 12콜 진단 1건 "
              f"${per_diag:.3f} ({per_diag*1400:,.0f}원)")
        print(f"  월 300건 {per_diag*300*1400:,.0f}원")

    (HERE / "gpt5mini_protocol_measurement.json").write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
