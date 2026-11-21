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
from zoneinfo import ZoneInfo
from typing import Any

SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_API = "https://reputation.motionlabs.kr/api/v1/public"
DEFAULT_PLATFORM_HOST = "reputation.motionlabs.kr"
USER_AGENT = "reputation-site-config-publish-check/1"
PAGES = ("/", "/contents")
# 참고로만 보이고 차이로 세지 않는 칸.
INFO_KEYS = frozenset({"captured_at", "seoul_date", "api.site_config_version", "api.site_config_layout", "headers"})

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


VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
SKIP_TEXT_TAGS = frozenset({"script", "style", "template", "noscript"})
# 모양을 정하는 속성(섹션 정렬·배경·첫 화면 모양·사진 초점 등). 클래스는 레이아웃 CSS 모듈 이름이 섞여도 배포 사이에만 바뀐다.
LOOK_ATTRS = ("class", "style")


def _look(attr: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in sorted(attr.items()) if key in LOOK_ATTRS or key.startswith("data-")}


class PageParser(HTMLParser):
    """홈·글 목록 HTML에서 비교할 구조와 화면 글을 뽑는다(표준 라이브러리만).

    화면 글은 섹션마다 모은다(가장 안쪽 섹션에). `aria-hidden="true"` 안의 글(장식 'Q' 등)과 스크립트는 빼고,
    기기별 줄바꿈 `<br class=…>`은 `↵클래스` 표지로 넣는다. 섹션 밖의 글(머리글·메뉴·바닥글·하단 바)은 `outside`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, Any]] = []
        self.outside: list[str] = []
        self.looks: list[str] = []
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.faq_screen: list[str] = []
        self.nav: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.jsonld_raw: list[str] = []
        self.visible: list[str] = []
        # (태그, 숨김 여부, 섹션 dict 또는 None)
        self._stack: list[tuple[str, bool, dict[str, Any] | None]] = []
        self._capture: tuple[str, list[str]] | None = None
        self._jsonld: list[str] | None = None
        self._link: tuple[str, list[str]] | None = None

    # --- 상태 ---
    def _hidden(self) -> bool:
        return any(hidden for _, hidden, _ in self._stack)

    def _section(self) -> dict[str, Any] | None:
        for _, _, section in reversed(self._stack):
            if section is not None:
                return section
        return None

    def _in(self, tag: str) -> bool:
        return any(name == tag for name, _, _ in self._stack)

    def _in_faq(self) -> bool:
        return any(section is not None and section.get("id") == "faq" for _, _, section in self._stack)

    def _emit(self, text: str) -> None:
        if self._hidden() or any(name in SKIP_TEXT_TAGS for name, _, _ in self._stack):
            return
        section = self._section()
        (section["_text"] if section is not None else self.outside).append(text)
        self.visible.append(text)

    # --- HTMLParser ---
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag in VOID_TAGS:
            self._void(tag, attr)
            return
        hidden = attr.get("aria-hidden") == "true" or "hidden" in attr
        section = None
        if tag == "section":
            section = {"id": attr.get("id") or None, "label": attr.get("aria-label") or None, "title": None}
            section["look"] = _look(attr)
            section["_text"] = []
            self.sections.append(section)
        elif any(key.startswith(("data-look", "data-hero")) for key in attr):
            self.looks.append(json.dumps(_look(attr), ensure_ascii=False, sort_keys=True))
        self._stack.append((tag, hidden, section))
        if tag == "script" and attr.get("type") == "application/ld+json":
            self._jsonld = []
        elif tag in {"h1", "h2"} or (tag == "dt" and self._in_faq()):
            self._capture = (tag, [])
        elif tag == "a" and attr.get("href") and (self._in("nav") or self._in("header")):
            self._link = (attr["href"], [])

    def _void(self, tag: str, attr: dict[str, str]) -> None:
        if tag == "meta":
            key = attr.get("property") or attr.get("name")
            if key in {"description", "og:title", "og:description", "og:image", "og:image:alt", "twitter:image"}:
                self.meta[key] = attr.get("content", "")
        elif tag == "link" and attr.get("rel") == "canonical":
            self.canonical = attr.get("href")
        elif tag == "br" and attr.get("class"):
            self._emit(f"↵{attr['class']}")
        elif tag == "img":
            self._emit(f"[이미지:{attr.get('alt', '')}]")

    def handle_endtag(self, tag: str) -> None:
        if not any(name == tag for name, _, _ in self._stack):
            return  # 짝 없는 닫는 태그
        if tag == "script" and self._jsonld is not None:
            self.jsonld_raw.append("".join(self._jsonld))
            self._jsonld = None
        if self._capture is not None and self._capture[0] == tag:
            text = _text("".join(self._capture[1]))
            if tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
                section = self._section()
                if section is not None and section["title"] is None:
                    section["title"] = text
            elif tag == "dt":
                self.faq_screen.append(text)
            self._capture = None
        if tag == "a" and self._link is not None:
            self.nav.append(f"{_text(''.join(self._link[1]))} → {self._link[0]}")
            self._link = None
        # 닫는 태그가 빠진 HTML에도 버티게 가장 가까운 같은 태그까지 닫는다.
        while self._stack:
            name, _, _ = self._stack.pop()
            if name == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._jsonld is not None:
            self._jsonld.append(data)
            return
        if not data.strip():
            return
        self._emit(data)
        if self._hidden():
            return
        if self._capture is not None:
            self._capture[1].append(data)
        if self._link is not None:
            self._link[1].append(data)


def visible_text(body: str) -> str:
    parser = PageParser()
    parser.feed(body)
    parser.close()
    return _text(" ".join(parser.visible))


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
            {
                **{key: value for key, value in section.items() if value is not None and not key.startswith("_")},
                "text": _text(" ".join(section["_text"])),
            }
            for section in parser.sections
        ],
        "looks": parser.looks,
        "outside": _text(" ".join(parser.outside)),
        "h2": parser.h2,
        "faq_screen": parser.faq_screen,
        "faqpage": _faqpage_questions(items),
        "jsonld_invalid": sum(1 for item in items if isinstance(item, dict) and "@invalid" in item),
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


def fresh_fetch(
    url: str, fetch: Fetch, *, sleep: Callable[[float], None] = time.sleep, attempts: int = 6
) -> tuple[int, dict[str, str], str]:
    """캐시가 오래된(STALE) 화면은 새로 만들어질 때까지 몇 번 더 읽는다.

    홈은 '오늘' 칸(오늘 진료시간·요일)을 만든 날 기준으로 그린다. 어제 만든 화면과 오늘 만든 화면을 비교하면
    설정과 무관한 차이가 난다. STALE 응답은 뒤에서 새로 만들기를 시작하므로, 잠시 뒤 다시 읽으면 지금 만든 화면이 온다.
    """

    result = fetch(url)
    for _ in range(attempts - 1):
        if (result[1].get("x-nextjs-cache") or "").upper() != "STALE":
            break
        sleep(3)
        result = fetch(url)
    return result


def _seoul_date() -> str:
    return datetime.now(timezone.utc).astimezone(SEOUL).date().isoformat()


def _api_hospital(status: int, body: str) -> dict[str, Any] | None:
    if status != 200:
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _version(hospital: dict[str, Any] | None) -> int | None:
    site_config = (hospital or {}).get("site_config")
    version = site_config.get("version") if isinstance(site_config, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def snapshot(
    slug: str,
    *,
    api: str,
    platform_host: str,
    fetch: Fetch = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    status, _, body = fetch(f"{api}/hospitals/{slug}")
    hospital = _api_hospital(status, body)
    if hospital is None:
        raise SystemExit(f"공개 API가 {status}(또는 읽을 수 없는 응답)를 돌려주었습니다 — 공개 병원 slug인지 확인하세요: {slug}")
    site_config = hospital.get("site_config")
    out: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seoul_date": _seoul_date(),
        "slug": slug,
        "api": {
            "site_layout": hospital.get("site_layout"),
            "site_config_version": _version(hospital),
            "site_config_layout": site_config.get("layout") if isinstance(site_config, dict) else None,
        },
        "pages": {},
    }
    hosts = hosts_for(slug, hospital, platform_host)
    for host in hosts:
        for path in PAGES:
            out["pages"][f"{host}{path}"] = page_snapshot(*fresh_fetch(f"https://{host}{path}", fetch, sleep=sleep))
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
        if page["jsonld_invalid"]:
            found.append(f"{key}: 읽을 수 없는 구조화 정보(JSON-LD) {page['jsonld_invalid']}개")
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
    """공개 판 번호가 `version` 이상이 되고(공개 API), `text`가 있으면 대표 주소 홈 화면 글에 보일 때까지.

    화면 글은 HTML 원문이 아니라 보이는 글(스크립트·구조화 정보·숨긴 글 제외, 문자 참조 풀고 공백 하나로)에서 찾는다.
    읽지 못한 응답은 '아직'으로 보고 다시 읽는다. 제한 시간을 넘겨 도착한 응답은 성공으로 치지 않는다.
    """

    started = clock()
    deadline = started + timeout
    hosts: list[str] | None = None
    wanted = _text(text) if text is not None else None
    while True:
        if hosts is None:
            status, _, body = fetch(f"{api}/hospitals/{slug}")
            hospital = _api_hospital(status, body)
            current = _version(hospital)
            if clock() > deadline:
                break
            if hospital is not None and current is not None and current >= version:
                hosts = hosts_for(slug, hospital, platform_host)
                print(f"[{clock() - started:5.0f}초] 공개 API: 공개 판 {current}")
                if wanted is None:
                    print("화면 글을 지정하지 않아 공개 API만 확인했습니다. 홈은 snapshot으로 비교하세요.")
                    return 0
        if hosts is not None and wanted is not None:
            status, headers, body = fetch(f"https://{hosts[0]}/")
            if clock() > deadline:
                break
            if status == 200 and wanted in visible_text(body):
                print(
                    f"[{clock() - started:5.0f}초] {hosts[0]}/ 화면에 '{wanted}'가 보입니다"
                    f" (x-nextjs-cache={headers.get('x-nextjs-cache')})."
                )
                return 0
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
    where = "공개 API의 공개 판 번호" if hosts is None else f"{hosts[0]}/ 화면의 '{wanted}'"
    print(f"{timeout:.0f}초 안에 {where}가 바뀌지 않았습니다.", file=sys.stderr)
    return 1


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
        if before.get("seoul_date") != after.get("seoul_date"):
            print(
                "[주의] 두 스냅샷의 날짜(한국 시간)가 다릅니다 — '오늘' 칸(오늘 진료시간·요일·전화 버튼 글)의 차이는"
                " 설정과 무관할 수 있습니다. 같은 날 다시 찍어 비교하세요."
            )
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

