"""Price a Claude column for the diagnosis report, measured rather than guessed.

The open question is whether adding Claude as a third platform is affordable.
Anthropic bills web search at $10/1k *searches* — and one API call can run
several searches — so a per-call estimate that assumes one search per call
understates the bill. This script reads usage.server_tool_use.web_search_requests
and charges the real count.

Two models, because they answer different questions:
  claude-sonnet-5  — what a Claude.ai user actually gets (the consumer default)
  claude-haiku-4-5 — the cheapest Claude that can run the same protocol

Same queries the gpt-5-mini protocol run used, so the numbers sit side by side.

    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_claude_cost.py
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

from measure_model_recall import extract_names  # noqa: E402
from query_mapper import build_queries  # noqa: E402

SYSTEM_PROMPT = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)
REPEATS = 2
REGION, KEYWORDS = "정자동", ["탈장", "치질 수술"]

# input $/1M, output $/1M — 공식 요금표 확인값 (2026-07-28).
# Sonnet 5는 2026-08-31까지 $2/$10 인트로 요금이나, 한 달 뒤 만료되므로
# 정가로 계획한다.
UNIT = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
# 4.6 미만 모델은 dynamic filtering(코드 실행 경유)을 지원하지 않는다.
SEARCH_TOOL = {
    "claude-sonnet-5": "web_search_20260209",
    "claude-haiku-4-5": "web_search_20250305",
}
SEARCH_UNIT = 10.00 / 1000  # $10 / 1k searches
USD_KRW = 1400


async def ask(client: Any, model: str, query: str) -> dict[str, Any]:
    response = await client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        tools=[{"type": SEARCH_TOOL[model], "name": "web_search"}],
        messages=[{"role": "user", "content": query}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    usage = response.usage
    server = getattr(usage, "server_tool_use", None)
    searches = getattr(server, "web_search_requests", 0) or 0
    return {
        "model": model,
        "query": query,
        "names": sorted(extract_names(text)),
        "searches": searches,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "stop_reason": response.stop_reason,
        "answer_chars": len(text),
    }


async def run_model(client: Any, model: str, plan: list[dict]) -> list[dict]:
    print(f"\n── {model}", flush=True)
    sem = asyncio.Semaphore(3)

    async def one(query: str, rep: int) -> dict | None:
        async with sem:
            try:
                row = await ask(client, model, query)
                row["repeat"] = rep
                return row
            except Exception as exc:  # noqa: BLE001
                print(f"   FAIL {type(exc).__name__}: {str(exc)[:110]}", flush=True)
                return None

    jobs = [one(item["query"], r + 1) for item in plan for r in range(REPEATS)]
    rows = [r for r in await asyncio.gather(*jobs) if r]
    if not rows:
        print("   전부 실패 — 이 모델은 이 경로를 지원하지 않음")
        return []

    n = len(rows)
    avg_in = sum(r["input_tokens"] for r in rows) / n
    avg_out = sum(r["output_tokens"] for r in rows) / n
    avg_search = sum(r["searches"] for r in rows) / n
    avg_names = sum(len(r["names"]) for r in rows) / n

    pin, pout = UNIT[model]
    cost = avg_in * pin / 1e6 + avg_out * pout / 1e6 + avg_search * SEARCH_UNIT
    print(
        f"   병원 {avg_names:.1f}개 · 검색 {avg_search:.1f}회 · "
        f"in {avg_in:,.0f} tok · out {avg_out:,.0f} tok"
    )
    print(
        f"   콜당 ${cost:.5f} ({cost*USD_KRW:,.0f}원) · 12콜 진단 1건 "
        f"${cost*12:.3f} ({cost*12*USD_KRW:,.0f}원) · 월 300건 {cost*12*300*USD_KRW:,.0f}원"
    )

    freq = Counter(h for r in rows for h in r["names"])
    top = ", ".join(f"{h}({c}/{n})" for h, c in freq.most_common(4))
    print(f"   상위 등장: {top}")
    return rows


async def main() -> int:
    from anthropic import AsyncAnthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    plan = build_queries(REGION, KEYWORDS)
    print(f"질의 {len(plan)}개 × {REPEATS}회 = 모델당 {len(plan)*REPEATS}콜")
    for i, item in enumerate(plan, 1):
        print(f"  {i}. [{item['kind']}] {item['query']}")

    rows: list[dict] = []
    for model in ("claude-haiku-4-5", "claude-sonnet-5"):
        rows.extend(await run_model(client, model, plan))

    (HERE / "claude_cost_measurement.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
