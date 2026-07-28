"""Measure real per-call token usage for the SoV query path.

cost_guard counts queries, not tokens, so unit cost cannot be derived from
existing logs. This calls the exact production shapes used by sov_engine
(_query_chatgpt_with_search_result / _query_gemini_result) and reports the
token counts the APIs return, so the cost model rests on measured numbers
rather than assumed ones.

Run from the repo root with the backend venv:
    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_sov_cost.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Load .env without pulling in app.core.config (which validates far more than
# this script needs and refuses some local-only combinations).
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key.strip(), value.strip())

OPENAI_MODEL = os.environ.get("OPENAI_MODEL_QUERY", "gpt-4o")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Same system prompt the engine uses.
SYSTEM_PROMPT = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)

# Drawn from QUERY_TEMPLATES so the shape matches what a real diagnosis sends.
QUERIES = [
    "분당 탈장 잘 보는 병원 추천해줘",
    "분당 외과 어디가 좋아",
    "탈장 있는데 분당 어느 병원 가야 해?",
    "분당 외과 병원 어디가 좋은지 비교해줘",
]


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


async def measure_openai(query: str) -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = await client.responses.create(
        model=OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        tool_choice="required",
        input=f"{SYSTEM_PROMPT}\n\n질문: {query}",
    )
    usage = _field(response, "usage")
    # Count the billable web_search invocations the model actually made.
    search_calls = sum(
        1
        for item in (_field(response, "output", []) or [])
        if str(_field(item, "type", "")).startswith("web_search")
    )
    text = _field(response, "output_text") or ""
    return {
        "platform": "openai",
        "input_tokens": _field(usage, "input_tokens", 0),
        "output_tokens": _field(usage, "output_tokens", 0),
        "total_tokens": _field(usage, "total_tokens", 0),
        "web_search_calls": search_calls,
        "answer_chars": len(text),
    }


async def measure_gemini(query: str) -> dict[str, Any]:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=query,
        config=genai_types.GenerateContentConfig(
            temperature=1.0,
            max_output_tokens=800,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        ),
    )
    meta = _field(response, "usage_metadata")
    return {
        "platform": "gemini",
        "input_tokens": _field(meta, "prompt_token_count", 0) or 0,
        "output_tokens": _field(meta, "candidates_token_count", 0) or 0,
        "total_tokens": _field(meta, "total_token_count", 0) or 0,
        "web_search_calls": 1,  # grounding is one request-level search
        "answer_chars": len(response.text or ""),
    }


async def main() -> int:
    rows: list[dict[str, Any]] = []
    for query in QUERIES:
        for label, fn in (("openai", measure_openai), ("gemini", measure_gemini)):
            try:
                row = await fn(query)
                row["query"] = query
                rows.append(row)
                print(
                    f"OK   {label:7s} in={row['input_tokens']:>6} out={row['output_tokens']:>5} "
                    f"search={row['web_search_calls']} chars={row['answer_chars']:>5}  {query[:28]}"
                )
            except Exception as exc:  # noqa: BLE001 — surface the failure, keep measuring
                print(f"FAIL {label:7s} {type(exc).__name__}: {str(exc)[:120]}")

    if not rows:
        print("\n측정된 호출이 없습니다.")
        return 1

    print("\n" + "=" * 68)
    for platform in ("openai", "gemini"):
        subset = [r for r in rows if r["platform"] == platform]
        if not subset:
            continue
        n = len(subset)
        avg_in = sum(r["input_tokens"] for r in subset) / n
        avg_out = sum(r["output_tokens"] for r in subset) / n
        avg_search = sum(r["web_search_calls"] for r in subset) / n
        print(
            f"{platform:7s} n={n}  평균 input={avg_in:,.0f} tok  output={avg_out:,.0f} tok  "
            f"web_search={avg_search:.1f}회/콜"
        )

    out = Path(__file__).with_name("sov_cost_measurement.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nraw → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
