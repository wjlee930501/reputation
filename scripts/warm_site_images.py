#!/usr/bin/env python3
"""Site 배포 직후 공개 페이지 사진의 최적화 캐시를 미리 채운다.

왜: Site는 인스턴스 1대·CPU 1개다(min=max=1). `/_next/image`는 사진·크기·형식마다 첫
요청 때 원본을 AVIF로 변환하는데 한 장에 1~2초가 걸리고(원본 크기와 거의 무관,
2026-11-14 측정), CPU 하나에서 차례로 처리된다. 캐시는 인스턴스 디스크라 배포·재시작 때
비워지므로, 배포 뒤 첫 방문자들이 사진이 차례로 늦게 뜨는 화면을 본다. 배포 직후 이
스크립트가 방문자 대신 그 첫 요청을 한다.

무엇을: 공개 병원마다 사진이 나오는 페이지(홈·진료 안내·의료진·건강 정보 목록)의 HTML에서
`<img srcset sizes>`를 읽고, 대표 화면(데스크톱 1440px·2배, 모바일 390px·3배)에서 브라우저가
고를 후보를 그대로 요청한다. 한 번에 한 장씩 보내 실제 방문 요청이 CPU를 기다리지 않게 한다.

실패는 배포를 막지 않는다 — 캐시를 채우지 못하면 종전처럼 첫 방문 때 변환될 뿐이다.

    python3 scripts/warm_site_images.py --public-domain reputation.motionlabs.kr [--budget-seconds 2400] [--dry-run]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

if __package__ in (None, ""):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.deploy_smoke import _json, public_hosts  # noqa: E402

PAGES = ("", "/visit", "/doctor", "/contents")
# (뷰포트 CSS 폭, 기기 배율). 방문 기기 대부분이 이 둘 중 하나와 같은 후보를 고른다.
PROFILES: tuple[tuple[int, float], ...] = ((1440, 2.0), (390, 3.0))
ACCEPT = "image/avif,image/webp,*/*"
USER_AGENT = "reputation-image-warmup"

_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_SIZE_ENTRY = re.compile(r"^\s*(?:\((max|min)-width:\s*(\d+(?:\.\d+)?)px\)\s*)?(.+?)\s*$")
_CALC = re.compile(r"^calc\(\s*100vw\s*-\s*(\d+(?:\.\d+)?)px\s*\)$")


def slot_width(sizes: str, viewport: int) -> float:
    """`sizes` 속성에서 이 뷰포트의 표시 폭(CSS px). 모르는 꼴은 뷰포트 폭으로 본다."""
    for raw in (sizes or "100vw").split(","):
        match = _SIZE_ENTRY.match(raw)
        if not match:
            continue
        kind, limit, value = match.groups()
        if kind == "max" and viewport > float(limit):
            continue
        if kind == "min" and viewport < float(limit):
            continue
        value = value.strip()
        if value.endswith("px"):
            return float(value[:-2])
        if value.endswith("vw"):
            return viewport * float(value[:-2]) / 100
        calc = _CALC.match(value)
        if calc:
            return viewport - float(calc.group(1))
        return float(viewport)
    return float(viewport)


def parse_srcset(srcset: str) -> list[tuple[int, str]]:
    """`url 640w, url 828w` → [(640, url), …] (폭 오름차순). 폭 표기가 없는 후보는 버린다."""
    out: list[tuple[int, str]] = []
    for part in srcset.split(","):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].endswith("w") and bits[1][:-1].isdigit():
            out.append((int(bits[1][:-1]), bits[0]))
    return sorted(out)


def pick_candidate(candidates: list[tuple[int, str]], needed: float) -> str | None:
    """브라우저처럼 필요한 픽셀 폭 이상인 가장 작은 후보, 없으면 가장 큰 후보."""
    if not candidates:
        return None
    for width, url in candidates:
        if width >= needed:
            return url
    return candidates[-1][1]


def image_urls(page_html: str, profiles: Iterable[tuple[int, float]] = PROFILES) -> list[str]:
    """페이지 안 최적화 사진(`/_next/image`)마다 대표 화면이 고를 후보 주소."""
    urls: list[str] = []
    for tag in _IMG.findall(page_html):
        attrs = {name.lower(): html.unescape(value) for name, value in _ATTR.findall(tag)}
        candidates = [c for c in parse_srcset(attrs.get("srcset", "")) if "/_next/image" in c[1]]
        if not candidates:
            continue
        for viewport, dpr in profiles:
            url = pick_candidate(candidates, slot_width(attrs.get("sizes", ""), viewport) * dpr)
            if url and url not in urls:
                urls.append(url)
    return urls


Fetch = Callable[[str, dict[str, str]], tuple[int, bytes, dict[str, str]]]


def http_get(url: str, headers: dict[str, str], timeout: float = 60.0) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — https 고정
            return response.status, response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, b"", {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b"", {}


@dataclass
class WarmResult:
    pages: int = 0
    images: int = 0
    hits: int = 0
    filled: int = 0
    failed: list[str] = field(default_factory=list)
    stopped_by_budget: bool = False


def warm(
    public_domain: str,
    fetch: Fetch = http_get,
    *,
    budget_seconds: float = 2400,
    dry_run: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> WarmResult:
    result = WarmResult()
    status, body, _ = fetch(f"https://{public_domain}/api/v1/public/hospitals", {})
    if status != 200:
        result.failed.append(f"병원 목록 HTTP {status or '연결 실패'}")
        return result
    started = clock()
    for host, _slug in public_hosts(_json(body), public_domain):
        for page in PAGES:
            status, body, _ = fetch(f"https://{host}{page}", {})
            if status != 200:
                if page == "":
                    result.failed.append(f"{host}{page}: HTTP {status or '연결 실패'}")
                continue
            result.pages += 1
            for path in image_urls(body.decode("utf-8", "replace")):
                result.images += 1
                if dry_run:
                    continue
                if clock() - started > budget_seconds:
                    result.stopped_by_budget = True
                    return result
                url = path if path.startswith("http") else f"https://{host}{path}"
                status, _, headers = fetch(url, {"Accept": ACCEPT})
                if status != 200:
                    result.failed.append(f"{url[:120]}: HTTP {status or '연결 실패'}")
                elif headers.get("x-nextjs-cache") == "HIT":
                    result.hits += 1
                else:
                    result.filled += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public-domain", required=True)
    parser.add_argument("--budget-seconds", type=float, default=2400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = warm(args.public_domain, budget_seconds=args.budget_seconds, dry_run=args.dry_run)
    print(
        f"사진 캐시 미리 채우기: 페이지 {result.pages}, 사진 후보 {result.images}, "
        f"새로 채움 {result.filled}, 이미 있음 {result.hits}, 실패 {len(result.failed)}"
        + (" — 시간 한도로 멈춤" if result.stopped_by_budget else "")
    )
    for line in result.failed[:20]:
        print(f"  실패: {line}")
    return 0  # 배포를 막지 않는다


if __name__ == "__main__":
    sys.exit(main())
