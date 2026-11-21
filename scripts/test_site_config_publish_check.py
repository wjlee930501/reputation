"""scripts/site_config_publish_check.py — 네트워크 없이 가짜 응답으로."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("site_config_publish_check.py")
spec = importlib.util.spec_from_file_location("site_config_publish_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)

API = "https://api.test/api/v1/public"

FAQ_LD = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
        {"@type": "Question", "name": "첫 질문?", "acceptedAnswer": {"@type": "Answer", "text": "답"}},
        {"@type": "Question", "name": "둘째 질문?", "acceptedAnswer": {"@type": "Answer", "text": "답"}},
    ],
}


def home(*, faq_title: str = "자주 묻는 질문", screen_questions=("첫 질문?", "둘째 질문?"), today: str = "09:00") -> str:
    faq = "".join(f"<div><dt>{q}</dt><dd>답</dd></div>" for q in screen_questions)
    return f"""<!doctype html><html><head>
<meta name="description" content="설명"><meta property="og:title" content="병원">
<link rel="canonical" href="https://clinic.test">
<script type="application/ld+json">{json.dumps(FAQ_LD, ensure_ascii=False)}</script>
</head><body>
<header><nav><a href="/treatments">진료 영역</a><a href="tel:0212345678">전화</a></nav></header>
<section id="top"><h1>병원  제목</h1><dl><div><dt>오늘 진료</dt><dd>{today}</dd></div></dl><br></section>
<section id="treatments"><h2>진료 영역</h2><section aria-label="안쪽"><h2>안쪽 제목</h2></section></section>
<section id="faq"><h2>{faq_title}</h2><dl>{faq}</dl></section>
</body></html>"""


def fake_fetch(responses: dict[str, tuple[int, dict[str, str], str]]):
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return responses[url]

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def responses(*, version=None, layout="B", config_layout=None, page=None, domain="clinic.test"):
    hospital = {
        "slug": "clinic",
        "site_layout": layout,
        "aeo_domain": domain,
        "site_config": None if version is None else {"version": version, "layout": config_layout or layout},
    }
    body = page or home()
    out = {f"{API}/hospitals/clinic": (200, {}, json.dumps(hospital))}
    hosts = ([domain] if domain else []) + ["clinic.platform.test"]
    for host in hosts:
        out[f"https://{host}/"] = (200, {"x-nextjs-cache": "HIT", "etag": '"a"'}, body)
        out[f"https://{host}/contents"] = (200, {}, "<html><body><header><nav><a href='/'>홈</a></nav></header></body></html>")
    out[f"https://{hosts[0]}/sitemap.xml"] = (200, {}, "<urlset><url><loc>https://clinic.test</loc><lastmod>2026-11-01</lastmod></url></urlset>")
    out[f"https://{hosts[0]}/llms.txt"] = (200, {}, "# 병원")
    return out


def take(**kwargs):
    return check.snapshot("clinic", api=API, platform_host="platform.test", fetch=fake_fetch(responses(**kwargs)))


def test_snapshot_reads_structure_from_both_hosts_and_only_gets():
    fetch = fake_fetch(responses())
    snap = check.snapshot("clinic", api=API, platform_host="platform.test", fetch=fetch)
    assert list(snap["pages"]) == [
        "clinic.test/",
        "clinic.test/contents",
        "clinic.platform.test/",
        "clinic.platform.test/contents",
    ]
    page = snap["pages"]["clinic.test/"]
    assert page["h1"] == ["병원 제목"]
    assert page["sections"] == [
        {"id": "top"},
        {"id": "treatments", "title": "진료 영역"},
        {"label": "안쪽", "title": "안쪽 제목"},
        {"id": "faq", "title": "자주 묻는 질문"},
    ]
    assert page["faq_screen"] == page["faqpage"] == ["첫 질문?", "둘째 질문?"]
    assert page["hero_facts"] == ["오늘 진료"]
    assert page["nav"] == ["진료 영역 → /treatments", "전화 → tel:0212345678"]
    assert page["meta"] == {"description": "설명", "og:title": "병원"}
    assert page["jsonld_types"] == {"Answer": 2, "FAQPage": 1, "Question": 2}
    assert snap["sitemap"]["urls"] == 1 and snap["llms"]["status"] == 200
    assert snap["issues"] == []
    assert all(not url.startswith("https://api.test") or url.endswith("/hospitals/clinic") for url in fetch.calls)


def test_platform_only_hospital_uses_the_subdomain():
    snap = take(domain=None)
    assert list(snap["pages"]) == ["clinic.platform.test/", "clinic.platform.test/contents"]


def test_unchanged_publish_differs_only_in_version_and_headers():
    before = take()
    after = take(version=1)
    after["pages"]["clinic.test/"]["headers"]["x-nextjs-cache"] = "STALE"
    diffs, notes = check.differences(before, after)
    assert diffs == []
    assert any(note.startswith("api.site_config_version") for note in notes)


def test_today_values_do_not_count_as_a_change():
    before = take(page=home(today="09:00"))
    after = take(page=home(today="휴진"))
    assert check.differences(before, after)[0] == []


def test_one_field_change_shows_only_that_field():
    before = take()
    after = take(version=2, page=home(faq_title="궁금한 점"))
    diffs, _ = check.differences(before, after)
    keys = sorted(line.split(":", 1)[0] for line in diffs)
    assert keys == [
        "pages.clinic.platform.test/.h2",
        "pages.clinic.platform.test/.sections",
        "pages.clinic.test/.h2",
        "pages.clinic.test/.sections",
    ]


def test_issues_flag_faq_mismatch_and_diverged_layout():
    snap = take(version=1, config_layout="C", page=home(screen_questions=("첫 질문?",)))
    assert any("공개 판의 레이아웃" in line for line in snap["issues"])
    assert any("화면 FAQ 질문(1)과 FAQPage 질문(2)" in line for line in snap["issues"])


def test_wait_returns_when_version_and_text_appear():
    states = iter([None, 1, 1])
    pages = iter([home(), home(faq_title="궁금한 점")])

    def fetch(url: str):
        if url.endswith("/hospitals/clinic"):
            version = next(states)
            return 200, {}, json.dumps(
                {"aeo_domain": "clinic.test", "site_config": None if version is None else {"version": version}}
            )
        return 200, {"x-nextjs-cache": "STALE"}, next(pages)

    now = [0.0]
    result = check.wait_for(
        "clinic",
        version=1,
        text="궁금한 점",
        api=API,
        platform_host="platform.test",
        timeout=100,
        interval=10,
        fetch=fetch,
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
    )
    assert result == 0


def test_wait_times_out(capsys: pytest.CaptureFixture[str]):
    now = [0.0]
    result = check.wait_for(
        "clinic",
        version=3,
        text=None,
        api=API,
        platform_host="platform.test",
        timeout=30,
        interval=10,
        fetch=lambda url: (200, {}, json.dumps({"site_config": {"version": 2}})),
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
    )
    assert result == 1
    assert "공개 판 번호" in capsys.readouterr().err
