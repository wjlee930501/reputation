"""김실장 2차 점검(2026-09-29)의 제외 목록과 수기 목록 추가분.

- 제외 목록(`REFERENCE_URL_EXCLUSIONS`): 사람이 실제 GET으로 확인해 근거로 쓸 수 없다고 판정한
  주소. 수기 목록에서도, 모델 참고자료로도 쓰지 않는다. 정규화한 주소로 비교한다.
- 수기 목록 추가분: 같은 점검에서 실제 GET으로 확인한 문서. 오프라인 fixture(실제 HTML)로 같은
  검증을 통과함을 고정한다.

네트워크는 쓰지 않는다 — fixture·가짜 fetcher만 쓴다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import reference_verification as rv
from app.services.reference_verification import (
    REASON_EXCLUDED_SOURCE,
    REASON_UNDETERMINABLE,
    ReferenceVerifier,
    article_topic_terms,
    reference_check_record,
    reference_gate_status,
    topic_fingerprint,
)
from app.utils import authority_sources
from app.utils.authority_sources import (
    CURATED_MEDICAL_SOURCE_PAGES,
    CURATED_SOURCE_URLS,
    REFERENCE_URL_EXCLUSIONS,
    normalize_reference_url,
    reference_exclusion_reason,
    select_curated_authority_sources,
)
from tests.reference_audit_fixture import (
    AuditFixtureFetcher,
    load_review_rows,
    review_row_topic,
    verify_review_row,
)
from tests.reference_fetch_doubles import PageFetcher

_EXCLUDED = load_review_rows("exclusions")
_SEED = load_review_rows("catalog_seed")
# 2026-09-29 보류 재생 후속(PR #177 리뷰): 보류를 만든 주제군과 MedlinePlus 오탐 2건의 문서
# 16건(/workspace/ref-url-guard/r2/vetting). 기존 15건 seed와 섞지 않는다.
_SEED_R3 = load_review_rows("catalog_seed_r3")
KDCA_6263 = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn=6263"
)
KDCA_2351 = KDCA_6263.replace("6263", "2351")
CANCER_STOMACH_HUB = "https://www.cancer.go.kr/lay1/S1T211C213/contents.do"
CANCER_COLON_3797 = "https://cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797"
MEDLINE_CT = "https://medlineplus.gov/ctscans.html"
MEDLINE_OSTEOPOROSIS = "https://medlineplus.gov/osteoporosis.html"


def _id(row: dict) -> str:
    return row["url"].rsplit("/", 2)[-2] if "cancer.go.kr" in row["url"] else row["url"][-14:]


# ── 제외 목록 ────────────────────────────────────────────────────────────────


def test_exclusion_list_is_the_reviewed_six_with_a_reason_each():
    assert [entry["url"] for entry in REFERENCE_URL_EXCLUSIONS] == [row["url"] for row in _EXCLUDED]
    assert all(str(entry["reason"]).strip() for entry in REFERENCE_URL_EXCLUSIONS)
    assert all(str(entry["topic"]).strip() for entry in REFERENCE_URL_EXCLUSIONS)


@pytest.mark.parametrize("row", _EXCLUDED, ids=[_id(row) for row in _EXCLUDED])
async def test_exclusion_list_row_is_blocked(row):
    """실제 응답 fixture로 — 제외 목록의 주소는 GET도, 직전 통과 재사용도 없이 떨어진다."""
    check, calls = await verify_review_row(row, review_row_topic(row))

    assert check["verdict"] == "fail", check
    assert check["reason"] == REASON_EXCLUDED_SOURCE
    assert calls == []


@pytest.mark.parametrize("url,topic", [(KDCA_6263, "소화불량"), (KDCA_2351, "당뇨병 합병증")])
async def test_excluded_url_is_blocked_even_when_the_site_serves_a_real_document(url, topic):
    """기관이 언젠가 이 주소에 실제 같은 문서를 돌려줘도 사람의 판정이 이긴다(제외 목록 끄면 통과한다)."""
    fetcher = PageFetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    terms = article_topic_terms(title=f"{topic} 진료 안내")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": topic, "url": url}], topic_terms=terms
    )

    assert outcome.kept == []
    assert outcome.checks[0]["reason"] == REASON_EXCLUDED_SOURCE
    assert fetcher.calls == []


async def test_a_stored_pass_for_an_excluded_url_is_not_reused_or_accepted_by_the_gate():
    terms = article_topic_terms(title="소화불량 진료 안내")
    stored = reference_check_record(
        KDCA_6263,
        verdict="pass",
        reason="page_verified",
        checked_at=datetime.now(timezone.utc),
        curated=False,
        status=200,
        verified_at=datetime.now(timezone.utc),
        topic_fingerprint=topic_fingerprint(terms),
    )
    references = [{"title": "소화불량", "url": KDCA_6263}]

    assert not reference_gate_status(references, [stored], topic_terms=terms).current
    outcome = await ReferenceVerifier(PageFetcher(), domain_spacing=0).verify(
        references, topic_terms=terms, previous_checks=[stored], reuse_fresh_checks=True
    )
    assert outcome.checks[0]["reason"] == REASON_EXCLUDED_SOURCE


@pytest.mark.parametrize(
    "variant",
    [
        KDCA_6263.replace("https://", "http://"),
        KDCA_6263.replace("https://health.", "https://www.health."),
        KDCA_6263 + "#top",
        KDCA_6263.replace("health.kdca.go.kr", "HEALTH.KDCA.GO.KR"),
        CANCER_STOMACH_HUB.replace("https://www.", "https://"),
        CANCER_STOMACH_HUB.replace("https://www.", "http://"),
        CANCER_STOMACH_HUB + "/",
    ],
)
def test_exclusion_matches_by_normalized_url(variant):
    assert reference_exclusion_reason(variant) is not None


@pytest.mark.parametrize(
    "other",
    [
        KDCA_6263.replace("6263", "6264"),
        KDCA_6263.replace("cntnts_sn=6263", "cntnts_sn=62630"),
        CANCER_STOMACH_HUB.replace("S1T211C213", "S1T211C215"),
        "https://health.kdca.go.kr/",
    ],
)
def test_other_documents_are_not_excluded(other):
    assert reference_exclusion_reason(other) is None


def test_normalization_ignores_query_order_scheme_www_and_trailing_slash():
    assert normalize_reference_url("https://www.example.go.kr/a/b.do?x=1&y=2") == (
        normalize_reference_url("http://example.go.kr/a/b.do/?y=2&x=1#frag")
    )
    # 문서를 가르는 값은 지우지 않는다.
    assert normalize_reference_url("https://example.go.kr/a.do?x=1") != normalize_reference_url(
        "https://example.go.kr/a.do?x=2"
    )


def test_no_catalog_entry_is_excluded():
    excluded = [
        str(entry["url"])
        for entry in CURATED_MEDICAL_SOURCE_PAGES
        if reference_exclusion_reason(entry["url"]) is not None
    ]
    assert excluded == []


def test_catalog_selection_skips_an_excluded_entry(monkeypatch):
    """누가 제외 주소를 수기 목록에 다시 넣어도(표기가 달라도) 후보로 나가지 않는다."""
    monkeypatch.setattr(
        authority_sources,
        "CURATED_MEDICAL_SOURCE_PAGES",
        (
            *CURATED_MEDICAL_SOURCE_PAGES,
            {
                "keywords": ("소화불량",),
                "title": "질병관리청 국가건강정보포털 — 소화불량",
                "url": KDCA_6263.replace("https://", "http://"),
            },
        ),
    )

    assert select_curated_authority_sources("소화불량 진료 안내") == []


# ── 수기 목록 추가분 ──────────────────────────────────────────────────────────


def test_seed_additions_are_in_the_catalog_and_not_excluded():
    assert len(_SEED) == 15
    for row in _SEED:
        assert row["url"] in CURATED_SOURCE_URLS, row["url"]
        assert reference_exclusion_reason(row["url"]) is None
        assert row["status"] == 200 and row["final_url"] == row["url"]


@pytest.mark.parametrize("row", _SEED, ids=[row["url"][-22:] for row in _SEED])
async def test_catalog_seed_row_passes_verification_offline(row):
    """실제 응답 HTML로 — 열어 본 문서가 빈 템플릿·soft-404·메뉴가 아니고, 글 주제와 맞는다."""
    check, calls = await verify_review_row(row, review_row_topic(row))

    assert calls == [row["url"]]  # 실제로 열어 본 판정이다(접속 불가 폴백이 아니다)
    assert check["verdict"] == "pass", check
    assert check["reason"] == "curated_verified", check


@pytest.mark.parametrize(
    "title",
    [
        "도화동 어깨 통증 진료 가능한 병원은 어디인가요?",
        "마산 혈액검사 가능한 병원 — 검사 항목과 진료 흐름 안내",
        "대구 동구 소화기내과 진료비, 검사 종류에 따라 얼마나 다를까요",
    ],
)
def test_seed_keywords_stay_narrow(title):
    """일반어(통증·검사·진료비)로는 추가분이 붙지 않는다 — 문서 주제어가 있어야 한다."""
    seed_urls = {row["url"] for row in _SEED}
    picked = {source["url"] for source in select_curated_authority_sources(title, limit=50)}
    assert not (picked & seed_urls)


@pytest.mark.parametrize(
    "url",
    [CANCER_COLON_3797, CANCER_COLON_3797.replace("https://", "https://www.")],
    ids=["no-www", "www"],
)
async def test_colon_cancer_3797_is_excluded_without_a_fetch(url):
    """1차 점검에서 '정상'이었던 대장암 요약 문서(3797)는 2차 점검이 제외했다 — 공개 글이 쓴
    www 표기도 같은 항목으로 막히고, 기관이 실제 문서를 돌려줘도 GET하지 않는다."""
    assert CANCER_COLON_3797 not in CURATED_SOURCE_URLS
    assert reference_exclusion_reason(url) is not None
    fetcher = PageFetcher()
    fetcher.add_document(url, "대장암 | 국가암정보센터", topic="대장암")
    terms = article_topic_terms(title="대장암 조기검진 시기·증상·대장내시경")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "국가암정보센터 — 대장암", "url": url}], topic_terms=terms
    )

    assert outcome.kept == []
    assert outcome.checks[0]["verdict"] == "fail"
    assert outcome.checks[0]["reason"] == REASON_EXCLUDED_SOURCE
    assert fetcher.calls == []


def test_colon_cancer_selection_keeps_the_screening_program_document_only():
    urls = [s["url"] for s in select_curated_authority_sources("대장암 검진 시기", limit=50)]

    assert "https://www.cancer.go.kr/lay1/S1T261C262/contents.do" in urls
    assert not any("cancer_seq=3797" in url for url in urls)


# ── 수기 목록 추가분(2026-09-29 보류 재생 후속) ───────────────────────────────


def test_r3_seed_additions_are_in_the_catalog_and_not_excluded():
    assert len(_SEED_R3) == 16
    for row in _SEED_R3:
        assert row["url"] in CURATED_SOURCE_URLS, row["url"]
        assert reference_exclusion_reason(row["url"]) is None
        assert row["status"] == 200 and row["final_url"] == row["url"]


@pytest.mark.parametrize("row", _SEED_R3, ids=[row["url"][-22:] for row in _SEED_R3])
async def test_catalog_seed_r3_row_passes_verification_offline(row):
    """실제 응답 HTML로 — 열어 본 문서가 빈 템플릿·soft-404·메뉴가 아니고, 글 주제와 맞는다."""
    check, calls = await verify_review_row(row, review_row_topic(row))

    assert calls == [row["url"]]  # 실제로 열어 본 판정이다(접속 불가 폴백이 아니다)
    assert check["verdict"] == "pass", check
    assert check["reason"] == "curated_verified", check


@pytest.mark.parametrize(
    "url,title",
    [
        # cbdafc4e — 목록 밖일 때 영문 제목이라 undeterminable로 빠져 보류됐다.
        (MEDLINE_CT, "대구 동구 CT 검사, 신기한속내과연합의원에서 가능합니다"),
        # 42ef2b13 — 같은 이유로 골다공증 문서가 빠졌다.
        (MEDLINE_OSTEOPOROSIS, "마산 골밀도검사, 병원 방문 전 내과에서 먼저 확인할 것들"),
    ],
    ids=["cbdafc4e-ct", "42ef2b13-bone-density"],
)
async def test_english_medlineplus_document_is_kept_once_curated(monkeypatch, url, title):
    rows = [row for row in _SEED_R3 if row["url"] == url]
    terms = article_topic_terms(title=title)
    reference = [{"title": "MedlinePlus", "url": url}]

    fetcher = AuditFixtureFetcher(rows)
    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(reference, topic_terms=terms)

    assert fetcher.calls == [url]
    assert [ref["url"] for ref in outcome.kept] == [url]
    assert outcome.checks[0]["reason"] == "curated_verified", outcome.checks[0]

    # 같은 fixture로 목록에서 빼면 종전처럼 판정 불가로 제거된다 — 목록 항목이 유일한 연결 고리다.
    monkeypatch.setattr(rv, "CURATED_SOURCE_URLS", CURATED_SOURCE_URLS - {url})
    monkeypatch.setattr(
        rv,
        "CURATED_MEDICAL_SOURCE_PAGES",
        tuple(entry for entry in CURATED_MEDICAL_SOURCE_PAGES if entry["url"] != url),
    )
    outside = await ReferenceVerifier(AuditFixtureFetcher(rows), domain_spacing=0).verify(
        reference, topic_terms=terms
    )
    assert outside.kept == []
    assert outside.checks[0]["reason"] == REASON_UNDETERMINABLE, outside.checks[0]
