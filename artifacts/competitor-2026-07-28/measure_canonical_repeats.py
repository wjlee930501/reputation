"""How stable are the two canonical patient query shapes across repeats?

The 12-query redundancy run showed 68% of hospitals appeared in exactly one
query — adding query variants samples variance, it does not widen coverage. The
corrective is fewer, canonical queries measured more times. This measures the
two shapes patients actually use:

    A. {지역} 근처 {진료과} 병원 추천해줘
    B. {증상/질환}이 있는데, {지역} 근처 병원 어디로 가야해?

and reports repeat-to-repeat stability, which is the number our published
protocol has to state. Each repeat is an independent request, so this is the
same thing SOV_REPEAT_COUNT buys us in production.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_canonical_repeats.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from measure_model_recall import ask  # noqa: E402 — identical extraction, identical call shape

REPEATS = 5
PATTERNS = [
    ("A. 진료과형", "분당 근처 외과 병원 추천해줘"),
    ("B. 증상형", "탈장이 있는데, 분당 근처 병원 어디로 가야해?"),
]


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    all_rows: list[dict[str, Any]] = []
    for label, query in PATTERNS:
        print(f"\n{label} — {query}")
        runs: list[set[str]] = []
        for i in range(REPEATS):
            try:
                row = await ask(client, "gpt-4o", query)
                row["pattern"] = label
                row["repeat"] = i + 1
                all_rows.append(row)
                names = set(row["names"])
                runs.append(names)
                print(f"  {i+1}회차  병원 {len(names):>2}개  출처 {row['sources']:>2}개  "
                      f"in={row['input_tokens']:>6}  {', '.join(sorted(names)[:3])}"
                      + ("…" if len(names) > 3 else ""))
            except Exception as exc:  # noqa: BLE001
                print(f"  {i+1}회차  FAIL {type(exc).__name__}: {str(exc)[:80]}")

        if len(runs) < 2:
            continue
        pairs = [jaccard(a, b) for a, b in combinations(runs, 2)]
        union = set().union(*runs)
        freq = Counter(n for r in runs for n in r)
        stable = [n for n, c in freq.items() if c >= len(runs) * 0.6]
        once = [n for n, c in freq.items() if c == 1]

        print(f"  ── 반복 간 Jaccard 평균 {sum(pairs)/len(pairs):.3f} "
              f"(최소 {min(pairs):.2f} · 최대 {max(pairs):.2f})")
        print(f"  ── 등장 병원 총 {len(union)}곳 중 "
              f"과반 반복 등장 {len(stable)}곳 / 1회만 등장 {len(once)}곳")
        # How much of the stable set does a smaller number of repeats recover?
        for k in range(1, len(runs) + 1):
            recovered = set().union(*runs[:k]) & set(stable)
            pct = len(recovered) / len(stable) * 100 if stable else 0.0
            print(f"     {k}회 반복 시 안정 병원 회수율 {pct:>5.1f}%")

    out = HERE / "canonical_repeats_measurement.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nraw → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
