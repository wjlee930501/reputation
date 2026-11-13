#!/usr/bin/env python3
"""병원 사이트 레이아웃 A·B의 명조 제목용 글꼴(나눔명조 Bold 기반)을 빈도 기반 서브셋으로 쪼갠다.

Pretendard와 같은 이유·같은 구간 정의를 쓴다(scripts/build_font_subsets.py 상단 주석).
병원 콘텐츠는 동적이라 쓰는 글자만 담은 파일 하나로 자를 수 없고, 브라우저가 필요한
구간만 받게 한다. CSP가 `font-src 'self'`라 Google Fonts를 쓸 수 없어 직접 호스팅한다.

굵기는 Bold(700) 하나다 — 레이아웃 A·B는 명조를 제목에만 쓴다(본문은 Pretendard).
@font-face가 선언돼 있어도 명조를 쓰지 않는 페이지(레이아웃 C, 레이아웃을 고르지 않은
병원, 랜딩)는 파일을 받지 않는다.

## 사용법

    python3 scripts/build_serif_subsets.py

`site/public/fonts/clinic-serif/`에 woff2들을, `site/app/fonts/clinic-serif-subset.css`에
@font-face 규칙을 쓴다. 원본 `site/app/fonts/ClinicSerif-Bold.woff2`는 소스이고 서빙되지 않는다.

## 라이선스와 이름

나눔명조는 SIL Open Font License 1.1이고 "Nanum", "NanumMyeongjo" 등이 예약 글꼴 이름(RFN)이다.
서브셋은 OFL상 수정판이라 예약 이름을 쓸 수 없다. 그래서 소스는 원본 TTF(google/fonts
`ofl/nanummyeongjo/NanumMyeongjo-Bold.ttf`)의 name 테이블을 "Reputation Clinic Serif"로 바꿔
woff2로 저장한 것이고, CSS 이름도 같다. 라이선스 전문은 `site/public/fonts/clinic-serif/OFL.txt`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_font_subsets import load_ranges  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "site/app/fonts/ClinicSerif-Bold.woff2")
OUT_DIR = os.path.join(REPO, "site/public/fonts/clinic-serif")
CSS_OUT = os.path.join(REPO, "site/app/fonts/clinic-serif-subset.css")
FAMILY = "Reputation Clinic Serif"


def main() -> None:
    if not os.path.exists(SRC):
        sys.exit(f"원본 폰트가 없습니다: {SRC}")
    pyftsubset = shutil.which("pyftsubset") or os.environ.get("PYFTSUBSET")
    if not pyftsubset:
        sys.exit("pyftsubset이 없습니다. `pip install 'fonttools[woff]' brotli` 또는 PYFTSUBSET=경로")

    os.makedirs(OUT_DIR, exist_ok=True)
    faces = []
    total = 0
    for idx, unicodes in load_ranges():
        name = f"ClinicSerif-Bold.subset.{idx}.woff2"
        dst = os.path.join(OUT_DIR, name)
        subprocess.run(
            [
                pyftsubset, SRC,
                f"--unicodes={unicodes}",
                "--flavor=woff2",
                "--layout-features=*",
                "--no-hinting",
                "--desubroutinize",
                f"--output-file={dst}",
            ],
            check=True,
            capture_output=True,
        )
        # 이 구간의 글자가 폰트에 하나도 없으면 빈 파일이 나온다 — 선언하지 않는다.
        size = os.path.getsize(dst)
        total += size
        faces.append(
            "@font-face {\n"
            f"  font-family: '{FAMILY}';\n"
            "  font-style: normal;\n"
            "  font-weight: 700;\n"
            "  font-display: swap;\n"
            f"  src: url('/fonts/clinic-serif/{name}') format('woff2');\n"
            f"  unicode-range: {unicodes};\n"
            "}"
        )

    header = (
        "/* 생성물입니다 — 직접 고치지 마세요. `python3 scripts/build_serif_subsets.py`로 다시 만듭니다.\n"
        "\n"
        "   병원 사이트 레이아웃 A·B의 명조 제목. 구간 정의는 Pretendard 서브셋과 같다.\n"
        "\n"
        "   Derived from Nanum Myeongjo Bold (SIL Open Font License 1.1, Copyright (c) 2010 NHN\n"
        "   Corporation), renamed per the OFL Reserved Font Name clause — site/public/fonts/clinic-serif/OFL.txt */\n"
        "\n"
        ":root {\n"
        f"  --font-clinic-serif: '{FAMILY}';\n"
        "}\n"
    )
    open(CSS_OUT, "w", encoding="utf-8").write(header + "\n" + "\n\n".join(faces) + "\n")
    print(f"{len(faces)}개 서브셋 생성 · 합계 {total:,}바이트 (원본 {os.path.getsize(SRC):,})")


if __name__ == "__main__":
    main()
