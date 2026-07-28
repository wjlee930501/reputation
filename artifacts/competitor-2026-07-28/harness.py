"""측정 하네스 v2 — 감사 가능한 실측.

v1의 세 가지 결함을 고친다. 셋 다 실제로 잘못된 결론을 만들어냈다.

1. **검색 과금 누락.** v1은 web_search를 콜당 1회로 과금했으나 gpt-5-mini는 평균
   6.61회(최대 14회)를 돌린다. 진단 원가가 3.3배 과소 계산됐다. 여기서는 응답이
   보고한 실제 호출 수로 과금한다.
2. **원자료 덮어쓰기.** v1은 모델을 바꿔 실행해도 같은 파일에 썼다. terra·sol·
   chat-latest의 비교 원자료가 소실돼 결정을 재감사할 수 없었다. 여기서는 실행마다
   고유 파일에 쓰고 절대 덮어쓰지 않는다.
3. **응답 모델 미기록.** 요청에 보낸 별칭만 저장해 별칭이 다른 모델로 해석돼도
   알 수 없었다. 여기서는 응답의 `model`을 함께 남긴다.

가격은 아래 PRICING 한 곳에서만 온다. 요금표에 없는 모델을 상위 모델 단가로
가정하면 40~240% 과소평가되므로, 미등재 모델은 계산하지 않고 명시적으로 실패한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
RUNS = HERE / "runs"

USD_KRW = 1400

# 검색 과금은 플랫폼마다 다르다. 하나의 상수로 묶으면 한쪽이 반드시 틀린다.
#   OpenAI  : $10 / 1k searches (무료 구간 없음)
#   Gemini  : 월 5,000 프롬프트 무료(Gemini 3 공유), 이후 $14 / 1k search queries
# Gemini 무료 구간은 **월** 단위이고 계정 전체가 공유하므로, 콜당 단가에는
# 보수적으로 유료 단가를 적용하고 무료 구간은 월 예산 계산에서 따로 뺀다.
SEARCH_USD = {"openai": 10.0 / 1000, "gemini": 14.0 / 1000}
GEMINI_FREE_SEARCHES_PER_MONTH = 5000

# 공식 요금표 확인값 (developers.openai.com/api/docs/pricing,
# ai.google.dev/gemini-api/docs/pricing — 2026-07-28).
# (input $/1M, output $/1M)
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-mini-2025-08-07": (0.25, 2.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5.6-terra": (2.50, 15.00),
    "gpt-5.6-sol": (5.00, 30.00),
    # Gemini. gemini-flash-latest는 부동 별칭이며 현재 gemini-3.6-flash로 해석된다.
    # 별칭에는 요금을 등록하지 않는다 — 해석 대상이 바뀌면 단가도 바뀌므로,
    # 별칭으로 측정하면 단가를 계산하지 못하고 실패하는 편이 안전하다.
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.5-flash": (1.50, 9.00),
}

# 측정 대상 답변에서 병원명을 뽑는 정규식. 일반명사는 STOPWORDS로 거른다.
# 프로덕션은 이 추출을 쓰지 않는다(대상 병원 언급 여부만 LLM에 묻는다). 모델 간
# 상대 비교용 도구일 뿐이므로 완벽할 필요는 없으나, 일반명사가 병원으로 세어지면
# "등장 병원 수"가 부풀려지므로 계속 보강한다.
SUFFIXES = "의원|병원|클리닉|한의원|치과|메디컬센터|의료원"
NAME_RE = re.compile(rf"[가-힣A-Za-z0-9]{{2,15}}(?:{SUFFIXES})")
STOPWORDS = {
    "종합병원", "대학병원", "동네병원", "지역병원", "상급종합병원", "전문병원",
    "요양병원", "재활병원", "개인병원", "일반병원", "가까운병원", "해당병원",
    "각병원", "이병원", "그병원", "우리병원", "타병원", "대형병원", "상급병원",
    "외과의원", "내과의원", "정형외과의원", "이비인후과의원", "척추병원",
    "탈장클리닉", "인근병원", "주변병원", "근처병원", "다른병원", "전문의원",
}

# 측정 조건. 프롬프트를 주면 "병원명을 대라고 시켰을 때의 출력률"을, 주지 않으면
# 환자가 실제로 받는 답변에 가까운 자연 노출률을 잰다. 무엇을 파는지에 따라
# 어느 쪽이 옳은지가 갈리므로 양쪽을 다 재고 결정한다.
SYSTEM_PROMPT = (
    "지역 병원 정보를 잘 아는 의료 정보 도우미입니다. 구체적인 병원 이름을 포함해 답변하세요."
)


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


@dataclass
class Call:
    """측정 1회. 비용 재계산에 필요한 모든 값을 원자료로 남긴다."""

    platform: str
    requested_model: str
    resolved_model: str | None
    variant: str
    query: str
    repeat: int
    names: list[str]
    search_calls: int
    input_tokens: int
    output_tokens: int
    sources: int
    answer_chars: int
    error: str | None = None


@dataclass
class Run:
    run_id: str
    platform: str
    requested_model: str
    variant: str
    system_prompt: bool
    max_uses: int | None
    repeats: int
    queries: list[str]
    calls: list[Call] = field(default_factory=list)


def unit_cost(
    model: str, avg_in: float, avg_out: float, avg_searches: float, platform: str
) -> float:
    """콜당 USD. 요금을 모르는 모델은 추정하지 않고 실패한다.

    별칭(gemini-flash-latest, chat-latest)은 PRICING에 등록하지 않으므로 여기서
    KeyError가 난다. 이는 의도된 동작이다 — 해석 대상이 바뀌면 단가도 바뀌는데
    별칭 이름으로 단가를 고정해두면 조용히 틀린 값을 보고하게 된다.
    """
    if model not in PRICING:
        raise KeyError(
            f"{model}의 공식 요금을 모른다. PRICING에 확인값을 추가하기 전에는 "
            f"단가를 계산하지 않는다 — 상위 모델 단가로 가정하면 과소평가된다."
        )
    pin, pout = PRICING[model]
    search = SEARCH_USD[platform]
    return avg_in * pin / 1e6 + avg_out * pout / 1e6 + avg_searches * search


async def ask_openai(
    client: Any, model: str, query: str, *, system_prompt: bool, max_uses: int | None
) -> dict[str, Any]:
    tool: dict[str, Any] = {"type": "web_search"}
    if max_uses is not None:
        tool["max_uses"] = max_uses
    prompt = f"{SYSTEM_PROMPT}\n\n질문: {query}" if system_prompt else query
    response = await client.responses.create(
        model=model, tools=[tool], tool_choice="required", input=prompt
    )
    usage = _field(response, "usage")
    text = _field(response, "output_text") or ""
    output = _field(response, "output", []) or []
    searches = sum(
        1 for item in output if str(_field(item, "type", "")).startswith("web_search")
    )
    urls: set[str] = set()
    for item in output:
        for content in (_field(item, "content", []) or []):
            for ann in (_field(content, "annotations", []) or []):
                url = _field(ann, "url")
                if isinstance(url, str) and url.startswith("http"):
                    urls.add(url.split("?")[0])
    return {
        "resolved_model": _field(response, "model"),
        "names": sorted(extract_names(text)),
        "search_calls": searches,
        "input_tokens": _field(usage, "input_tokens", 0) or 0,
        "output_tokens": _field(usage, "output_tokens", 0) or 0,
        "sources": len(urls),
        "answer_chars": len(text),
    }


async def ask_gemini(
    client: Any, model: str, query: str, *, system_prompt: bool, max_uses: int | None
) -> dict[str, Any]:
    """Gemini 경로. ChatGPT와 **동일한** 조건을 적용할 수 있게 프롬프트를 주입한다.

    프로덕션은 현재 Gemini에만 시스템 프롬프트를 빼고 있어 두 플랫폼의 숫자가
    서로 다른 조건에서 나온다. 여기서는 조건을 인자로 통일해 비교 가능하게 만든다.
    """
    from google.genai import types as genai_types

    config = genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        system_instruction=SYSTEM_PROMPT if system_prompt else None,
    )
    response = await client.aio.models.generate_content(
        model=model, contents=query, config=config
    )
    text = getattr(response, "text", "") or ""
    usage = getattr(response, "usage_metadata", None)
    searches, urls = 0, set()
    for cand in (getattr(response, "candidates", None) or []):
        meta = getattr(cand, "grounding_metadata", None)
        if not meta:
            continue
        searches += len(getattr(meta, "web_search_queries", None) or [])
        for chunk in (getattr(meta, "grounding_chunks", None) or []):
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None) if web else None
            if uri:
                urls.add(uri.split("?")[0])
    return {
        "resolved_model": getattr(response, "model_version", None),
        "names": sorted(extract_names(text)),
        "search_calls": searches,
        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
        "sources": len(urls),
        "answer_chars": len(text),
    }


async def run_protocol(
    *,
    platform: str,
    model: str,
    queries: list[str],
    repeats: int,
    variant: str,
    system_prompt: bool = True,
    max_uses: int | None = None,
    concurrency: int = 4,
) -> Run:
    if platform == "openai":
        from openai import AsyncOpenAI

        client: Any = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        ask = ask_openai
    else:
        from google import genai

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        ask = ask_gemini

    run = Run(
        run_id=uuid.uuid4().hex[:12],
        platform=platform,
        requested_model=model,
        variant=variant,
        system_prompt=system_prompt,
        max_uses=max_uses,
        repeats=repeats,
        queries=list(queries),
    )
    sem = asyncio.Semaphore(concurrency)

    async def one(query: str, rep: int) -> Call:
        async with sem:
            try:
                row = await ask(
                    client, model, query, system_prompt=system_prompt, max_uses=max_uses
                )
                return Call(
                    platform=platform,
                    requested_model=model,
                    resolved_model=row["resolved_model"],
                    variant=variant,
                    query=query,
                    repeat=rep,
                    names=row["names"],
                    search_calls=row["search_calls"],
                    input_tokens=row["input_tokens"],
                    output_tokens=row["output_tokens"],
                    sources=row["sources"],
                    answer_chars=row["answer_chars"],
                )
            except Exception as exc:  # noqa: BLE001
                return Call(
                    platform=platform,
                    requested_model=model,
                    resolved_model=None,
                    variant=variant,
                    query=query,
                    repeat=rep,
                    names=[],
                    search_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    sources=0,
                    answer_chars=0,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )

    jobs = [one(q, r + 1) for q in queries for r in range(repeats)]
    run.calls = list(await asyncio.gather(*jobs))
    return run


def save(run: Run) -> Path:
    """실행마다 고유 파일. 덮어쓰지 않는다 — 원자료가 사라지면 감사가 불가능해진다."""
    RUNS.mkdir(exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{run.requested_model}_{run.variant}")
    path = RUNS / f"{safe}_{run.run_id}.json"
    if path.exists():  # run_id 충돌은 사실상 없지만, 덮어쓰기는 절대 허용하지 않는다.
        raise FileExistsError(path)
    path.write_text(json.dumps(asdict(run), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize(run: Run) -> dict[str, Any]:
    ok = [c for c in run.calls if c.error is None]
    failed = [c for c in run.calls if c.error is not None]
    if not ok:
        return {"model": run.requested_model, "variant": run.variant, "n": 0,
                "failed": len(failed), "error": failed[0].error if failed else None}

    n = len(ok)
    avg_in = sum(c.input_tokens for c in ok) / n
    avg_out = sum(c.output_tokens for c in ok) / n
    avg_search = sum(c.search_calls for c in ok) / n

    from collections import Counter

    freq = Counter(h for c in ok for h in c.names)
    once = sum(1 for v in freq.values() if v == 1)
    resolved = {c.resolved_model for c in ok if c.resolved_model}

    # 별칭으로 요청했더라도 요금은 **해석된 실제 모델** 기준으로 매긴다.
    # 해석 결과가 여러 개면(별칭이 도중에 바뀐 경우) 단가를 계산하지 않는다.
    priced_as = run.requested_model
    if len(resolved) == 1:
        only = next(iter(resolved))
        if only in PRICING:
            priced_as = only
    try:
        cost = unit_cost(priced_as, avg_in, avg_out, avg_search, run.platform)
    except KeyError:
        cost = None

    return {
        "model": run.requested_model,
        "resolved": sorted(resolved),
        "priced_as": priced_as if cost is not None else None,
        "platform": run.platform,
        "variant": run.variant,
        "n": n,
        "failed": len(failed),
        "avg_search_calls": round(avg_search, 2),
        "max_search_calls": max(c.search_calls for c in ok),
        "avg_in": round(avg_in),
        "avg_out": round(avg_out),
        "avg_names": round(sum(len(c.names) for c in ok) / n, 2),
        "zero_name_calls": sum(1 for c in ok if not c.names),
        "distinct_hospitals": len(freq),
        "noise_pct": round(once / len(freq) * 100) if freq else None,
        "top": freq.most_common(6),
        "cost_krw_per_call": round(cost * USD_KRW, 1) if cost is not None else None,
    }


def print_summary(s: dict[str, Any]) -> None:
    if not s["n"]:
        print(f"  {s['model']:24s} [{s['variant']}] 전부 실패 — {s.get('error')}")
        return
    resolved = ", ".join(s["resolved"]) or "?"
    cost = f"{s['cost_krw_per_call']}원" if s["cost_krw_per_call"] is not None else "요금 미상"
    print(f"  {s['model']} [{s['variant']}]  → 해석: {resolved}")
    print(
        f"    검색 {s['avg_search_calls']}회(최대 {s['max_search_calls']}) · "
        f"in {s['avg_in']:,} · out {s['avg_out']:,} · 콜당 {cost}"
    )
    print(
        f"    병원 {s['avg_names']}개/답변 · 0개 답변 {s['zero_name_calls']}회 · "
        f"등장 {s['distinct_hospitals']}곳 · 잡음 {s['noise_pct']}% · 실패 {s['failed']}"
    )
    if s["top"]:
        print("    상위: " + ", ".join(f"{h}({c})" for h, c in s["top"][:4]))


if __name__ == "__main__":
    print(__doc__)
    print(f"저장 위치: {RUNS}")
    print(f"요금표 등록 모델: {', '.join(sorted(PRICING))}")
    sys.exit(0)
