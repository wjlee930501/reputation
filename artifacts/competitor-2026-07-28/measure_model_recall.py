"""Validate scenario D: can gpt-4o-mini replace gpt-4o as the SoV answer model?

Mention judging already runs on gpt-4o-mini (OPENAI_MODEL_PARSE), so the only
thing the swap changes is the *answer*. What matters is therefore whether mini
surfaces a comparable set of hospital names, grounded on a comparable number of
sources. If mini names fewer hospitals, every hospital's measured mention rate
drifts low and the metric stops being comparable across time.

Extraction is a deterministic regex applied identically to both models — using an
LLM to judge would let the judge's bias contaminate the comparison.

Run from the repo root with OPENAI_API_KEY exported:
    backend/.venv/bin/python artifacts/competitor-2026-07-28/measure_model_recall.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SYSTEM_PROMPT = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)

QUERIES = [
    "분당 탈장 잘 보는 병원 추천해줘",
    "분당 외과 어디가 좋아",
    "정자동 탈장 잘하는 곳",
    "분당 외과 전문의 추천",
    "탈장 수술 분당 어느 병원이 좋아?",
    "분당 탈장 치료 잘하는 병원",
    "탈장 증상 분당에서 치료 잘하는 곳",
    "탈장 있는데 분당 어느 병원 가야 해?",
    "분당 외과 병원 어디가 좋은지 비교해줘",
    "분당 탈장 병원 후기 좋은 곳",
    "정자동 외과 잘한다고 소문난 병원",
    "분당 탈장 빨리 낫는 병원",
]

SUFFIXES = (
    "의원|병원|클리닉|한의원|치과|메디컬센터|의료원"
)
NAME_RE = re.compile(rf"[가-힣A-Za-z0-9]{{2,15}}(?:{SUFFIXES})")

# Generic words that match the pattern but name no specific clinic.
STOPWORDS = {
    "종합병원", "대학병원", "동네병원", "지역병원", "상급종합병원", "전문병원",
    "요양병원", "재활병원", "개인병원", "일반병원", "이비인후과의원", "가까운병원",
    "해당병원", "각병원", "이병원", "그병원", "우리병원", "타병원", "정형외과의원",
}


def extract_names(text: str) -> set[str]:
    found = set()
    for m in NAME_RE.finditer(text or ""):
        name = m.group(0).strip()
        if name in STOPWORDS or len(name) < 4:
            continue
        found.add(name)
    return found


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def source_urls(response: Any) -> set[str]:
    urls: set[str] = set()
    for item in (_field(response, "output", []) or []):
        for content in (_field(item, "content", []) or []):
            for ann in (_field(content, "annotations", []) or []):
                url = _field(ann, "url")
                if isinstance(url, str) and url.startswith("http"):
                    urls.add(url.split("?")[0])
    return urls


async def ask(client: Any, model: str, query: str) -> dict[str, Any]:
    response = await client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        tool_choice="required",
        input=f"{SYSTEM_PROMPT}\n\n질문: {query}",
    )
    usage = _field(response, "usage")
    text = _field(response, "output_text") or ""
    searches = sum(
        1
        for item in (_field(response, "output", []) or [])
        if str(_field(item, "type", "")).startswith("web_search")
    )
    return {
        "model": model,
        "query": query,
        "names": sorted(extract_names(text)),
        "sources": len(source_urls(response)),
        "web_search_calls": searches,
        "input_tokens": _field(usage, "input_tokens", 0),
        "output_tokens": _field(usage, "output_tokens", 0),
        "answer_chars": len(text),
    }


async def main() -> int:
    from openai import AsyncOpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY 없음", file=sys.stderr)
        return 1
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    rows: list[dict[str, Any]] = []
    for query in QUERIES:
        for model in ("gpt-4o", "gpt-4o-mini"):
            try:
                row = await ask(client, model, query)
                rows.append(row)
                print(
                    f"OK  {model:12s} 병원 {len(row['names']):>2}개 출처 {row['sources']:>2}개 "
                    f"search={row['web_search_calls']} in={row['input_tokens']:>6} "
                    f"out={row['output_tokens']:>4}  {query[:24]}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {model:12s} {type(exc).__name__}: {str(exc)[:110]}")

    if not rows:
        return 1

    out = Path(__file__).with_name("model_recall_measurement.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    by_model = {m: [r for r in rows if r["model"] == m] for m in ("gpt-4o", "gpt-4o-mini")}
    for model, subset in by_model.items():
        if not subset:
            continue
        n = len(subset)
        print(
            f"{model:12s} n={n}  병원명 평균 {sum(len(r['names']) for r in subset)/n:.1f}개  "
            f"출처 평균 {sum(r['sources'] for r in subset)/n:.1f}개  "
            f"input 평균 {sum(r['input_tokens'] for r in subset)/n:,.0f} tok  "
            f"output 평균 {sum(r['output_tokens'] for r in subset)/n:,.0f} tok"
        )

    print("\n질의별 병원명 집합 일치도")
    overlaps, recalls = [], []
    for query in QUERIES:
        a = next((set(r["names"]) for r in by_model["gpt-4o"] if r["query"] == query), set())
        b = next((set(r["names"]) for r in by_model["gpt-4o-mini"] if r["query"] == query), set())
        if not a and not b:
            continue
        inter = a & b
        jac = len(inter) / len(a | b) if (a | b) else 0.0
        # mini가 4o가 찾은 병원을 얼마나 회수하는가 — 언급률 편향의 직접 지표.
        rec = len(inter) / len(a) if a else None
        overlaps.append(jac)
        if rec is not None:
            recalls.append(rec)
        print(
            f"  4o={len(a):>2} mini={len(b):>2} 공통={len(inter):>2} "
            f"Jaccard={jac:.2f} " + (f"mini회수율={rec:.2f}" if rec is not None else "") +
            f"  {query[:26]}"
        )

    if overlaps:
        print(f"\n평균 Jaccard {sum(overlaps)/len(overlaps):.3f}")
    if recalls:
        print(f"평균 mini 회수율 {sum(recalls)/len(recalls):.3f}  "
              f"(4o가 언급한 병원 중 mini도 언급한 비율)")
    print(f"\nraw → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
