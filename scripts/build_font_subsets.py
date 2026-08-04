#!/usr/bin/env python3
"""Pretendard 가변폰트를 빈도 기반 동적 서브셋으로 쪼갠다.

## 왜 필요한가

`site/app/fonts/PretendardVariable.woff2`는 2,057,688바이트이고, 랜딩 1회 로드
전송량 2,639,052바이트의 **78%**를 혼자 차지했다. 병원 원장이 진료 사이에 LTE로 여는
페이지에서 이 한 파일이 가장 큰 비용이었다.

## 왜 연속 구간으로 자르면 안 되는가 (측정함)

한글 음절(U+AC00–D7A3)은 초성→중성→종성 순으로 배열돼 있어서, 실제 한국어 문장은
블록 **전체**에 흩어진다. 코드포인트를 연속 구간으로 N등분해 측정한 결과:

    16개로 분할  → 16/16 구간 사용, 2,064,752바이트
    48개로 분할  → 47/48 구간 사용, 2,227,852바이트
    120개로 분할 → 105/119 구간 사용, 2,258,648바이트

전부 원본(2,057,688)보다 **커진다** — 파일당 헤더 중복 때문이다. 연속 분할은 답이 아니다.

## 그래서 빈도 기반 그룹을 쓴다

Pretendard가 배포하는 dynamic-subset의 unicode-range 92개는 함께 쓰이는 글자끼리
묶여 있다. 그 구간 정의만 가져와 **우리 폰트 파일**에 적용한다(자체호스팅 유지, CDN 의존 없음).

측정 결과:

    랜딩 텍스트(609자)     → 22/92 서브셋,  538,464바이트 (원본의 26.2%)
    사이트 전체 텍스트(830자) → 29/92 서브셋,  714,596바이트 (원본의 34.7%)

병원 콘텐츠는 동적이라 어떤 글자가 올지 미리 알 수 없다. 그래서 "쓰는 글자만 담은 파일
하나"로 자를 수 없고(빠뜨리면 글자가 깨진다), 브라우저가 필요한 구간만 받아가는
이 방식이 맞다.

## 사용법

    python3 scripts/build_font_subsets.py

`site/public/fonts/pretendard/`에 woff2들을, `site/app/fonts/pretendard-subset.css`에
@font-face 규칙을 쓴다. 원본 woff2는 서브셋의 소스이므로 지우지 않는다(서빙되지 않는다).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "site/app/fonts/PretendardVariable.woff2")
OUT_DIR = os.path.join(REPO, "site/public/fonts/pretendard")
CSS_OUT = os.path.join(REPO, "site/app/fonts/pretendard-subset.css")

# 구간 정의의 출처. 폰트 바이너리는 받지 않는다 — 우리 파일을 자른다.
RANGES_CSS_URL = (
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9"
    "/dist/web/variable/pretendardvariable-dynamic-subset.css"
)
RANGES_CACHE = os.path.join(REPO, "scripts/.pretendard-ranges.css")


def load_ranges() -> list[tuple[int, str]]:
    if os.path.exists(RANGES_CACHE):
        css = open(RANGES_CACHE, encoding="utf-8").read()
    else:
        with urllib.request.urlopen(RANGES_CSS_URL) as r:
            css = r.read().decode("utf-8")
        open(RANGES_CACHE, "w", encoding="utf-8").write(css)

    blocks = re.findall(r"/\* \[(\d+)\] \*/\s*@font-face\s*\{(.*?)\}", css, re.S)
    if not blocks:
        sys.exit("Pretendard unicode-range 정의를 파싱하지 못했습니다.")
    out = []
    for idx, body in blocks:
        raw = re.search(r"unicode-range:\s*([^;]+);", body).group(1).strip()
        out.append((int(idx), re.sub(r"\s+", "", raw)))
    return sorted(out)


def main() -> None:
    if not os.path.exists(SRC):
        sys.exit(f"원본 폰트가 없습니다: {SRC}")
    if shutil.which("pyftsubset") is None:
        sys.exit("pyftsubset이 없습니다. `pip install 'fonttools[woff]' brotli`")

    ranges = load_ranges()
    os.makedirs(OUT_DIR, exist_ok=True)

    faces = []
    total = 0
    for idx, unicodes in ranges:
        name = f"PretendardVariable.subset.{idx}.woff2"
        dst = os.path.join(OUT_DIR, name)
        subprocess.run(
            [
                "pyftsubset", SRC,
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
        total += os.path.getsize(dst)
        faces.append(
            "@font-face {\n"
            "  font-family: 'Pretendard Variable';\n"
            "  font-style: normal;\n"
            "  font-weight: 45 920;\n"
            "  font-display: swap;\n"
            f"  src: url('/fonts/pretendard/{name}') format('woff2-variations');\n"
            f"  unicode-range: {unicodes};\n"
            "}"
        )

    header = (
        "/* 생성물입니다 — 직접 고치지 마세요. `python3 scripts/build_font_subsets.py`로 다시 만듭니다.\n"
        "\n"
        "   Pretendard 원본 한 덩어리(2,057,688바이트)는 랜딩 전송량의 78%였다. 빈도 기반\n"
        "   서브셋으로 쪼개면 브라우저가 실제로 쓰는 구간만 받는다 — 랜딩 기준 538,464바이트(26.2%).\n"
        "\n"
        "   연속 코드포인트로 자르면 오히려 커진다(한글 음절이 초성 순 배열이라 문장이 블록 전체에\n"
        "   흩어진다). 근거와 측정값은 scripts/build_font_subsets.py 상단 주석에 있다.\n"
        "\n"
        "   Pretendard is licensed under SIL Open Font License 1.1.\n"
        "   Copyright (c) 2021 Kil Hyung-jin — https://github.com/orioncactus/pretendard */\n"
        "\n"
        ":root {\n"
        "  --font-pretendard: 'Pretendard Variable';\n"
        "}\n"
    )
    open(CSS_OUT, "w", encoding="utf-8").write(header + "\n" + "\n\n".join(faces) + "\n")

    print(f"{len(faces)}개 서브셋 생성 · 합계 {total:,}바이트 (원본 {os.path.getsize(SRC):,})")
    print(f"  woff2 → {OUT_DIR}")
    print(f"  css   → {CSS_OUT}")


if __name__ == "__main__":
    main()
