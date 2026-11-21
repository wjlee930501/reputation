#!/usr/bin/env python3
"""레이아웃 편집기 '공개 반영' 전후의 운영 공개 화면을 읽기만 해서 비교한다.

왜: 공개 반영은 공개 페이지를 몇 분 안에 바꾼다. 첫 반영은 '화면이 바뀌지 않는 반영 → 작은 변화 하나 →
되돌리기' 순서로 한 병원씩 확인한다(`docs/ops/site-config-first-publish-check.md`). 그때 사람이 눈으로
대조하던 것(섹션 순서·제목, FAQ 화면 = FAQPage, 구조화 정보, 메뉴, 공유 메타, sitemap·llms.txt)을 반영
전후 스냅샷 두 장의 차이로 보이게 한다.

무엇을 읽나(GET만, 운영에 쓰지 않는다):
- 공개 API `/public/hospitals/{slug}` — `site_layout`, 공개 판 `site_config.version`
- 병원 주소(자기 도메인이 있으면 그 주소와 플랫폼 하위 도메인 둘 다)의 홈·글 목록 HTML
- 대표 주소의 `sitemap.xml`·`llms.txt`

    python3 scripts/site_config_publish_check.py snapshot SLUG -o before.json
    # (편집기에서 공개 반영)
    python3 scripts/site_config_publish_check.py wait SLUG --version 1 [--text "바꾼 제목"]
    python3 scripts/site_config_publish_check.py snapshot SLUG -o after.json
    python3 scripts/site_config_publish_check.py compare before.json after.json

`compare`는 다른 칸을 모두 보이고, 다른 칸이 있으면 종료 코드 1이다(공개 판 번호·레이아웃과 캐시 헤더는 참고로만 보이고
세지 않는다). 화면이 바뀌지 않는 반영이면 0이어야 하고, 한 칸만 바꾼 반영이면 그 칸만 나와야 한다.
오늘 날짜에 따라 바뀌는 글('오늘 진료' 시간 등)은 비교하지 않는다. 휴대폰 폭 화면은 이 스크립트가 보지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

DEFAULT_API = "https://reputation.motionlabs.kr/api/v1/public"
DEFAULT_PLATFORM_HOST = "reputation.motionlabs.kr"
USER_AGENT = "reputation-site-config-publish-check/1"
PAGES = ("/", "/contents")
# 참고로만 보이고 차이로 세지 않는 칸.
INFO_KEYS = frozenset({"captured_at", "api.site_config_version", "api.site_config_layout", "headers"})

Fetch = Callable[[str], tuple[int, dict[str, str], str]]


def http_get(url: str, timeout: float = 30.0) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, {k.lower(): v for k, v in response.headers.items()}, body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace") if error.fp else ""
        return error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, body


def _text(value: str) -> str:
    return " ".join(value.split())


class PageParser(HTMLParser):
    """홈·글 목록 HTML에서 비교할 구조만 뽑는다(표준 라이브러리만)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, Any]] = []
        self._open_sections: list[dict[str, Any]] = []
        self._tag_stack: list[str] = []
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.faq_screen: list[str] = []
        self.hero_facts: list[str] = []
        self.nav: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.jsonld_raw: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._in_jsonld = False
        self._nav_depth = 0
        self._link_href: str | None = None
        self._link_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag in {"meta", "link", "img", "br", "input", "source", "hr"}:
            self._void(tag, attr)
            return
        self._tag_stack.append(tag)
        if tag == "section":
            section = {"id": attr.get("id") or None, "label": attr.get("aria-label") or None, "title": None}
            self.sections.append(section)
            self._open_sections.append(section)
        elif tag == "script" and attr.get("type") == "application/ld+json":
            self._in_jsonld = True
            self._buffer = []
        elif tag in {"h1", "h2"} or (tag == "dt" and self._open_sections):
            self._capture = tag
            self._buffer = []
        if tag in {"nav", "header"}:
            self._nav_depth += 1
        if tag == "a" and self._nav_depth and attr.get("href"):
            self._link_href = attr["href"]
            self._link_buffer = []

    def _void(self, tag: str, attr: dict[str, str]) -> None:
        if tag == "meta":
            key = attr.get("property") or attr.get("name")
            if key in {"description", "og:title", "og:description", "og:image", "og:image:alt", "twitter:image"}:
                self.meta[key] = attr.get("content", "")
        elif tag == "link" and attr.get("rel") == "canonical":
            self.canonical = attr.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self.jsonld_raw.append("".join(self._buffer))
            self._in_jsonld = False
        if self._capture == tag:
            text = _text("".join(self._buffer))
            if tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
                if self._open_sections and self._open_sections[-1]["title"] is None:
                    self._open_sections[-1]["title"] = text
            elif tag == "dt":
                section_id = self._open_sections[-1]["id"] if self._open_sections else None
                if any(section["id"] == "faq" for section in self._open_sections):
                    self.faq_screen.append(text)
                elif section_id == "top":
                    self.hero_facts.append(text)
            self._capture = None
        if tag == "a" and self._link_href is not None:
            self.nav.append(f"{_text(''.join(self._link_buffer))} → {self._link_href}")
            self._link_href = None
        if tag in {"nav", "header"} and self._nav_depth:
            self._nav_depth -= 1
        if tag == "section" and self._open_sections:
            self._open_sections.pop()
        # 닫는 태그가 빠진 HTML에도 버티게 가장 가까운 같은 태그까지 닫는다.
        if tag in self._tag_stack:
            while self._tag_stack and self._tag_stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if self._in_jsonld or self._capture:
            self._buffer.append(data)
        if self._link_href is not None:
            self._link_buffer.append(data)


def _jsonld_items(raw_blocks: list[str]) -> list[Any]:
    items: list[Any] = []
    for raw in raw_blocks:
        try:
            parsed = json.loads(raw)
        except ValueError:
            items.append({"@invalid": raw[:80]})
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("@graph"), list):
            items.extend(parsed["@graph"])
        elif isinstance(parsed, list):
            items.extend(parsed)
        else:
            items.append(parsed)
    return items


def _types(value: Any, counter: Counter[str]) -> None:
    if isinstance(value, dict):
        kind = value.get("@type")
        for name in kind if isinstance(kind, list) else [kind]:
            if isinstance(name, str):
                counter[name] += 1
        for child in value.values():
            _types(child, counter)
    elif isinstance(value, list):
        for child in value:
            _types(child, counter)


def _faqpage_questions(items: list[Any]) -> list[str]:
    questions: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("@type") == "FAQPage":
            for entity in item.get("mainEntity") or []:
                if isinstance(entity, dict) and isinstance(entity.get("name"), str):
                    questions.append(_text(entity["name"]))
    return questions


def page_snapshot(status: int, headers: dict[str, str], body: str) -> dict[str, Any]:
    parser = PageParser()
    if status == 200:
        parser.feed(body)
        parser.close()
    items = _jsonld_items(parser.jsonld_raw)
    counter: Counter[str] = Counter()
    _types(items, counter)
    canonical_jsonld = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "status": status,
        "headers": {key: headers.get(key) for key in ("x-nextjs-cache", "etag", "cache-control")},
        "canonical": parser.canonical,
        "h1": parser.h1,
        "sections": [
            {key: value for key, value in section.items() if value is not None} for section in parser.sections
        ],
        "h2": parser.h2,
        "faq_screen": parser.faq_screen,
        "faqpage": _faqpage_questions(items),
        "hero_facts": parser.hero_facts,
        "nav": parser.nav,
        "meta": parser.meta,
        "jsonld_types": dict(sorted(counter.items())),
        "jsonld_sha256": hashlib.sha256(canonical_jsonld.encode()).hexdigest(),
    }


def sitemap_snapshot(status: int, body: str) -> dict[str, Any]:
    urls = re.findall(r"<url>(.*?)</url>", body, flags=re.S) if status == 200 else []
    entries = []
    for block in urls:
        loc = re.search(r"<loc>(.*?)</loc>", block, flags=re.S)
        lastmod = re.search(r"<lastmod>(.*?)</lastmod>", block, flags=re.S)
        entries.append([loc.group(1).strip() if loc else "", lastmod.group(1).strip() if lastmod else ""])
    return {"status": status, "urls": len(entries), "sha256": hashlib.sha256(json.dumps(entries).encode()).hexdigest()}


def hosts_for(slug: str, hospital: dict[str, Any], platform_host: str) -> list[str]:
    hosts = []
    domain = (hospital.get("aeo_domain") or "").strip().lower()
    if domain:
        hosts.append(domain)
    hosts.append(f"{slug}.{platform_host}")
    return hosts


def snapshot(slug: str, *, api: str, platform_host: str, fetch: Fetch = http_get) -> dict[str, Any]:
    status, _, body = fetch(f"{api}/hospitals/{slug}")
    if status != 200:
        raise SystemExit(f"공개 API가 {status}를 돌려주었습니다 — 공개 병원 slug인지 확인하세요: {slug}")
    hospital = json.loads(body)
    site_config = hospital.get("site_config")
    out: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "slug": slug,
        "api": {
            "site_layout": hospital.get("site_layout"),
            "site_config_version": site_config.get("version") if isinstance(site_config, dict) else None,
            "site_config_layout": site_config.get("layout") if isinstance(site_config, dict) else None,
        },
        "pages": {},
    }
    hosts = hosts_for(slug, hospital, platform_host)
    for host in hosts:
        for path in PAGES:
            out["pages"][f"{host}{path}"] = page_snapshot(*fetch(f"https://{host}{path}"))
    primary = hosts[0]
    sitemap_status, _, sitemap_body = fetch(f"https://{primary}/sitemap.xml")
    out["sitemap"] = sitemap_snapshot(sitemap_status, sitemap_body)
    llms_status, _, llms_body = fetch(f"https://{primary}/llms.txt")
    out["llms"] = {"status": llms_status, "sha256": hashlib.sha256(llms_body.encode()).hexdigest()}
    out["issues"] = issues(out)
    return out


def issues(snap: dict[str, Any]) -> list[str]:
    """한 장의 스냅샷 안에서 바로 보이는 문제."""

    found = []
    api = snap["api"]
    if api["site_config_version"] is not None and api["site_config_layout"] != api["site_layout"]:
        found.append("공개 판의 레이아웃이 병원 레이아웃과 다릅니다(공개 응답은 site_config를 싣지 않아야 합니다).")
    for key, page in snap["pages"].items():
        if page["status"] != 200:
            found.append(f"{key}: HTTP {page['status']}")
            continue
        if key.endswith("/") and page["faq_screen"] != page["faqpage"]:
            found.append(f"{key}: 화면 FAQ 질문({len(page['faq_screen'])})과 FAQPage 질문({len(page['faqpage'])})이 다릅니다.")
    if snap["sitemap"]["status"] != 200:
        found.append(f"sitemap.xml: HTTP {snap['sitemap']['status']}")
    if snap["llms"]["status"] != 200:
        found.append(f"llms.txt: HTTP {snap['llms']['status']}")
    return found


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and not (prefix.endswith(".headers") or prefix == "headers"):
        flat: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict) and key not in {"meta", "jsonld_types", "headers"}:
                flat.update(_flatten(child, path))
            else:
                flat[path] = child
        return flat
    return {prefix: value}


def differences(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(차이, 참고) — 참고는 공개 판 번호·캐시 헤더처럼 바뀌어도 되는 칸."""

    left, right = _flatten(before), _flatten(after)
    diffs: list[str] = []
    notes: list[str] = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) == right.get(key):
            continue
        informational = key in INFO_KEYS or key.endswith(".headers") or key == "issues"
        line = f"{key}:\n  전: {json.dumps(left.get(key), ensure_ascii=False)}\n  후: {json.dumps(right.get(key), ensure_ascii=False)}"
        (notes if informational else diffs).append(line)
    return diffs, notes


def wait_for(
    slug: str,
    *,
    version: int,
    text: str | None,
    api: str,
    platform_host: str,
    timeout: float,
    interval: float,
    fetch: Fetch = http_get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """공개 판 번호가 `version` 이상이 되고(공개 API), `text`가 있으면 대표 주소 홈에 그 글이 보일 때까지."""

    started = clock()
    hosts: list[str] | None = None
    api_seen = False
    while True:
        elapsed = clock() - started
        if not api_seen:
            status, _, body = fetch(f"{api}/hospitals/{slug}")
            hospital = json.loads(body) if status == 200 else {}
            current = (hospital.get("site_config") or {}).get("version")
            if isinstance(current, int) and current >= version:
                api_seen = True
                hosts = hosts_for(slug, hospital, platform_host)
                print(f"[{elapsed:5.0f}초] 공개 API: 공개 판 {current}")
                if text is None:
                    print("화면 글을 지정하지 않아 공개 API만 확인했습니다. 홈은 snapshot으로 비교하세요.")
                    return 0
        if api_seen and hosts and text is not None:
            status, headers, body = fetch(f"https://{hosts[0]}/")
            if status == 200 and text in body:
                print(f"[{elapsed:5.0f}초] {hosts[0]}/ 에 '{text}'가 보입니다 (x-nextjs-cache={headers.get('x-nextjs-cache')}).")
                return 0
        if elapsed >= timeout:
            where = "공개 API의 공개 판 번호" if not api_seen else f"{hosts[0] if hosts else ''}/ 화면의 '{text}'"
            print(f"{timeout:.0f}초 안에 {where}가 바뀌지 않았습니다.", file=sys.stderr)
            return 1
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API, help="공개 API 기준 주소(…/api/v1/public)")
    parser.add_argument("--platform-host", default=DEFAULT_PLATFORM_HOST, help="병원 하위 도메인의 플랫폼 호스트")
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="지금 공개 화면 스냅샷(JSON)")
    snap.add_argument("slug")
    snap.add_argument("-o", "--out", help="저장할 파일(없으면 표준 출력)")
    compare = commands.add_parser("compare", help="스냅샷 두 장의 차이")
    compare.add_argument("before")
    compare.add_argument("after")
    wait = commands.add_parser("wait", help="공개 반영이 공개 API·화면에 보일 때까지 기다림")
    wait.add_argument("slug")
    wait.add_argument("--version", type=int, required=True, help="기다릴 공개 판 번호(편집기 상태 줄의 '공개 중 설정 N판')")
    wait.add_argument("--text", help="반영 뒤 홈에 보여야 할 글(바꾼 제목 등)")
    wait.add_argument("--timeout", type=float, default=900.0)
    wait.add_argument("--interval", type=float, default=20.0)
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    if args.command == "snapshot":
        result = snapshot(args.slug, api=api, platform_host=args.platform_host)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        else:
            print(rendered)
        for line in result["issues"]:
            print(f"확인 필요: {line}", file=sys.stderr)
        print(
            f"공개 판 {result['api']['site_config_version'] or '없음'} · 레이아웃 {result['api']['site_layout'] or '없음'}"
            f" · 페이지 {len(result['pages'])}개 · 확인 필요 {len(result['issues'])}건",
            file=sys.stderr,
        )
        return 1 if result["issues"] else 0
    if args.command == "compare":
        with open(args.before, encoding="utf-8") as handle:
            before = json.load(handle)
        with open(args.after, encoding="utf-8") as handle:
            after = json.load(handle)
        diffs, notes = differences(before, after)
        for line in notes:
            print(f"[참고] {line}")
        for line in diffs:
            print(f"[다름] {line}")
        for line in after.get("issues") or []:
            print(f"[확인 필요] {line}")
        print(f"다른 칸 {len(diffs)}개 · 참고 {len(notes)}개 · 확인 필요 {len(after.get('issues') or [])}건")
        return 1 if diffs or after.get("issues") else 0
    return wait_for(
        args.slug,
        version=args.version,
        text=args.text,
        api=api,
        platform_host=args.platform_host,
        timeout=args.timeout,
        interval=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())

