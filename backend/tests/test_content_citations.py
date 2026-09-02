"""인용 귀속 — AI 답변이 인용한 URL을 우리 글·허브 페이지에 매칭한다.

여기서 고정하는 사실은 두 가지다.

1. 자기 도메인이 없는 병원(플랫폼 경로형·서브도메인형)도 owned로 집계된다.
   이전에는 `_owned_source_roots`가 이 두 형태를 몰라 허브가 인용돼도 항상 0이었다.
2. 호스트는 **완전 일치**만 인정한다. `reputation.motionlabs.kr.evil.com`처럼
   우리 호스트를 접두사로 갖는 유사 도메인이 자사 인용으로 계상되면
   "AI가 우리 글을 읽었다"는 리포트 문장 자체가 거짓이 된다.
"""
import uuid
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from app.core.config import settings
from app.services.content_citations import (
    build_citation_match,
    hospital_surface_roots,
    match_cited_content,
    match_cited_hub_pages,
    normalize_cited_url,
    platform_public_base_url,
    platform_subdomain_host,
)
from app.services.report_engine import _matches_owned_source, _owned_source_roots

PLATFORM_HOST = "reputation.motionlabs.kr"
CONTENT_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_CONTENT_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")


@pytest.fixture(autouse=True)
def _platform_base(monkeypatch):
    monkeypatch.setattr(settings, "SITE_BASE_URL", f"https://{PLATFORM_HOST}", raising=False)


def _hospital(*, slug: str = "jangpyeonhan", aeo_domain: str | None = None):
    return SimpleNamespace(
        slug=slug,
        aeo_domain=aeo_domain,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
    )


def _content(content_id=CONTENT_ID, title="치질 수술 FAQ", content_type="FAQ"):
    return SimpleNamespace(id=content_id, title=title, content_type=content_type)


# ── URL 정규화 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_host,expected_path",
    [
        ("https://www.example.com/a/b/", "example.com", "/a/b"),
        ("http://Example.COM:8080/a?x=1#frag", "example.com", "/a"),
        ("example.com/a", "example.com", "/a"),
        ("//example.com/a", "example.com", "/a"),
        ("https://example.com", "example.com", "/"),
        ("https://example.com//a//b", "example.com", "/a/b"),
        (f"https://{PLATFORM_HOST}/jangpyeonhan/treatments/{quote('치질수술')}",
         PLATFORM_HOST, "/jangpyeonhan/treatments/치질수술"),
    ],
)
def test_normalize_cited_url(raw, expected_host, expected_path):
    normalized = normalize_cited_url(raw)

    assert normalized is not None
    assert (normalized.host, normalized.path) == (expected_host, expected_path)


@pytest.mark.parametrize("raw", [None, "", "   ", 42, "https://"])
def test_normalize_cited_url_rejects_unusable_values(raw):
    assert normalize_cited_url(raw) is None


# ── 표면 루트 ─────────────────────────────────────────────────────


def test_platform_subdomain_and_base_url_derive_from_site_base_url():
    assert platform_subdomain_host("jangpyeonhan") == f"jangpyeonhan.{PLATFORM_HOST}"
    assert platform_public_base_url("jangpyeonhan") == f"https://jangpyeonhan.{PLATFORM_HOST}/"


@pytest.mark.parametrize("slug", [None, "", "www", "a.b"])
def test_platform_subdomain_rejects_non_slug_labels(slug):
    assert platform_subdomain_host(slug) is None


def test_surface_roots_cover_path_subdomain_and_custom_domain():
    roots = hospital_surface_roots(_hospital(aeo_domain="clinic.example.kr"))

    assert {(root.host, root.base_path) for root in roots} == {
        ("clinic.example.kr", ""),
        (f"jangpyeonhan.{PLATFORM_HOST}", ""),
        (PLATFORM_HOST, "/jangpyeonhan"),
    }


# ── 글 단위 매칭 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cited",
    [
        f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}",
        f"https://jangpyeonhan.{PLATFORM_HOST}/contents/{CONTENT_ID}",
        f"https://clinic.example.kr/contents/{CONTENT_ID}",
        f"https://www.clinic.example.kr/contents/{CONTENT_ID}/?utm_source=chatgpt#top",
    ],
)
def test_match_cited_content_matches_every_public_surface(cited):
    matched = match_cited_content(
        [cited], _hospital(aeo_domain="clinic.example.kr"), [_content()]
    )

    assert matched == {str(CONTENT_ID): [cited]}


def test_match_cited_content_is_case_insensitive_on_the_identifier():
    cited = f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{str(CONTENT_ID).upper()}"

    assert match_cited_content([cited], _hospital(), [_content()]) == {str(CONTENT_ID): [cited]}


@pytest.mark.parametrize(
    "cited",
    [
        # 유사 호스트: 우리 호스트를 접두사로 갖지만 남의 도메인이다.
        f"https://{PLATFORM_HOST}.evil.com/jangpyeonhan/contents/{CONTENT_ID}",
        f"https://evil-{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}",
        # 같은 플랫폼 호스트지만 다른 병원 경로다.
        f"https://{PLATFORM_HOST}/otherclinic/contents/{CONTENT_ID}",
        # slug가 경로 접두사로만 겹치는 경우.
        f"https://{PLATFORM_HOST}/jangpyeonhan-2/contents/{CONTENT_ID}",
        "https://blog.naver.com/somebody/12345",
    ],
)
def test_match_cited_content_rejects_lookalike_and_foreign_urls(cited):
    match = build_citation_match([cited], _hospital(), [_content()])

    assert match.contents == {}
    assert match.hub_pages == {}
    assert match.unresolved == []
    assert match.has_owned is False


def test_unknown_content_id_on_our_surface_is_owned_but_unresolved():
    cited = f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{OTHER_CONTENT_ID}"

    match = build_citation_match([cited], _hospital(), [_content()])

    assert match.contents == {}
    assert match.unresolved == [cited]
    assert match.has_owned is True


def test_duplicate_urls_are_counted_once_per_normalized_target():
    urls = [
        f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}",
        f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}/",
        f"http://www.{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}?utm=1",
    ]

    matched = match_cited_content(urls, _hospital(), [_content()])

    assert matched == {str(CONTENT_ID): [urls[0]]}


# ── 허브 페이지 매칭 ──────────────────────────────────────────────


def test_hub_pages_bucket_covers_home_doctor_treatments_and_visit():
    korean = quote("치질수술")
    urls = [
        f"https://{PLATFORM_HOST}/jangpyeonhan",
        f"https://{PLATFORM_HOST}/jangpyeonhan/doctor",
        f"https://{PLATFORM_HOST}/jangpyeonhan/treatments/{korean}",
        f"https://jangpyeonhan.{PLATFORM_HOST}/visit",
    ]

    hub = match_cited_hub_pages(urls, _hospital())

    assert set(hub) == {"home", "doctor", "treatments/치질수술", "visit"}


def test_article_urls_never_land_in_the_hub_bucket():
    cited = f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}"

    match = build_citation_match([cited], _hospital(), [_content()])

    assert match.hub_pages == {}
    assert set(match.contents) == {str(CONTENT_ID)}


def test_hospital_without_slug_or_domain_matches_nothing():
    match = build_citation_match(
        [f"https://{PLATFORM_HOST}/jangpyeonhan"], SimpleNamespace(), [_content()]
    )

    assert match.has_owned is False


# ── owned roots (report_engine) ───────────────────────────────────


def test_owned_roots_include_platform_path_and_subdomain_forms():
    roots = _owned_source_roots(_hospital())

    assert (PLATFORM_HOST, "/jangpyeonhan") in roots
    assert (f"jangpyeonhan.{PLATFORM_HOST}", "") in roots


@pytest.mark.parametrize(
    "url,owned",
    [
        (f"https://{PLATFORM_HOST}/jangpyeonhan", True),
        (f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}", True),
        (f"https://jangpyeonhan.{PLATFORM_HOST}/contents/{CONTENT_ID}", True),
        (f"https://{PLATFORM_HOST}/otherclinic/contents/{CONTENT_ID}", False),
        (f"https://{PLATFORM_HOST}.evil.com/jangpyeonhan", False),
        (f"https://{PLATFORM_HOST}/jangpyeonhan-2", False),
    ],
)
def test_platform_served_hospital_is_no_longer_structurally_owned_zero(url, owned):
    roots = _owned_source_roots(_hospital())

    assert _matches_owned_source(url, roots) is owned


def test_a_custom_domain_equal_to_the_platform_host_never_claims_other_hospitals():
    """`aeo_domain`이 플랫폼 기본 호스트와 같으면 그 호스트 전체가 이 병원 것이 됐다.

    (플랫폼 호스트, "") 루트가 목록 맨 앞에 서서 `_relative_path`가 먼저 걸리므로,
    다른 병원의 `/{다른 slug}/contents/...` 인용까지 이 병원 글로 귀속됐다.
    """
    hospital = _hospital(slug="jangpyeonhan", aeo_domain=PLATFORM_HOST)

    roots = hospital_surface_roots(hospital)

    assert (PLATFORM_HOST, "") not in [(root.host, root.base_path) for root in roots]
    assert (PLATFORM_HOST, "/jangpyeonhan") in [(root.host, root.base_path) for root in roots]

    match = build_citation_match(
        [f"https://{PLATFORM_HOST}/other-clinic/contents/{OTHER_CONTENT_ID}"],
        hospital,
        [SimpleNamespace(id=OTHER_CONTENT_ID, title="남의 병원 글")],
    )

    assert not match.has_owned


def test_a_custom_domain_equal_to_the_platform_host_still_matches_its_own_path():
    hospital = _hospital(slug="jangpyeonhan", aeo_domain=PLATFORM_HOST)

    contents = match_cited_content(
        [f"https://{PLATFORM_HOST}/jangpyeonhan/contents/{CONTENT_ID}"],
        hospital,
        [SimpleNamespace(id=CONTENT_ID, title="우리 글")],
    )

    assert list(contents) == [str(CONTENT_ID).lower()]
