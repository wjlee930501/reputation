"""참고자료 실제 문서 검증 — 2026-09-29 전수 점검 fixture와 가드별 고정 테스트.

네트워크는 쓰지 않는다. 모든 GET은 가짜 fetcher(fixture HTML) 또는 httpx.MockTransport다.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import reference_verification as rv
from app.services.reference_verification import (
    REFERENCE_CHECK_CLOCK_SKEW,
    REFERENCE_CHECK_MAX_AGE,
    REFERENCE_CHECK_REUSE_WINDOW,
    FetchResult,
    HttpxReferenceFetcher,
    OfflineReferenceFetcher,
    ReferenceVerifier,
    article_topic_terms,
    check_is_fresh_pass,
    default_reference_fetcher,
    judge_fetched_page,
    override_reference_fetcher,
    reference_check_record,
    reference_gate_status,
    reference_url_fingerprint,
    topic_fingerprint,
)
from app.utils.authority_sources import CURATED_SOURCE_URLS
from tests.reference_audit_fixture import (
    EXPECT_PASS,
    EXPECT_REMOVED,
    fixture_html,
    load_manifest,
    verify_audit_row,
)
from tests.reference_fetch_doubles import PageFetcher, document_body, page_html

NOW = datetime(2026, 9, 29, 7, 45, tzinfo=timezone.utc)

KDCA_VIEW = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn={}"
)
CURATED_HEMORRHOID_KDCA = KDCA_VIEW.format(5818)
CURATED_HEMORRHOID_AMC = (
    "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31772"
)
HEMORRHOID_TOPIC = article_topic_terms(title="치질 수술 후 회복 기간과 통증 관리", extra=["치질 수술"])
KNEE_TOPIC = article_topic_terms(title="무릎 관절염 초기 증상과 치료", extra=["무릎 통증"])

_ROWS = load_manifest()["rows"]


def _row_id(row: dict) -> str:
    return f"{row['category']}-{row['url'][-40:]}"


# ── 표 (a): 점검의 불량 URL은 전부 제거되고 정상 48건은 전부 통과한다 ──────────────


@pytest.mark.parametrize("row", _ROWS, ids=[_row_id(row) for row in _ROWS])
async def test_audit_fixture_row_is_judged_like_the_audit(row):
    check = await verify_audit_row(row)

    if row["category"] in EXPECT_PASS:
        assert check["verdict"] == "pass", check
    else:
        assert row["category"] in EXPECT_REMOVED
        assert check["verdict"] == "fail", check


def test_audit_fixture_covers_every_categorised_row():
    manifest = load_manifest()
    counts: dict[str, int] = {}
    for row in manifest["rows"]:
        counts[row["category"]] = counts.get(row["category"], 0) + 1

    assert counts == {"정상": 48, "빈 페이지": 37, "주제 불일치": 22, "죽은 링크": 4}
    # 사람 확인 필요 254행은 판정 대상이 아니다(정보 제공용 개수만 남긴다).
    assert manifest["informational_human_review_rows"] == 254
    # 정상 48건은 전부 수기 목록, 불량 63건 중 수기 목록은 국가암검진사업 1건뿐이다.
    assert all(row["url"] in CURATED_SOURCE_URLS for row in manifest["rows"] if row["category"] == "정상")
    bad_curated = [
        row["url"]
        for row in manifest["rows"]
        if row["category"] in EXPECT_REMOVED and row["url"] in CURATED_SOURCE_URLS
    ]
    assert bad_curated == ["https://www.cancer.go.kr/lay1/S1T261C262/contents.do"]


def _audit_row(category: str, predicate) -> dict:
    return next(row for row in _ROWS if row["category"] == category and predicate(row))


# ── 가드: 목록 밖 URL은 실제 GET 통과분만 ─────────────────────────────────────


async def test_outside_list_url_needs_a_real_passing_get():
    url = KDCA_VIEW.format(99999)
    fetcher = PageFetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    passed = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치질 자료", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )
    offline = await ReferenceVerifier(OfflineReferenceFetcher(), domain_spacing=0).verify(
        [{"title": "치질 자료", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert passed.checks[0]["reason"] == "page_verified"
    assert [ref["url"] for ref in passed.kept] == [url]
    # GET으로 확인하지 못한 목록 밖 URL은 남지 않는다(운영 외 환경의 기본값이 이것이다).
    assert offline.kept == []
    assert offline.checks[0]["reason"] == "not_verified"


async def test_curated_url_is_the_default_and_survives_without_network():
    outcome = await ReferenceVerifier(OfflineReferenceFetcher(), domain_spacing=0).verify(
        [{"title": "아무 라벨", "url": CURATED_HEMORRHOID_KDCA}], topic_terms=HEMORRHOID_TOPIC
    )

    assert [ref["url"] for ref in outcome.kept] == [CURATED_HEMORRHOID_KDCA]
    assert outcome.checks[0]["reason"] == "curated_unreachable"


def test_non_production_default_fetcher_never_touches_the_network(monkeypatch):
    monkeypatch.setattr(rv.settings, "APP_ENV", "test")
    assert isinstance(default_reference_fetcher(), OfflineReferenceFetcher)
    monkeypatch.setattr(rv.settings, "APP_ENV", "production")
    assert isinstance(default_reference_fetcher(), HttpxReferenceFetcher)
    fake = PageFetcher()
    with override_reference_fetcher(fake):
        assert default_reference_fetcher() is fake


# ── 가드: 모델 라벨은 주제 판정에 쓰지 않는다 ────────────────────────────────────


async def test_label_that_matches_the_article_cannot_rescue_a_different_real_page():
    """'국가건강정보포털 - 무릎 관절염' 라벨을 달았어도 실제 문서가 대장암이면 제거한다."""
    url = KDCA_VIEW.format(1234)
    fetcher = PageFetcher()
    fetcher.add_document(url, "대장암 | 국가건강정보포털 | 질병관리청", topic="대장암")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "국가건강정보포털 - 무릎 관절염 초기 증상", "url": url}],
        topic_terms=KNEE_TOPIC,
    )

    assert outcome.kept == []
    assert outcome.checks[0]["reason"] == "unrelated_topic"


async def test_a_mismatched_label_does_not_drop_a_matching_real_page():
    url = KDCA_VIEW.format(1235)
    fetcher = PageFetcher()
    fetcher.add_document(url, "무릎관절염 | 국가건강정보포털 | 질병관리청", topic="무릎관절염")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "국가암정보센터 - 대장암 예방", "url": url}], topic_terms=KNEE_TOPIC
    )

    assert [ref["url"] for ref in outcome.kept] == [url]


async def test_audit_label_mismatch_rows_are_removed_by_the_real_page():
    """점검의 실제 사례: 라벨 '헬리코박터균 감염증', 실제 문서 '악구충증'."""
    row = _audit_row("주제 불일치", lambda r: "cntnts_sn=5527" in r["url"])
    assert "헬리코박터" in row["ref_label"]

    check = await verify_audit_row(row)

    assert check["reason"] == "unrelated_topic"
    assert "악구충증" in check["page_title"]


async def test_audit_curated_screening_page_on_an_unrelated_article_is_removed():
    """점검의 수기 목록 쪽 주제 불일치 1건: '위례·송파 진료 가능한 병원 선택' 글의
    국가암검진사업 페이지. 문서는 멀쩡하지만 글 주제(병원 선택)에 암검진이 없다 — 병원 단위
    문구로 고른 수기 문서(점검 원인 6)의 모양이라 제거한다."""
    row = _audit_row("주제 불일치", lambda r: r["url"] in CURATED_SOURCE_URLS)

    check = await verify_audit_row(row)

    assert check["curated"] is True
    assert check["reason"] == "unrelated_topic"


# ── 가드: 빈 템플릿·통계/메뉴·soft-404·죽은 링크 ───────────────────────────────


def _empty_kdca_template() -> str:
    row = _audit_row("빈 페이지", lambda r: "health.kdca.go.kr" in r["url"])
    return fixture_html(row["html"])


async def test_kdca_empty_template_served_as_200_without_redirect_is_rejected():
    """KDCA는 없는 cntnts_sn에 200과 `| 국가건강정보포털 | 질병관리청`(빈 문서명)을 준다."""
    url = KDCA_VIEW.format(2480)
    fetcher = PageFetcher({url: (200, url, _empty_kdca_template())})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "갑상선암", "url": url}],
        topic_terms=article_topic_terms(title="갑상선초음파 검사와 갑상선암"),
    )

    assert outcome.kept == []
    assert outcome.checks[0]["reason"] == "empty_template"
    # 템플릿 자체에는 메뉴 텍스트가 많다 — 본문 길이만으로는 잡히지 않는다.
    assert outcome.checks[0]["text_len"] >= rv.REFERENCE_MIN_BODY_CHARS


async def test_amc_empty_content_id_template_is_rejected():
    row = _audit_row("빈 페이지", lambda r: "amc.seoul.kr" in r["url"])

    check = await verify_audit_row(row)

    assert check["reason"] == "empty_template"


@pytest.mark.parametrize("fragment", ["S1T639C641", "S1T639C640"])
async def test_cancer_statistics_pages_are_rejected(fragment):
    row = _audit_row("주제 불일치", lambda r: fragment in r["url"])

    check = await verify_audit_row(row)

    assert check["reason"] == "stats_or_menu_page"


async def test_cancer_statistics_breadcrumb_is_rejected_on_any_path():
    """메뉴 코드 모양이 아닌 주소라도 '통계로 보는 암' 화면이면 문서 근거가 아니다."""
    url = "https://www.cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=9999"
    fetcher = PageFetcher(
        {url: (200, url, page_html("홈 >통계로 보는 암>발생률>대장암 발생 현황", document_body("대장암")))}
    )

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "대장암", "url": url}],
        topic_terms=article_topic_terms(title="대장암 검진 시기와 방법"),
    )

    assert outcome.checks[0]["reason"] == "stats_or_menu_page"


async def test_guessed_cancer_menu_code_outside_the_list_is_rejected_even_when_on_topic():
    url = "https://www.cancer.go.kr/lay1/S1T211C999/contents.do"
    fetcher = PageFetcher({url: (200, url, page_html("홈 >내가 알고 싶은 암>암의 종류>치핵", document_body("치핵")))})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert outcome.checks[0]["reason"] == "stats_or_menu_page"


async def test_curated_cancer_screening_menu_page_is_allowed_for_a_screening_article():
    url = "https://www.cancer.go.kr/lay1/S1T261C262/contents.do"
    fetcher = PageFetcher({url: (200, url, page_html("홈 >암예방과 검진>검진>국가암검진 사업", document_body("국가암검진")))})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "국가암검진사업", "url": url}],
        topic_terms=article_topic_terms(title="대장암 검진 시기와 방법"),
    )

    assert outcome.checks[0]["reason"] == "curated_verified"


@pytest.mark.parametrize(
    "final_url",
    [
        "https://health.kdca.go.kr/healthinfo/biz/health/main/mainPage/main.do",
        "https://health.kdca.go.kr/healthinfo/",
        "https://health.kdca.go.kr/",
    ],
)
async def test_same_domain_redirect_to_home_is_a_soft_404(final_url):
    url = KDCA_VIEW.format(7777)
    # 실제 문서처럼 보이는 본문이어도 홈으로 이동했다면 없는 문서다.
    fetcher = PageFetcher({url: (200, final_url, page_html("치핵 | 국가건강정보포털 | 질병관리청", document_body("치핵")))})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert outcome.checks[0]["reason"] == "soft_404_redirect"


@pytest.mark.parametrize("status", [404, 410])
@pytest.mark.parametrize("url", [KDCA_VIEW.format(8888), CURATED_HEMORRHOID_KDCA])
async def test_dead_links_are_removed_even_from_the_curated_list(status, url):
    fetcher = PageFetcher({url: (status, url, "<html><title>알림메세지</title></html>")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert outcome.checks[0]["reason"] == "dead_link"
    assert outcome.kept == []


async def test_redirect_outside_the_whitelist_is_removed():
    url = KDCA_VIEW.format(9999)
    fetcher = PageFetcher({url: (200, "https://example.com/landing", page_html("치핵", document_body("치핵")))})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert outcome.checks[0]["reason"] == "redirect_outside_whitelist"


# ── 가드: 판단 불가 + 목록 밖 → 제거, 판단 불가 + 목록 → 유지 ──────────────────────


@pytest.mark.parametrize(
    "page",
    [
        (403, None, "<html><title>Access Denied</title></html>"),
        (429, None, ""),
        (203, None, "<html><title>pubmed.ncbi.nlm.nih.gov</title></html>"),
        (200, None, page_html("Hemorrhoids - MedlinePlus", document_body("Hemorrhoids"))),
        (200, None, page_html("국가건강정보포털 | 질병관리청", document_body("안내"))),
        TimeoutError("slow"),
    ],
    ids=["403", "429", "203", "english-only", "institution-only", "timeout"],
)
async def test_undeterminable_outside_list_is_removed_and_curated_is_kept(page):
    outside = "https://medlineplus.gov/hemorrhoids.html"
    pages = {}
    for url in (outside, CURATED_HEMORRHOID_AMC):
        pages[url] = page if isinstance(page, BaseException) else (page[0], url, page[2])
    fetcher = PageFetcher(pages)

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치질", "url": outside}, {"title": "치질", "url": CURATED_HEMORRHOID_AMC}],
        topic_terms=HEMORRHOID_TOPIC,
    )
    by_url = {check["url"]: check for check in outcome.checks}

    assert by_url[outside]["verdict"] == "fail"
    assert by_url[outside]["reason"] == "undeterminable"
    assert by_url[CURATED_HEMORRHOID_AMC]["verdict"] == "pass"
    assert [ref["url"] for ref in outcome.kept] == [CURATED_HEMORRHOID_AMC]


async def test_curated_url_off_the_article_topic_is_removed():
    """수기 목록도 카탈로그 키워드·확인된 제목으로 주제를 본다(무조건 통과가 아니다)."""
    outcome = await ReferenceVerifier(OfflineReferenceFetcher(), domain_spacing=0).verify(
        [{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}], topic_terms=KNEE_TOPIC
    )

    assert outcome.kept == []
    assert outcome.checks[0]["reason"] == "unrelated_topic"


# ── 가드: httpx의 모든 예외를 잡는다 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "exc,expected",
    [
        (httpx.TooManyRedirects("loop"), "too_many_redirects"),
        (httpx.RemoteProtocolError("peer closed"), "protocol"),
        (httpx.ConnectTimeout("connect"), "timeout"),
        (httpx.ReadTimeout("read"), "timeout"),
        (httpx.ConnectError("refused"), "connect"),
        (httpx.ReadError("reset"), "network"),
        (httpx.DecodingError("gzip"), "decode"),
        (RuntimeError("anything else"), "error"),
    ],
)
async def test_httpx_fetcher_turns_every_exception_into_an_observation(monkeypatch, exc, expected):
    def handler(_request):
        raise exc

    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(rv.httpx, "AsyncClient", client)

    result = await HttpxReferenceFetcher()(KDCA_VIEW.format(1))

    assert result.error == expected
    assert result.status is None


async def test_httpx_fetcher_follows_redirects_and_reads_the_real_page(monkeypatch):
    target = KDCA_VIEW.format(5818)
    source = "https://health.kdca.go.kr/old?cntnts_sn=5818"
    html = page_html("치핵 | 국가건강정보포털 | 질병관리청", document_body("치핵"))

    def handler(request):
        if str(request.url) == source:
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, html=html)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        rv.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    result = await HttpxReferenceFetcher()(source)

    assert (result.status, result.final_url) == (200, target)
    assert "치핵" in rv.html_page_title(result.html)


async def test_a_raising_injected_fetcher_cannot_crash_verification():
    url = KDCA_VIEW.format(2)
    fetcher = PageFetcher({url: httpx.RemoteProtocolError("boom")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert outcome.checks[0]["fetch_error"] == "protocol"
    assert outcome.kept == []


# ── 가드: 시간 제한·동시성·도메인 간격·실행당 GET 상한 ─────────────────────────


async def test_each_get_is_bounded_by_the_timeout():
    url = KDCA_VIEW.format(3)
    fetcher = PageFetcher({url: (200, url, page_html("치핵", document_body("치핵")))}, delay=5)
    started = time.monotonic()

    outcome = await ReferenceVerifier(fetcher, timeout=0.05, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC
    )

    assert time.monotonic() - started < 1
    assert outcome.checks[0]["fetch_error"] == "timeout"
    assert outcome.kept == []


async def test_total_concurrency_is_capped():
    urls = [f"https://site{index}.kdca.go.kr/doc?cntnts_sn={index}" for index in range(8)]
    fetcher = PageFetcher(
        {url: (200, url, page_html("치핵 | 기관", document_body("치핵"))) for url in urls},
        delay=0.02,
    )

    await ReferenceVerifier(fetcher, max_concurrency=2, domain_spacing=0).verify(
        [{"title": "x", "url": url} for url in urls], topic_terms=HEMORRHOID_TOPIC
    )

    assert len(fetcher.calls) == 8
    assert fetcher.max_active == 2


async def test_one_domain_gets_one_request_at_a_time_with_spacing():
    urls = [KDCA_VIEW.format(100 + index) for index in range(3)]
    fetcher = PageFetcher(
        {url: (200, url, page_html("치핵 | 국가건강정보포털", document_body("치핵"))) for url in urls},
        delay=0.01,
    )
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await ReferenceVerifier(
        fetcher, max_concurrency=4, domain_spacing=0.5, sleep=record_sleep
    ).verify([{"title": "x", "url": url} for url in urls], topic_terms=HEMORRHOID_TOPIC)

    assert fetcher.max_active == 1
    assert len(sleeps) == 2 and all(0 < value <= 0.5 for value in sleeps)


async def test_gets_beyond_the_run_budget_are_deferred_not_failed():
    urls = [KDCA_VIEW.format(200 + index) for index in range(3)]
    fetcher = PageFetcher(
        {url: (200, url, page_html("치핵 | 국가건강정보포털", document_body("치핵"))) for url in urls}
    )
    verifier = ReferenceVerifier(fetcher, max_fetches=2, domain_spacing=0)

    outcome = await verifier.verify(
        [{"title": "x", "url": url} for url in urls], topic_terms=HEMORRHOID_TOPIC
    )

    assert len(fetcher.calls) == 2
    assert [ref["url"] for ref in outcome.deferred] == [urls[2]]
    assert len(outcome.checks) == 2


# ── 가드: 저장된 판정의 신선도·같은 URL ─────────────────────────────────────


def _pass_record(
    url: str,
    *,
    checked_at: datetime,
    verified_at: datetime | None = None,
    topic_terms=None,
) -> dict:
    return reference_check_record(
        url,
        verdict="pass",
        reason="page_verified",
        checked_at=checked_at,
        curated=False,
        status=200,
        final_url=url,
        page_title="치핵 | 국가건강정보포털",
        text_len=900,
        verified_at=verified_at or checked_at,
        topic_fingerprint=topic_fingerprint(
            HEMORRHOID_TOPIC if topic_terms is None else topic_terms
        ),
    )


def test_gate_requires_a_fresh_pass_for_the_same_url():
    url = KDCA_VIEW.format(300)
    references = [{"title": "치핵", "url": url}]

    fresh = [_pass_record(url, checked_at=NOW - timedelta(hours=2))]
    stale = [_pass_record(url, checked_at=NOW - REFERENCE_CHECK_MAX_AGE - timedelta(minutes=1))]
    other_url = [_pass_record(KDCA_VIEW.format(301), checked_at=NOW)]
    failed = [{**fresh[0], "verdict": "fail", "reason": "dead_link"}]

    assert reference_gate_status(references, fresh, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert not reference_gate_status(references, stale, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert not reference_gate_status(references, other_url, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert not reference_gate_status(references, failed, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert not reference_gate_status(references, None, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert reference_gate_status([], None, topic_terms=HEMORRHOID_TOPIC, now=NOW).current


def test_fingerprint_distinguishes_document_ids():
    assert reference_url_fingerprint(KDCA_VIEW.format(5818)) != reference_url_fingerprint(
        KDCA_VIEW.format(5819)
    )
    assert check_is_fresh_pass(
        _pass_record(KDCA_VIEW.format(1), checked_at=NOW),
        now=NOW,
        topic_fingerprint=topic_fingerprint(HEMORRHOID_TOPIC),
    )


async def test_fresh_stored_pass_is_reused_without_a_get():
    url = KDCA_VIEW.format(400)
    fetcher = PageFetcher()

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        previous_checks=[_pass_record(url, checked_at=NOW - timedelta(hours=3))],
        reuse_fresh_checks=True,
        now=NOW,
    )

    assert fetcher.calls == []
    assert [ref["url"] for ref in outcome.kept] == [url]


def test_gate_binds_each_pass_to_the_article_topic():
    """같은 URL이라도 다른 글 주제로 판정한 통과는 신선한 통과가 아니다(제목·brief 변경)."""
    url = KDCA_VIEW.format(310)
    references = [{"title": "치핵", "url": url}]
    judged_for_old_topic = [
        _pass_record(url, checked_at=NOW, topic_terms=article_topic_terms(title="고혈압 관리"))
    ]
    legacy_without_topic = [{**_pass_record(url, checked_at=NOW), "topic_fingerprint": None}]

    assert not reference_gate_status(
        references, judged_for_old_topic, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current
    assert not reference_gate_status(
        references, legacy_without_topic, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current
    # 순서·공백·구두점만 다른 같은 주제어는 같은 지문이다.
    assert topic_fingerprint(["치질 수술", "회복 기간"]) == topic_fingerprint(["회복기간", "치질수술!"])
    assert topic_fingerprint(["치질 수술"]) != topic_fingerprint(["치질 수술", "대장내시경"])


def test_gate_rejects_future_dated_checks_beyond_the_clock_skew():
    url = KDCA_VIEW.format(320)
    references = [{"title": "치핵", "url": url}]
    within_skew = [_pass_record(url, checked_at=NOW + REFERENCE_CHECK_CLOCK_SKEW / 2)]
    far_future = [_pass_record(url, checked_at=NOW + timedelta(days=3))]

    assert reference_gate_status(
        references, within_skew, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current
    assert not reference_gate_status(
        references, far_future, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current


@pytest.mark.parametrize(
    "references",
    [
        ["https://health.kdca.go.kr/x"],
        [{"title": "제목만"}],
        [{"title": "빈 주소", "url": "   "}],
        {"title": "목록이 아님", "url": KDCA_VIEW.format(330)},
    ],
    ids=["not-a-mapping", "no-url", "blank-url", "not-a-list"],
)
def test_gate_fails_every_malformed_reference_entry(references):
    good = KDCA_VIEW.format(331)
    stored = references if isinstance(references, dict) else [{"title": "치핵", "url": good}, *references]
    checks = [_pass_record(good, checked_at=NOW)]

    status = reference_gate_status(stored, checks, topic_terms=HEMORRHOID_TOPIC, now=NOW)

    assert not status.current
    assert status.malformed_entries == 1


async def test_outage_reuse_rejects_a_future_dated_verification():
    url = KDCA_VIEW.format(502)
    fetcher = PageFetcher({url: httpx.ConnectError("down")})
    future = NOW + timedelta(days=2)
    previous = [_pass_record(url, checked_at=NOW - timedelta(hours=30), verified_at=future)]

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        previous_checks=previous,
        reuse_fresh_checks=True,
        now=NOW,
    )

    assert outcome.checks[0]["reason"] != "reused_previous_pass"


async def test_outage_reuse_never_crosses_article_topics():
    url = KDCA_VIEW.format(503)
    fetcher = PageFetcher({url: httpx.ConnectError("down")})
    previous = [
        _pass_record(
            url,
            checked_at=NOW - timedelta(hours=30),
            topic_terms=article_topic_terms(title="고혈압 관리"),
        )
    ]

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        previous_checks=previous,
        reuse_fresh_checks=True,
        now=NOW,
    )

    assert outcome.checks[0]["reason"] != "reused_previous_pass"


# ── 가드: 발행 직전 일시 장애는 제거가 아니라 미룸 ──────────────────────────────


@pytest.mark.parametrize(
    "page",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("reset"),
        (503, None, "<html><title>점검</title></html>"),
        (429, None, "<html><title>too many</title></html>"),
    ],
    ids=["connect", "timeout", "protocol", "http-503", "http-429"],
)
async def test_publish_time_transient_failure_defers_instead_of_removing(page):
    url = KDCA_VIEW.format(800)
    fetcher = PageFetcher({url: page if isinstance(page, BaseException) else (page[0], url, page[2])})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        defer_transient=True,
        now=NOW,
    )

    assert outcome.kept == [] and outcome.dropped == []
    assert [ref["url"] for ref in outcome.deferred] == [url]
    assert outcome.checks[0]["verdict"] == "deferred"
    assert outcome.checks[0]["reason"] == "site_unreachable"
    assert outcome.failed_checks() == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [(404, "dead_link"), (410, "dead_link"), (403, "undeterminable"), (400, "http_error")],
)
async def test_publish_time_definitive_results_still_remove(status, reason):
    url = KDCA_VIEW.format(801)
    fetcher = PageFetcher({url: (status, url, "")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        defer_transient=True,
        now=NOW,
    )

    assert outcome.deferred == []
    assert outcome.checks[0]["verdict"] == "fail"
    assert outcome.checks[0]["reason"] == reason


async def test_generation_time_transient_failure_still_removes_outside_urls():
    """수용 기준 2: 생성 단계의 판단 불가 + 목록 밖 → 제거(미루지 않는다)."""
    url = KDCA_VIEW.format(802)
    fetcher = PageFetcher({url: httpx.ConnectError("down")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC, now=NOW
    )

    assert outcome.deferred == []
    assert outcome.checks[0]["reason"] == "undeterminable"


async def test_curated_reference_is_judged_by_catalog_when_the_run_budget_is_spent():
    curated = next(url for url in CURATED_SOURCE_URLS if "cntnts_sn=5818" in url)
    fetcher = PageFetcher()

    outcome = await ReferenceVerifier(fetcher, max_fetches=0, domain_spacing=0).verify(
        [{"title": "치핵", "url": curated}], topic_terms=HEMORRHOID_TOPIC, defer_transient=True
    )

    assert fetcher.calls == []
    assert outcome.deferred == []
    assert outcome.checks[0]["reason"] == "curated_unreachable"


# ── 가드: 기관 사이트 장애 폴백 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "page",
    [httpx.ConnectError("down"), (503, None, "<html><title>점검</title></html>")],
    ids=["connect-error", "http-503"],
)
async def test_domain_outage_reuses_the_previous_pass_within_the_window(page):
    url = KDCA_VIEW.format(500)
    fetcher = PageFetcher({url: page if isinstance(page, BaseException) else (page[0], url, page[2])})
    previous = [
        _pass_record(url, checked_at=NOW - timedelta(days=2), verified_at=NOW - timedelta(days=2))
    ]

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        previous_checks=previous,
        reuse_fresh_checks=True,
        now=NOW,
    )

    check = outcome.checks[0]
    assert check["verdict"] == "pass"
    assert check["reason"] == "reused_previous_pass"
    assert check["verified_at"] == previous[0]["verified_at"]
    assert reference_gate_status(
        [{"url": url}], outcome.checks, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current


async def test_outage_reuse_is_bounded_by_the_last_real_verification():
    url = KDCA_VIEW.format(501)
    fetcher = PageFetcher({url: httpx.ConnectError("down")})
    too_old = NOW - REFERENCE_CHECK_REUSE_WINDOW - timedelta(hours=1)
    previous = [_pass_record(url, checked_at=NOW - timedelta(hours=30), verified_at=too_old)]

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}],
        topic_terms=HEMORRHOID_TOPIC,
        previous_checks=previous,
        reuse_fresh_checks=True,
        now=NOW,
    )

    assert outcome.checks[0]["verdict"] == "fail"
    assert outcome.checks[0]["reason"] == "undeterminable"


async def test_a_down_domain_is_not_hammered_for_every_reference():
    urls = [KDCA_VIEW.format(600 + index) for index in range(3)]
    fetcher = PageFetcher({url: httpx.ConnectTimeout("down") for url in urls})

    await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "x", "url": url} for url in urls], topic_terms=HEMORRHOID_TOPIC
    )

    assert len(fetcher.calls) == 1


def test_judge_ignores_the_model_label_entirely():
    """판정 함수는 라벨을 입력으로 받지도 않는다 — 실제 GET 관측과 글 주제뿐이다."""
    import inspect

    assert "label" not in inspect.signature(judge_fetched_page).parameters
    assert "title" not in inspect.signature(judge_fetched_page).parameters


async def test_verifier_state_survives_across_event_loops():
    """07:45·08:00 워커는 글마다 `_run_async`로 루프에 들어간다 — 검증기는 재사용된다."""
    url = KDCA_VIEW.format(700)
    fetcher = PageFetcher()
    fetcher.add_document(url, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")
    verifier = ReferenceVerifier(fetcher, domain_spacing=0)

    def run_in_a_fresh_loop() -> None:
        asyncio.run(
            verifier.verify([{"title": "치핵", "url": url}], topic_terms=HEMORRHOID_TOPIC)
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        for _ in range(2):
            pool.submit(run_in_a_fresh_loop).result()

    assert fetcher.calls == [url]  # 같은 실행 안에서는 한 번만 GET한다


def test_fetch_result_defaults_are_an_unreachable_observation():
    assert FetchResult(url="x").status is None


# ── 가드: 제목만 맞고 본문이 다른 문서는 통과가 아니다 ──────────────────────────────


def _title_only_page(title: str, body: str) -> str:
    """제목은 <title>에만 두고 본문에는 다시 쓰지 않는다(본문 토큰 검사를 따로 본다)."""
    return f"<html><head><title>{title}</title></head><body><main><p>{body}</p></main></body></html>"


async def test_matching_title_over_a_different_document_body_is_not_a_pass():
    url = KDCA_VIEW.format(99111)
    title = "치핵 | 국가건강정보포털 | 질병관리청"
    fetcher = PageFetcher(
        {
            url: (200, url, _title_only_page(title, document_body("악구충증"))),
            KDCA_VIEW.format(99112): (
                200,
                KDCA_VIEW.format(99112),
                _title_only_page(title, document_body("치핵")),
            ),
        }
    )

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": url}, {"title": "치핵", "url": KDCA_VIEW.format(99112)}],
        topic_terms=HEMORRHOID_TOPIC,
    )

    reasons = {check["url"]: check["reason"] for check in outcome.checks}
    assert reasons[url] == "unrelated_topic"  # 제목은 치핵, 본문은 다른 질환
    assert reasons[KDCA_VIEW.format(99112)] == "page_verified"  # 같은 제목·본문도 치핵이면 통과
    assert [ref["url"] for ref in outcome.kept] == [KDCA_VIEW.format(99112)]


# ── 가드: 깨진 포트 주소는 거절하고 크래시하지 않는다 ──────────────────────────────


MALFORMED_PORT_URL = (
    "https://health.kdca.go.kr:bad/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn=5818"
)


async def test_malformed_port_url_is_rejected_without_crashing_or_fetching():
    from app.services.content_engine import _normalize_references
    from app.utils.authority_sources import (
        is_citable_reference_url,
        is_whitelisted_url,
        normalize_reference_url,
        reference_exclusion_reason,
    )

    assert not is_whitelisted_url(MALFORMED_PORT_URL)
    assert not is_citable_reference_url(MALFORMED_PORT_URL)
    assert normalize_reference_url(MALFORMED_PORT_URL)  # ValueError 없이 비교 키를 낸다
    assert reference_exclusion_reason(MALFORMED_PORT_URL) is None
    assert _normalize_references([{"title": "치핵", "url": MALFORMED_PORT_URL}]) == []

    status = reference_gate_status(
        [{"title": "치핵", "url": MALFORMED_PORT_URL}], None, topic_terms=HEMORRHOID_TOPIC
    )
    assert not status.current

    fetcher = PageFetcher()
    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "치핵", "url": MALFORMED_PORT_URL}], topic_terms=HEMORRHOID_TOPIC
    )
    assert outcome.kept == []
    assert outcome.checks[0]["reason"] == "not_citable"
    assert fetcher.calls == []
