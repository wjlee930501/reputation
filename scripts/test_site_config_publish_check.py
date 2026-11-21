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


def home(
    *,
    faq_title: str = "자주 묻는 질문",
    screen_questions=("첫 질문?", "둘째 질문?"),
    today: str = "09:00",
    note: str = "안내 문구",
    align: str = "start",
    jsonld: str | None = None,
) -> str:
    # C안처럼 질문 앞에 장식 'Q'(aria-hidden)가 붙는다.
    faq = "".join(f'<div><dt><span aria-hidden="true">Q</span>{q}</dt><dd>답</dd></div>' for q in screen_questions)
    ld = jsonld if jsonld is not None else json.dumps(FAQ_LD, ensure_ascii=False)
    return f"""<!doctype html><html><head>
<meta name="description" content="설명"><meta property="og:title" content="병원">
<link rel="canonical" href="https://clinic.test">
<script type="application/ld+json">{ld}</script>
</head><body>
<header><nav><a href="/treatments">진료 영역</a><a href="tel:0212345678">전화</a></nav></header>
<section id="top"><h1>병원  제목</h1><dl><div><dt>오늘 진료</dt><dd>{today}</dd></div></dl><br></section>
<section id="treatments" data-look-align="{align}"><h2>진료 영역</h2><p>{note}</p><section aria-label="안쪽"><h2>안쪽 제목</h2></section></section>
<section id="faq"><h2>{faq_title}</h2><dl>{faq}</dl></section>
<footer>바닥글 &amp; 안내</footer>
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
        {"id": "top", "look": {}, "text": "병원 제목 오늘 진료 09:00 ↵"},
        {"id": "treatments", "title": "진료 영역", "look": {"data-look-align": "start"}, "text": "진료 영역 안내 문구"},
        {"label": "안쪽", "title": "안쪽 제목", "look": {}, "text": "안쪽 제목"},
        {"id": "faq", "title": "자주 묻는 질문", "look": {}, "text": "자주 묻는 질문 Q첫 질문? 답 Q둘째 질문? 답"},
    ]
    assert page["faq_screen"] == page["faqpage"] == ["첫 질문?", "둘째 질문?"]
    assert page["outside"] == "진료 영역전화 바닥글 & 안내"
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


def test_note_and_look_changes_are_differences():
    before = take()
    for page in (home(note="새 안내"), home(align="center")):
        diffs, _ = check.differences(before, take(version=2, page=page))
        assert sorted(line.split(":", 1)[0] for line in diffs) == [
            "pages.clinic.platform.test/.sections",
            "pages.clinic.test/.sections",
        ]


def test_stale_pages_are_read_again_until_regenerated():
    calls = []
    answers = iter([(200, {"x-nextjs-cache": "STALE"}, "old"), (200, {"x-nextjs-cache": "HIT"}, "new")])

    def fetch(url):
        calls.append(url)
        return next(answers)

    assert check.fresh_fetch("https://x/", fetch, sleep=lambda _: None)[2] == "new"
    assert len(calls) == 2


def test_compare_warns_when_snapshots_are_from_different_days(tmp_path, capsys):
    before, after = take(), take()
    after["seoul_date"] = "2099-01-01"
    (tmp_path / "a.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(after), encoding="utf-8")
    assert check.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    assert "날짜(한국 시간)나 화면을 만든 요일이 다릅니다" in capsys.readouterr().out


def test_broken_jsonld_is_an_issue():
    snap = take(page=home(jsonld="{broken"))
    assert any("읽을 수 없는 구조화 정보" in line for line in snap["issues"])


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
    states = iter(["broken", None, 1])
    pages = iter([home(), home(faq_title="궁금한 &amp; 점")])

    def fetch(url: str):
        if url.endswith("/hospitals/clinic"):
            version = next(states)
            if version == "broken":
                return 200, {}, "{not json"
            return 200, {}, json.dumps(
                {"aeo_domain": "clinic.test", "site_config": None if version is None else {"version": version}}
            )
        return 200, {"x-nextjs-cache": "STALE"}, next(pages)

    now = [0.0]
    result = check.wait_for(
        "clinic",
        version=1,
        text="궁금한 & 점",
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


def test_wait_does_not_find_text_that_is_only_in_scripts_or_hidden():
    page = home().replace("</body>", '<script>"숨은 글"</script><span aria-hidden="true">숨은 글</span></body>')
    now = [0.0]
    result = check.wait_for(
        "clinic",
        version=1,
        text="숨은 글",
        api=API,
        platform_host="platform.test",
        timeout=20,
        interval=10,
        fetch=lambda url: (200, {}, json.dumps({"site_config": {"version": 1}}))
        if url.endswith("/hospitals/clinic")
        else (200, {}, page),
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
    )
    assert result == 1


def test_wait_ignores_a_success_that_arrives_after_the_deadline():
    now = [0.0]

    def slow_fetch(url):
        now[0] += 50  # 응답이 제한 시간 뒤에 도착
        return 200, {}, json.dumps({"site_config": {"version": 5}})

    result = check.wait_for(
        "clinic",
        version=1,
        text=None,
        api=API,
        platform_host="platform.test",
        timeout=10,
        interval=5,
        fetch=slow_fetch,
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
    )
    assert result == 1


def test_hidden_label_doctor_photo_style_plain_breaks_and_title_spaces_are_compared():
    base = """<html><body><section id="doctor" data-look-align="start"><span aria-hidden="true">{label}</span>
<h2><span>내과,</span>{space}<strong>방문 전에{br}확인하세요</strong></h2>
<div style="aspect-ratio:{ratio}"><img alt="원장" style="object-position:{focus}"></div></section></body></html>"""

    def page(label="DOCTOR", space=" ", br="", ratio="4/5", focus="50% 50%"):
        return check.page_snapshot(200, {}, base.format(label=label, space=space, br=br, ratio=ratio, focus=focus))

    original = page()
    assert original["h2"] == ["내과, 방문 전에확인하세요"]
    for changed in (page(label=""), page(br="<br>"), page(ratio="1/1"), page(focus="50% 20%")):
        assert changed["sections"] != original["sections"]
    assert check.visible_text(base.format(label="DOCTOR", space=" ", br="<br>", ratio="1", focus="0")).count("DOCTOR") == 0


def test_rendered_day_comes_from_the_page():
    b_payload = 'self.__next_f.push([1,"{\\"renderedTodayKey\\":\\"sat\\"}"])'
    assert check.rendered_days(f"<script>{b_payload}</script>") == ["sat"]
    assert check.rendered_days("<p>오늘(<!-- -->토<!-- -->) 09:00</p>") == ["sat"]
    assert check.rendered_days("<p>11<!-- -->월 <!-- -->21<!-- -->일 (<!-- -->토<!-- -->)</p>") == ["sat"]
    snap = {"pages": {"x/": {"rendered_day": ["fri"]}}}
    assert check.day_warnings(snap, today="sat") and not check.day_warnings(snap, today="fri")


def test_connection_errors_are_a_status_not_a_crash(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert check.http_get("https://x.test/") == (0, {}, "")


def test_look_attributes_stay_with_their_section():
    # A안 SectionLook처럼 섹션 안쪽 요소에 붙는 정렬·배경 — 다른 섹션으로 옮기면 차이여야 한다.
    page = """<html><body><section id="treatments"><div {a}><h2>진료 영역</h2></div></section>
<section id="doctor"><div {b}><h2>의료진</h2></div></section></body></html>"""
    look = 'data-look-align="start" data-look-bg="tint"'
    first = check.page_snapshot(200, {}, page.format(a=look, b=""))
    moved = check.page_snapshot(200, {}, page.format(a="", b=look))
    assert first["sections"] != moved["sections"]
    assert "data-look-bg" in first["sections"][0]["text"]


def test_error_body_read_failure_is_still_a_status(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    class SlowBody(io.BytesIO):
        def read(self, *args):
            raise TimeoutError("read timed out")

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://x.test/", 503, "unavailable", {}, SlowBody())

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert check.http_get("https://x.test/")[0] == 503


def test_wrapping_section_look_belongs_to_the_wrapped_section():
    # 실제 SectionLook은 <section>을 바깥에서 감싼다.
    page = """<html><body><div {a}><section id="treatments"><h2>진료 영역</h2></section></div>
<div {b}><section id="doctor"><h2>의료진</h2></section></div><div data-look-align="end"><p>섹션 밖</p></div></body></html>"""
    look = 'class="clinic-look" data-look-align="start" data-look-bg="tint"'
    first = check.page_snapshot(200, {}, page.format(a=look, b=""))
    moved = check.page_snapshot(200, {}, page.format(a="", b=look))
    assert first["sections"][0]["look"]["wrap"] and "wrap" not in first["sections"][1]["look"]
    assert first["sections"] != moved["sections"]
    assert "data-look-align" in first["outside"]  # 섹션을 감싸지 않은 모양은 바깥 글에 남는다


def test_truncated_error_body_is_still_a_status(monkeypatch):
    import http.client
    import io
    import urllib.error
    import urllib.request

    class Truncated(io.BytesIO):
        def read(self, *args):
            raise http.client.IncompleteRead(b"par", 10)

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://x.test/", 503, "unavailable", {}, Truncated())

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert check.http_get("https://x.test/")[0] == 503
