"""End-to-end check of the intake → query mapping on a real lead shape.

Uses a Seoul location and a specialty deliberately different from the earlier
분당/외과 runs, so the conclusion is not resting on one region-specialty pair.
The hospital name from intake is never placed in a query — it exists only to
judge mentions — so this run measures the query set itself.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_mapper_endtoend.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from measure_model_recall import ask  # noqa: E402
from query_mapper import build_queries  # noqa: E402

REPEATS = 3
# 신청 폼이 받는 그대로. 병원명은 판정용이지 질의 재료가 아니다.
INTAKE = {"region": "수서역", "keywords": ["대장내시경", "치질 수술", "허리디스크"]}


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    plan = build_queries(INTAKE["region"], INTAKE["keywords"])
    print(f"신청 입력: 지역={INTAKE['region']} 키워드={INTAKE['keywords']}")
    print(f"생성 질의 {len(plan)}개 × {REPEATS}회 = {len(plan)*REPEATS}콜\n")
    for i, item in enumerate(plan, 1):
        print(f"  {i}. [{item['kind']}] {item['query']}")

    sem = asyncio.Semaphore(4)

    async def one(query: str, rep: int) -> dict[str, Any] | None:
        async with sem:
            try:
                row = await ask(client, "gpt-4o", query)
                row["repeat"] = rep
                return row
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {query[:24]} {rep}회 {type(exc).__name__}")
                return None

    jobs = [one(item["query"], r + 1) for item in plan for r in range(REPEATS)]
    rows = [r for r in await asyncio.gather(*jobs) if r]

    if not rows:
        print("\n측정 실패")
        return 1

    print(f"\n집계 — 총 {len(rows)}회 실행")
    freq = Counter(h for r in rows for h in r["names"])
    zero = [r for r in rows if not r["names"]]
    for hospital, count in freq.most_common(10):
        bar = "█" * round(count / len(rows) * 20)
        print(f"  {hospital:26s} {count:>2}/{len(rows):<3d} {count/len(rows)*100:>5.1f}% {bar}")
    once = sum(1 for c in freq.values() if c == 1)
    print(f"\n  병원명 0개였던 실행: {len(zero)}/{len(rows)}회")
    print(f"  1회만 등장한 병원: {once}/{len(freq)}곳 ({once/len(freq)*100:.0f}%)")

    print("\n질의별 기여")
    for item in plan:
        subset = [r for r in rows if r["query"] == item["query"]]
        names = set().union(*(set(r["names"]) for r in subset)) if subset else set()
        print(f"  [{item['kind']:5s}] 등장 {len(names):>2}곳  {item['query'][:38]}")

    (HERE / "mapper_endtoend_measurement.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
