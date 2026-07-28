"""Does a 3-repeat, 4-query design reproduce what 5 repeats told us?

Part 1 costs nothing: the existing 5-repeat runs are subsampled to every
C(5,3)=10 combination of three, so we can see how far a 3-repeat mention rate
drifts from the 5-repeat one — and, more importantly, whether it still finds the
hospitals that 5 repeats called stable.

Part 2 spends 12 calls on the actual proposed set: one specialty query plus
three symptom queries, in the two shapes patients really use, with no
superlative modifiers and a region slot in every one.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_three_repeats.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from measure_model_recall import ask  # noqa: E402

REPEATS = 3
QUERIES = [
    ("진료과형", "분당 근처 외과 병원 추천해줘"),
    ("증상형", "탈장이 있는데, 분당 근처 병원 어디로 가야해?"),
    ("증상형", "담낭염이 있는데, 분당 근처 병원 어디로 가야해?"),
    ("증상형", "치질이 있는데, 분당 근처 병원 어디로 가야해?"),
]
STABLE_CUT = 0.6  # 5회 중 3회 이상 = 안정 등장으로 간주


def part1_subsample() -> None:
    path = HERE / "canonical_repeats_measurement.json"
    if not path.exists():
        print("canonical_repeats_measurement.json 없음 — Part 1 생략\n")
        return
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_pattern: dict[str, list[set[str]]] = defaultdict(list)
    for r in rows:
        by_pattern[r["pattern"]].append(set(r["names"]))

    print("① 기존 5회 데이터를 3회로 재표본 (새 호출 없음)")
    for pattern, runs in by_pattern.items():
        if len(runs) < 5:
            continue
        n5 = len(runs)
        rate5 = {h: sum(h in r for r in runs) / n5 for h in set().union(*runs)}
        stable = {h for h, v in rate5.items() if v >= STABLE_CUT}

        devs: list[float] = []
        found_rates: list[float] = []
        for combo in combinations(range(n5), 3):
            sub = [runs[i] for i in combo]
            rate3 = {h: sum(h in r for r in sub) / 3 for h in set().union(*sub)}
            devs.extend(abs(rate3.get(h, 0.0) - rate5[h]) for h in rate5)
            if stable:
                found_rates.append(len(stable & set().union(*sub)) / len(stable))

        print(f"  {pattern}")
        print(f"    5회 기준 안정 병원 {len(stable)}곳 — {', '.join(sorted(stable)) or '없음'}")
        print(f"    3회 표본이 안정 병원을 찾아내는 비율: 평균 "
              f"{statistics.mean(found_rates)*100:.1f}% "
              f"(최악 {min(found_rates)*100:.0f}%)" if found_rates else "")
        print(f"    병원별 등장률 절대오차: 평균 {statistics.mean(devs):.3f} "
              f"(즉 3회 측정이 5회 대비 평균 {statistics.mean(devs)*100:.1f}%p 어긋남)")
    print()


async def part2_measure() -> list[dict[str, Any]]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    rows: list[dict[str, Any]] = []
    print(f"② 제안 세트 {len(QUERIES)}질의 × {REPEATS}회 = {len(QUERIES)*REPEATS}콜 (gpt-4o)")
    for kind, query in QUERIES:
        runs: list[set[str]] = []
        for i in range(REPEATS):
            try:
                row = await ask(client, "gpt-4o", query)
                row.update(kind=kind, repeat=i + 1)
                rows.append(row)
                runs.append(set(row["names"]))
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {query[:24]} {i+1}회 {type(exc).__name__}")
        if runs:
            union = set().union(*runs)
            freq = Counter(h for r in runs for h in r)
            allhit = [h for h, c in freq.items() if c == len(runs)]
            print(f"  {kind:6s} {query[:34]:36s} 등장 {len(union):>2}곳 · "
                  f"{len(runs)}회 전부 등장 {len(allhit)}곳"
                  + (f" ({', '.join(sorted(allhit)[:2])})" if allhit else ""))
    return rows


def part3_report(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    by_q: dict[str, list[set[str]]] = defaultdict(list)
    for r in rows:
        by_q[r["query"]].append(set(r["names"]))

    # 병원별 전체 등장률 = (등장한 실행 수) / (총 실행 수) — 이게 리포트에 실릴 분수다.
    total_runs = sum(len(v) for v in by_q.values())
    freq = Counter(h for runs in by_q.values() for r in runs for h in r)
    print(f"\n③ 세트 전체 집계 — 총 {total_runs}회 실행")
    print(f"   {'병원':28s} {'등장':>6s}  등장률")
    for hospital, count in freq.most_common(12):
        bar = "█" * round(count / total_runs * 20)
        print(f"   {hospital:28s} {count:>2}/{total_runs:<3d}  {count/total_runs*100:>5.1f}% {bar}")

    once = sum(1 for c in freq.values() if c == 1)
    print(f"\n   1회만 등장한 병원 {once}/{len(freq)}곳 "
          f"({once/len(freq)*100:.0f}%) — 낮을수록 세트가 안정적")

    out = HERE / "three_repeats_measurement.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nraw → {out}")


async def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    part1_subsample()
    rows = await part2_measure()
    part3_report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
