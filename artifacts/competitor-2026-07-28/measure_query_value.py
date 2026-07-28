"""Which queries actually carry SoV signal, and how many do we really need?

Two questions, answered with data rather than intuition:

  1. Do the untested templates produce hospital names at all? Four of the 18
     templates have no {region} slot, so they may never name a local clinic —
     if so they cost money and return no signal.
  2. How redundant is the set? Near-identical templates ("잘 보는 병원 추천해줘"
     vs "치료 잘하는 병원") may surface the same clinics, in which case running
     both buys nothing. Greedy set cover tells us how few queries reach the same
     coverage the full set does.

Repeats (measuring the same query n times) are statistical rigour and stay.
Redundancy (measuring near-identical queries once each) is waste and can go.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_query_value.py
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

from measure_model_recall import ask, extract_names  # noqa: E402  — reuse identical extraction

# The six templates the recall run never exercised. The first four have no
# {region} slot at all — those are the prime suspects.
UNTESTED = [
    ("정보형·지역없음", "탈장 초기 증상이 뭔지 알려줘"),
    ("정보형·지역없음", "탈장 치료하려면 어떤 전문의한테 가야 해?"),
    ("비용형·지역없음", "탈장 치료 비용이 얼마나 드는지 알려줘"),
    ("비용형·지역없음", "탈장 수술 후 회복 기간 얼마나 돼?"),
    ("비용형·지역있음", "탈장 비수술 치료 가능한 병원 분당"),
    ("비용형·지역있음", "분당 외과 비용 어느 정도야?"),
]


def greedy_cover(sets: dict[str, set[str]]) -> list[tuple[str, int, float]]:
    """Order queries by how much *new* coverage each one adds."""
    universe: set[str] = set().union(*sets.values()) if sets else set()
    remaining = dict(sets)
    covered: set[str] = set()
    order: list[tuple[str, int, float]] = []
    while remaining and len(covered) < len(universe):
        best = max(remaining, key=lambda q: len(remaining[q] - covered))
        gain = len(remaining[best] - covered)
        if gain == 0:
            break
        covered |= remaining.pop(best)
        order.append((best, gain, len(covered) / len(universe) if universe else 0.0))
    return order


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print("① 미측정 템플릿 6종 — SoV 신호가 나오는가 (gpt-4o)")
    new_rows: list[dict[str, Any]] = []
    for kind, query in UNTESTED:
        try:
            row = await ask(client, "gpt-4o", query)
            row["kind"] = kind
            new_rows.append(row)
            flag = "신호없음 ✗" if not row["names"] else f"병원 {len(row['names'])}개"
            print(f"  {kind:14s} {flag:12s} 출처 {row['sources']}개 "
                  f"in={row['input_tokens']:>6}  {query[:30]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {kind}: {type(exc).__name__}: {str(exc)[:90]}")

    out = HERE / "query_value_measurement.json"
    out.write_text(json.dumps(new_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── ② redundancy across the 12 already-measured queries ──
    prior_path = HERE / "model_recall_measurement.json"
    if not prior_path.exists():
        print("\nmodel_recall_measurement.json 없음 — 중복도 분석 생략")
        return 0
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    sets = {
        r["query"]: set(r["names"])
        for r in prior
        if r["model"] == "gpt-4o" and r["names"]
    }

    universe = set().union(*sets.values()) if sets else set()
    print(f"\n② 기존 12질의 중복도 — 전체에서 발견된 병원 {len(universe)}곳")
    order = greedy_cover(sets)
    for i, (query, gain, cum) in enumerate(order, 1):
        print(f"  {i:>2}. +{gain:>2}곳  누적 {cum*100:>5.1f}%  {query[:34]}")
    if order:
        for target in (0.8, 0.9, 1.0):
            need = next((i for i, (_, _, c) in enumerate(order, 1) if c >= target), None)
            if need:
                print(f"  → 발견 병원의 {target*100:.0f}% 도달에 필요한 질의 수: {need}개")

    only_once = [n for n in universe if sum(n in s for s in sets.values()) == 1]
    print(f"  → 단 한 질의에서만 등장한 병원: {len(only_once)}/{len(universe)}곳 "
          f"({len(only_once)/len(universe)*100:.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
