"""모델 재선정 캠페인 — 올바른 검색 과금 + 측정 조건 교차.

두 가지를 한 번에 답한다.

1. **원가.** v1은 웹검색을 콜당 1회로 과금해 gpt-5-mini 원가를 3.3배 낮게 봤다.
   실제 검색 횟수로 다시 재고, 검색 팬아웃이 모델 계열의 속성인지 확인한다
   (OpenAI Responses API는 max_uses를 지원하지 않아 튜닝 불가).

2. **측정 조건.** 프로덕션은 ChatGPT에만 "구체적인 병원 이름을 포함해 답변하세요"를
   주고 Gemini에는 주지 않는다. 두 플랫폼 숫자가 다른 조건에서 나오고 있고,
   프롬프트가 있는 쪽은 자연 노출률이 아니라 지시된 출력률을 잰다.
   prompt_on / prompt_off를 양쪽에 교차해 차이를 수치로 만든다.

    ../../backend/.venv/bin/python campaign.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from harness import USD_KRW, print_summary, run_protocol, save, summarize  # noqa: E402

# 두 지역을 쓰면 지역 효과와 모델 효과가 섞인다(Codex #9). 모델 비교가 목적이므로
# 지역·진료과·키워드를 하나로 고정하고 모델만 바꾼다.
QUERIES = [
    "수서역 근처 내과 병원 추천해줘",
    "대장내시경 받으려는데 수서역 근처 병원 어디가 좋아?",
    "허리디스크가 있는데 수서역 근처 병원 어디로 가야해?",
]
REPEATS = 3

OPENAI_MODELS = ["gpt-5-mini", "gpt-5.6-luna", "gpt-5.6-terra", "chat-latest", "gpt-4o"]
GEMINI_MODELS = ["gemini-flash-latest"]


async def main() -> int:
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(var):
            print(f"{var} 없음", file=sys.stderr)
            return 1

    summaries = []
    for system_prompt in (True, False):
        variant = "prompt_on" if system_prompt else "prompt_off"
        print(f"\n{'=' * 78}\n측정 조건: {variant}\n{'=' * 78}")

        for platform, models in (("openai", OPENAI_MODELS), ("gemini", GEMINI_MODELS)):
            for model in models:
                run = await run_protocol(
                    platform=platform,
                    model=model,
                    queries=QUERIES,
                    repeats=REPEATS,
                    variant=variant,
                    system_prompt=system_prompt,
                )
                path = save(run)
                s = summarize(run)
                s["platform"] = platform
                summaries.append(s)
                print_summary(s)
                print(f"    raw → {path.name}", flush=True)

    (HERE / "campaign_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'=' * 78}\n요약 — 진단 1건(12콜) 기준\n{'=' * 78}")
    print(f"{'모델':22s} {'조건':10s} {'검색':>6s} {'병원':>5s} {'잡음':>5s} {'콜당':>9s} {'진단':>10s}")
    for s in summaries:
        if not s["n"]:
            continue
        cost = s["cost_krw_per_call"]
        cost_s = f"{cost:,.0f}원" if cost is not None else "미상"
        diag_s = f"{cost * 12:,.0f}원" if cost is not None else "미상"
        print(
            f"{s['model']:22s} {s['variant']:10s} {s['avg_search_calls']:>6.1f} "
            f"{s['avg_names']:>5.1f} {str(s['noise_pct']) + '%':>5s} {cost_s:>9s} {diag_s:>10s}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
