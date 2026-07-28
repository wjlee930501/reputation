"""Probe Naver search for Korean hospital AEO/GEO vendors.

WebSearch is US-indexed and misses fresh Korean marketing copy, which is
exactly where this market advertises. Naver is the reliable path per the
insane-search naver reference.
"""
from __future__ import annotations

import re
import sys
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

QUERIES = [
    "병원 GEO 최적화",
    "병원 AI 검색 최적화 대행",
    "챗GPT 병원 노출 마케팅",
    "의료 GEO AEO 대행사",
    "병원 AI 노출 솔루션",
    "GEO 최적화 비용",
]

# Aggregators and portals are noise for vendor discovery.
SKIP_HOST_PARTS = (
    "naver.com", "daum.net", "google.", "youtube.com", "tistory.com",
    "brunch.co.kr", "wikipedia.org", "facebook.com", "instagram.com",
)


def session() -> requests.Session:
    s = requests.Session(impersonate="chrome124")
    s.headers.update({
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.google.com/",
    })
    s.get("https://www.naver.com/", timeout=15)  # cookie warm-up
    s.headers["Referer"] = "https://www.naver.com/"
    return s


def probe(s: requests.Session, query: str, where: str) -> list[tuple[str, str]]:
    url = f"https://search.naver.com/search.naver?where={where}&query={quote(query)}"
    r = s.get(url, timeout=20)
    if r.status_code != 200 or len(r.content) < 3000:
        print(f"  !! {where} HTTP {r.status_code} size={len(r.content)}", file=sys.stderr)
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        host = (urlparse(href).hostname or "").lower()
        if not host or any(p in host for p in SKIP_HOST_PARTS):
            continue
        label = re.sub(r"\s+", " ", a.get_text(strip=True))[:90]
        if len(label) < 4:
            continue
        out.append((host, label))
    return out


def main() -> int:
    s = session()
    hosts: dict[str, set[str]] = {}
    for q in QUERIES:
        print(f"\n=== {q} ===")
        for where in ("post", "web"):
            for host, label in probe(s, q, where):
                hosts.setdefault(host, set()).add(label)
    print("\n\n########## VENDOR HOST CANDIDATES ##########")
    for host, labels in sorted(hosts.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{host}  ({len(labels)} hits)")
        for label in sorted(labels)[:4]:
            print(f"    - {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
