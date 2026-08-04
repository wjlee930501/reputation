#!/usr/bin/env python3
"""공유 카드(OG) 이미지의 원본 — 실제 리포트 지면을 익명 payload로 렌더한다.

## 왜 이 스크립트가 있는가

앞 버전의 OG 이미지는 AI로 생성한 마케팅 이미지였다. 노트북 화면의 "대시보드"는
라벨이 생성 낙서였고 차트는 아무것도 인코딩하지 않았다 — 존재하지 않는 제품의
스크린샷이었다. AE가 카톡으로 링크를 보낼 때 원장이 보는 첫 화면이 그것이었다.

대신 **실제로 발송되는 문서**를 쓴다. 여기서 렌더하는 것은 프로덕션과 같은
`backend/app/templates/lead_report.html`이고, 표·라벨·고지 문구·모델명이 전부 같다.
다른 것은 병원명(○○ 플레이스홀더)과 값뿐이다.

## 사용법

    APP_ENV=development ADMIN_SECRET_KEY=x \\
      backend/.venv/bin/python scripts/build_og_report_html.py

`/tmp/og-report.html`이 나온다. 이후 조판은 브라우저로 한다:

  1. 이 HTML을 폭 820px·DPR 2로 풀페이지 스크린샷 → 지면 이미지
  2. 1200×630 카드에 왼쪽 카피 + 오른쪽 지면(위에서 512px만, 아래는 흰색 페이드)으로 배치
  3. `site/public/landing/reputation-diagnosis-report-og.png`로 저장하고
     `site/app/layout.tsx`의 `OG_IMAGE`가 그 파일을 가리키는지 확인

**모델명이나 측정 규약이 바뀌면 다시 만든다.** 리포트가 `gpt-5.6-luna`를 쓰지 않게
되는 날, 공유 카드만 옛 모델명을 들고 있으면 그 자체가 이 서비스의 반증이 된다.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.lead_report import (  # noqa: E402
    LeadReportPayload,
    PlatformSegment,
    QueryDisclosure,
    render_lead_report_html,
)

KST = timezone(timedelta(hours=9))

# 고정 값이다 — 이미지가 재현 가능해야 하고, 매번 오늘 날짜로 바뀌면 diff가 무의미해진다.
GENERATED_AT = datetime(2026, 8, 4, 10, 30, tzinfo=KST)

# 병원명은 랜딩 전체와 같은 ○○ 플레이스홀더 규칙을 따른다.
PAYLOAD = LeadReportPayload(
    hospital_name="○○정형외과의원",
    region="서울 강남",
    generated_at=GENERATED_AT,
    repeat_count=3,
    system_prompt=(
        "당신은 환자에게 병원을 추천하는 도우미입니다. "
        "질문에 대해 실제로 존재하는 병원만 언급하고, 확실하지 않으면 추측하지 마세요."
    ),
    judge_model="gpt-5.6-luna",
    # 실패 1건을 일부러 남긴다. 실패를 감추면 "측정 안 됨"과 "언급 0"이 같은 숫자로
    # 보이는데, 그 둘을 나눠 적는 것이 이 리포트의 규율이다(F3-5).
    segments=(
        PlatformSegment(
            platform="chatgpt", vendor_label="OpenAI API", model="gpt-5.6-luna",
            planned=9, measured=9, mentioned=4, failed=0,
        ),
        PlatformSegment(
            platform="gemini", vendor_label="Google Gemini API", model="gemini-3.6-flash",
            planned=9, measured=8, mentioned=2, failed=1,
        ),
    ),
    queries=(
        QueryDisclosure(slot=1, kind="region_specialty",
                        text="강남 근처 정형외과 병원 추천해줘",
                        measured_at=GENERATED_AT - timedelta(minutes=12)),
        QueryDisclosure(slot=2, kind="region_symptom",
                        text="강남에서 어깨 통증 진료 받을 수 있는 병원 알려줘",
                        measured_at=GENERATED_AT - timedelta(minutes=11)),
        QueryDisclosure(slot=3, kind="region_treatment",
                        text="강남 근처 비수술 통증치료 병원 추천해줘",
                        measured_at=GENERATED_AT - timedelta(minutes=9)),
    ),
    notices=(
        "본 자료는 귀 병원 내부 참고용 진단 자료이며 광고물이 아닙니다.",
        "측정과 리포트 생성에 인공지능이 사용되었습니다.",
    ),
)


def main() -> None:
    out = Path("/tmp/og-report.html")
    out.write_text(render_lead_report_html(PAYLOAD), encoding="utf-8")
    print(f"wrote {out}")
    print(
        f"  측정 {PAYLOAD.total_measured}회 · 언급 {PAYLOAD.total_mentioned}회 "
        f"({' / '.join(s.label for s in PAYLOAD.segments)})"
    )


if __name__ == "__main__":
    main()
