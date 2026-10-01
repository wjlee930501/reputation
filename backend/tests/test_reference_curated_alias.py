"""수기 목록 문서의 별칭·리다이렉트도 수기 목록 문서다(PR #177 리뷰 3차 F1 이후).

같은 문서를 가리키는 주소 표기(`&utm_source=x`·`:443`·`cntnts_sn=03796`·MedlinePlus `?from=x`)가
목록 밖 URL로 판정되면, 제목이 그 문서의 주제어를 품은 진료비 글("요통 도수치료 비용")에서 실제
GET으로 통과해 남았다. 발행 전 진료비·병원 선택 글의 수기 목록 문서 금지(2026-09-29 실장 결정)를
표기만 바꿔 비껴 간 것이다. 그래서 목록 항목별 판정(`authority_sources._matching_documents`)으로
본다 — 위치가 같고 항목 URL 자신의 id 이름마다 서버가 읽는 정수가 같은 값이 있으면 그 항목이다.
GET의 최종 주소가 목록 문서면(리다이렉트) 그 주소도 목록 문서다.

- 4차 리뷰: 서버는 목록 항목이 쓰지 않는 id 이름(`contentId`·`SEQ`·`thtimt_cntnts_sn`)을 무시하고
  id 값의 앞 정수를 읽는다(`3796abc`·`3796%2B`).
- 5차 리뷰: KDCA `cntnts_sn`은 값의 숫자만 모아 읽는다(`a3796`·`-3796`·`37a96`도 요통 3796).
  다른 id 이름은 관측이 없어 앞 정수 규칙이다.
- 숫자는 ASCII `0-9`만이다(#183 후속). 전각 `３７９６`은 목록 문서가 아닌 주소로 실제 GET 판정을 받는다.
- 같은 이름이 반복되면 서버는 첫 값을 쓴다. 목록 문서라서 **빼는** 판정(진료비·병원 선택 글의
  생성·발행 전 재검증·PATCH 422·제외 목록)은 어느 값이든 맞으면 목록 문서로 보고, 목록 문서로
  **인정해 주는** 판정(의료 글의 카탈로그 주제 대조·장애 시 유지)은 첫 값만 본다.
- 제외 목록 문서로 리다이렉트되는 주소는 그 제외 문서다(생성·발행 전 재검증·PATCH).

- 생성 검증·발행 전 재검증·관리자 PATCH(422) 모두 별칭을 잡는다.
- 다른 문서 id(3797·37960·99999·숫자 없는 값, 다른 MedlinePlus 글)나 항목이 쓰지 않는 이름에만
  실린 id는 목록 문서가 아니다.
- 의료 글이 인용한 별칭은 목록 문서로 인정된다(`curated_verified`).
- 공개된 글은 규칙 밖이다.

네트워크·DB는 쓰지 않는다 — 가짜 fetcher와 더블만 쓴다.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import HTTPException

from app.api.admin.content import CURATED_REFERENCE_NOT_ALLOWED_MESSAGE
from app.models.content import ContentType
from app.services import content_engine
from app.services.reference_publication import (
    apply_publication_reference_refresh,
    publication_references_settled,
    refresh_publication_references,
)
from app.services.reference_verification import (
    ReferenceVerifier,
    override_reference_fetcher,
    reference_check_record,
)
from app.utils.authority_sources import (
    CURATED_DOCUMENT_ID_PARAMS,
    CURATED_MEDICAL_SOURCE_PAGES,
    CURATED_SOURCE_URLS,
    REFERENCE_URL_EXCLUSIONS,
    curated_source_entries,
    is_curated_source_url,
)
from app.workers import tasks
from tests.reference_fetch_doubles import PageFetcher
from tests.test_reference_operator_decides import (
    _patch,
    _patch_setup,
    _published,
    _unwritten_slot,
)
from tests.test_reference_publication_gate import KDCA_VIEW, _now, _stamp
from tests.test_reference_requirement import (
    CURATED_HYPERTENSION,
    _brief_hypertension,
    _mapo_hospital,
    _notice_payload,
    _stub_writer,
)

LOW_BACK = KDCA_VIEW.format(3796)
CAROTID = "https://medlineplus.gov/ency/article/003774.htm"
SHOCKWAVE = "https://pubmed.ncbi.nlm.nih.gov/28403111/"
# 요통 문서 앞에 정렬상 앞서는 다른 id 16개(4차 리뷰 hold/bypass6.py의 (e)) — 옛 키 조합 상한을 넘긴다.
_SORTED_FIRST_IDS = ["1", *(str(n) for n in range(10, 24)), "2"]
# 리뷰어가 공개 GET으로 같은 문서임을 확인한 별칭(3차 4종 2026-09-29 19시 KST, 4차 5종 21시 KST)과
# 서버 동작과 무관하게 표기만 다른 경로 별칭.
ALIASES = {
    "utm": (LOW_BACK + "&utm_source=x", LOW_BACK),
    "port_443": (LOW_BACK.replace(".go.kr/", ".go.kr:443/", 1), LOW_BACK),
    "zero_padded_id": (KDCA_VIEW.format("03796"), LOW_BACK),
    "medlineplus_from": (CAROTID + "?from=x", CAROTID),
    # 4차 (a) 서버는 id 값의 앞 정수를 읽는다.
    "id_with_letters": (KDCA_VIEW.format("3796abc"), LOW_BACK),
    "id_with_plus": (KDCA_VIEW.format("3796%2B"), LOW_BACK),
    # 4차 (b)-(d) 항목이 쓰지 않는 id 이름은 서버도 무시한다.
    "unused_contentId": (LOW_BACK + "&contentId=1", LOW_BACK),
    "unused_SEQ": (LOW_BACK + "&SEQ=1", LOW_BACK),
    "unused_thtimt_cntnts_sn": (LOW_BACK + "&thtimt_cntnts_sn=7", LOW_BACK),
    # 4차 (e) 반복 id — 상한 없이 하나라도 맞으면 목록 문서다(순서와 무관).
    "repeated_16_sorted_first": (
        LOW_BACK + "".join(f"&cntnts_sn={value}" for value in _SORTED_FIRST_IDS),
        LOW_BACK,
    ),
    "repeated_17_sorted_first": (
        LOW_BACK + "".join(f"&cntnts_sn={value}" for value in [*_SORTED_FIRST_IDS, "0"]),
        LOW_BACK,
    ),
    "repeated_other_after": (LOW_BACK + "&cntnts_sn=1", LOW_BACK),
    # 5차: KDCA `cntnts_sn`은 값의 숫자만 모아 읽는다(리뷰어 공개 GET, hold/bypass7b.py).
    "digits_after_letter": (KDCA_VIEW.format("a3796"), LOW_BACK),
    "digits_after_minus": (KDCA_VIEW.format("-3796"), LOW_BACK),
    "digits_around_letter": (KDCA_VIEW.format("37a96"), LOW_BACK),
    "digits_between_letters": (KDCA_VIEW.format("3a7b9c6"), LOW_BACK),
    "digits_after_encoded_plus": (KDCA_VIEW.format("%2B3796"), LOW_BACK),
    # 경로 표기: 끝 슬래시(목록 URL에 있거나 없거나)·퍼센트 인코딩·겹친 슬래시.
    "trailing_slash_dropped": (SHOCKWAVE.rstrip("/"), SHOCKWAVE),
    "trailing_slash_added": (CAROTID + "/", CAROTID),
    "percent_encoded_path": (
        LOW_BACK.replace("gnrlzHealthInfoView.do", "gnrlzHealthInfoView%2Edo"),
        LOW_BACK,
    ),
    "double_slash_path": (LOW_BACK.replace("/healthinfo/biz/", "/healthinfo//biz/"), LOW_BACK),
}
ALIAS_IDS = sorted(ALIASES)
# 빼는 판정만 목록 문서로 보는 주소 — 반복 id의 첫 값이 아닌 값이 목록 문서다. 서버는 첫 값(1)을
# 돌려주므로 의료 글에서 요통 문서로 인정하지 않지만, 진료비 글에서는 어느 값이든 뺀다.
STRICT_ONLY_ALIASES = {
    "repeated_other_first": (KDCA_VIEW.format("1") + "&cntnts_sn=3796", LOW_BACK),
}
REMOVED_ALIASES = {**ALIASES, **STRICT_ONLY_ALIASES}
REMOVED_ALIAS_IDS = sorted(REMOVED_ALIASES)
# 별칭이 가리키는 문서의 주제어를 제목에 품은 발행 전 진료비 글 — 목록 밖 URL이었다면 GET으로 통과했다.
COST_TITLE_FOR = {
    LOW_BACK: ("요통 도수치료 비용 — 보험 적용과 횟수에 따라 달라지는 이유", "요통"),
    CAROTID: ("경동맥초음파 검사 비용 — 보험 적용과 본인부담", "경동맥초음파"),
    SHOCKWAVE: ("체외충격파 치료 비용 — 횟수와 보험 적용", "체외충격파"),
}
MEDICAL_TITLE_FOR = {
    LOW_BACK: ("요통이 오래갈 때 — 원인과 치료", "요통"),
    CAROTID: ("경동맥초음파 검사 — 언제 받나요", "경동맥초음파"),
    SHOCKWAVE: ("체외충격파 치료 — 족저근막염에 효과가 있나요", "체외충격파"),
}
# 목록 밖 주소가 GET에서 요통 문서로 리다이렉트된다.
REDIRECTING = KDCA_VIEW.format(9002)


def _fetcher(*urls: str, redirect_to: str | None = None) -> PageFetcher:
    fetcher = PageFetcher()
    for url in urls:
        canonical = next((c for a, c in REMOVED_ALIASES.values() if a == url), url)
        _title, topic = MEDICAL_TITLE_FOR.get(canonical, ("", "요통"))
        fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    if redirect_to is not None:
        _title, topic = MEDICAL_TITLE_FOR[redirect_to]
        fetcher.add_document(redirect_to, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
        status, _final, html = fetcher.pages[redirect_to]
        fetcher.pages[REDIRECTING] = (status, redirect_to, html)
    return fetcher


def _page_pass(url: str, *, final_url: str | None = None, curated: bool = False) -> dict:
    """저장된 신선한 GET 통과 기록(수정 전 코드가 별칭을 목록 밖으로 판정해 남긴 모양)."""

    checked = _now()
    return reference_check_record(
        url,
        verdict="pass",
        reason="page_verified",
        checked_at=checked,
        curated=curated,
        status=200,
        final_url=final_url or url,
        page_title="요통 | 국가건강정보포털 | 질병관리청",
        text_len=800,
        verified_at=checked,
    )


# ── 동일성 키 ──────────────────────────────────────────────────────


def test_every_catalog_url_is_its_own_document():
    """문서 id 밖의 질의를 무시해도 서로 다른 목록 문서가 한 항목으로 합쳐지지 않는다."""

    # 같은 URL의 목록 항목이 둘이면 같은 문서다 — 지금 목록에는 그런 항목이 없다.
    assert len(CURATED_SOURCE_URLS) == len(CURATED_MEDICAL_SOURCE_PAGES)
    for source in CURATED_MEDICAL_SOURCE_PAGES:
        assert curated_source_entries(source["url"]) == [source]


def test_the_document_id_params_are_the_ones_the_lists_use():
    """목록·제외 목록 URL의 질의 이름 가운데 id가 아닌 것은 `MODE`(보기 방식) 하나뿐이다."""

    from urllib.parse import parse_qsl, urlparse

    urls = [*CURATED_SOURCE_URLS, *(entry["url"] for entry in REFERENCE_URL_EXCLUSIONS)]
    names = {
        name for url in urls for name, _value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    }
    assert names - CURATED_DOCUMENT_ID_PARAMS == {"MODE"}
    assert CURATED_DOCUMENT_ID_PARAMS <= names


@pytest.mark.parametrize("name", ALIAS_IDS)
def test_an_alias_is_the_curated_document(name):
    alias, canonical = ALIASES[name]

    assert alias not in CURATED_SOURCE_URLS
    assert is_curated_source_url(alias)
    assert curated_source_entries(alias) == curated_source_entries(canonical)
    assert len(curated_source_entries(canonical)) == 1


@pytest.mark.parametrize(
    "url",
    [
        LOW_BACK.replace("http://", "https://").replace(".go.kr/", ".go.kr:80/", 1),
        LOW_BACK.replace("https://", "http://").replace(".go.kr/", ".go.kr:443/", 1),
        LOW_BACK.replace("https://", "https://WWW.") + "#section",
        LOW_BACK.replace("gnrlzHealthInfoView.do", "gnrlzHealthInfoView.do;jsessionid=AB12"),
        LOW_BACK + "&cntnts_sn=3796",
        KDCA_VIEW.format("0" * 5000 + "3796"),
        LOW_BACK.replace("cntnts_sn=", "cntnts%5Fsn="),
        LOW_BACK.replace("https://", "https://user:pw@"),
        LOW_BACK.replace(".go.kr/", ".go.kr./", 1),  # 호스트 끝 점(인용 불가지만 같은 문서)
    ],
    ids=[
        "explicit_80",
        "http_443",
        "www_fragment",
        "jsessionid",
        "repeated_same_id",
        "many_leading_zeros",
        "percent_encoded_name",
        "userinfo",
        "trailing_dot_host",
    ],
)
def test_default_ports_and_decorations_are_the_same_document(url):
    assert is_curated_source_url(url)
    assert curated_source_entries(url) == curated_source_entries(LOW_BACK)


@pytest.mark.parametrize(
    "url",
    [
        KDCA_VIEW.format(3797),
        KDCA_VIEW.format(37960),
        KDCA_VIEW.format(379),
        KDCA_VIEW.format(99999),
        KDCA_VIEW.format("abc"),
        KDCA_VIEW.format(""),
        # 숫자만 모아도 다른 id — 서버도 없는 문서(37961)·다른 문서를 준다.
        KDCA_VIEW.format("a3797"),
        KDCA_VIEW.format("37a97"),
        KDCA_VIEW.format("3796-1"),
        KDCA_VIEW.format("3796%26x%3D1"),
        KDCA_VIEW.format("3796;x=1"),
        KDCA_VIEW.format("0x0ED4"),
        KDCA_VIEW.format("9" * 5000),
        LOW_BACK.replace("cntnts_sn=", "CNTNTS_SN="),
        KDCA_VIEW.format("1") + "&cntnts_sn=2",
        # 항목이 쓰지 않는 이름에만 실린 id — 서버는 읽지 않는다.
        KDCA_VIEW.split("?", 1)[0] + "?contentId=3796",
        KDCA_VIEW.split("?", 1)[0] + "?thtimt_cntnts_sn=3796&SEQ=3796",
        LOW_BACK.replace(".go.kr/", ".go.kr:8443/", 1),
        LOW_BACK.replace("gnrlzHealthInfoView.do", "GnrlzHealthInfoView.do"),
        KDCA_VIEW.split("?", 1)[0],
        "https://medlineplus.gov/ency/article/003775.htm",
        "https://medlineplus.gov/ency/article/003774.html",
        "https://pubmed.ncbi.nlm.nih.gov/28403112/",
    ],
    ids=[
        "id_3797",
        "id_37960",
        "id_379",
        "id_99999",
        "no_leading_digits",
        "empty_id",
        "letter_then_3797",
        "digits_37_97",
        "minus_suffix_37961",
        "encoded_query_suffix_37961",
        "semicolon_suffix_37961",
        "hex_id",
        "overlong_id",
        "uppercase_name",
        "repeated_other_ids",
        "id_under_unused_name",
        "ids_under_unused_names",
        "other_port",
        "path_case",
        "no_id",
        "medlineplus_other_article",
        "medlineplus_other_path",
        "pubmed_other_article",
    ],
)
def test_a_different_document_is_not_curated(url):
    assert not is_curated_source_url(url)
    assert curated_source_entries(url) == []


# 목록에 있는 요통 밖의 KDCA 문서 하나(고혈압) — 한 주소에 두 목록 id가 반복된 경우에 쓴다.
_OTHER_KDCA_SOURCE = next(
    source
    for source in CURATED_MEDICAL_SOURCE_PAGES
    if source["url"] == KDCA_VIEW.format(6765)
)


def test_a_repeated_id_naming_two_catalog_documents_is_curated_but_served_as_the_first():
    """빼는 판정은 어느 값이든 목록 문서다. 인정해 주는 판정(항목)은 서버가 쓰는 첫 값의 문서다."""

    both = LOW_BACK + "&cntnts_sn=6765"
    reversed_order = KDCA_VIEW.format(6765) + "&cntnts_sn=3796"
    (low_back,) = curated_source_entries(LOW_BACK)
    assert is_curated_source_url(both) and is_curated_source_url(reversed_order)
    assert curated_source_entries(both) == [low_back]
    assert curated_source_entries(reversed_order) == [_OTHER_KDCA_SOURCE]


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("요통이 오래갈 때 — 원인과 치료", "curated_verified"),
        ("고혈압 약을 먹기 시작할 때 — 생활 관리", "unrelated_topic"),
    ],
    ids=["first_value_topic", "second_value_topic"],
)
async def test_a_medical_post_citing_a_two_document_url_matches_only_the_first_value(title, reason):
    url = LOW_BACK + "&cntnts_sn=6765"
    fetcher = PageFetcher()
    fetcher.add_document(url, "요통 | 국가건강정보포털 | 질병관리청", topic="요통")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[title]
    )

    (check,) = outcome.checks
    assert check["reason"] == reason and check["curated"] is True


async def test_a_medical_post_citing_a_catalog_id_after_another_id_is_not_that_document():
    """`cntnts_sn=1&cntnts_sn=3796` — 서버는 1번 문서를 준다. 요통 카탈로그로 통과하지 않고
    목록 밖 문서로 실제 GET 본문을 판정한다(여기서는 1번 문서가 요통 글이 아니다)."""

    title, _topic = MEDICAL_TITLE_FOR[LOW_BACK]
    alias = STRICT_ONLY_ALIASES["repeated_other_first"][0]
    assert curated_source_entries(alias) == []
    fetcher = PageFetcher()
    fetcher.add_document(alias, "사마귀 | 국가건강정보포털 | 질병관리청", topic="사마귀")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": alias}], topic_terms=[title]
    )

    assert fetcher.calls == [alias] and outcome.kept == []
    (check,) = outcome.checks
    assert check["curated"] is False and check["reason"] not in {
        "curated_verified",
        "curated_unreachable",
    }


@pytest.mark.parametrize(
    ("url", "kept"),
    [(LOW_BACK + "&cntnts_sn=1", True), (KDCA_VIEW.format(1) + "&cntnts_sn=3796", False)],
    ids=["catalog_id_first", "catalog_id_second"],
)
async def test_a_repeated_id_is_kept_on_outage_only_when_the_catalog_id_comes_first(url, kept):
    title, _topic = MEDICAL_TITLE_FOR[LOW_BACK]
    fetcher = PageFetcher({url: TimeoutError("site down")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[title], defer_transient=True
    )

    (check,) = outcome.checks
    if kept:
        assert [ref["url"] for ref in outcome.kept] == [url]
        assert check["reason"] == "curated_unreachable"
    else:
        # 목록 밖 주소의 일시 장애 — 인정하지 않고 다음 시간대로 미룬다.
        assert outcome.kept == [] and check["curated"] is False
        assert check["reason"] != "curated_unreachable"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("a3796", 3796), ("-3796", 3796), ("37a96", 3796), ("6a7b6c5", 6765), ("%2B6765", 6765)],
)
def test_digits_forming_a_catalog_id_name_that_document(value, expected):
    (entry,) = curated_source_entries(KDCA_VIEW.format(value))
    assert entry["url"] == KDCA_VIEW.format(expected)


def test_only_cntnts_sn_reads_every_digit():
    """다른 id 이름은 서버 관측이 없어 앞 정수 규칙 그대로다(AMC `contentId`)."""

    amc = "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId={}"
    assert is_curated_source_url(amc.format(31773))
    assert is_curated_source_url(amc.format("31773abc"))
    assert is_curated_source_url(amc.format("%2B31773"))  # 앞의 `+`(퍼센트 인코딩)
    assert not is_curated_source_url(amc.format("a31773"))


def test_the_exclusion_list_uses_the_same_document_matcher():
    """제외 목록도 별칭으로 비껴 가지 못한다 — 목록 문서와 같은 판정이다."""

    from app.utils.authority_sources import reference_exclusion_reason

    excluded = REFERENCE_URL_EXCLUSIONS[0]["url"]  # KDCA cntnts_sn=6263
    assert excluded.endswith("cntnts_sn=6263")
    for alias in (
        excluded.replace("6263", "06263"),
        excluded.replace("6263", "6263abc"),
        excluded.replace("6263", "a6263"),
        excluded.replace("6263", "-62a63"),
        excluded + "&utm_source=x&contentId=1",
        excluded.replace(".go.kr/", ".go.kr:443/", 1),
        excluded.replace("cntnts_sn=6263", "cntnts_sn=1&cntnts_sn=6263"),
        excluded.replace("/healthinfo/biz/", "/healthinfo//biz/"),
    ):
        assert reference_exclusion_reason(alias) == REFERENCE_URL_EXCLUSIONS[0]["reason"], alias
    cancer_seq = REFERENCE_URL_EXCLUSIONS[-1]["url"]
    assert cancer_seq.endswith("cancer_seq=3797")
    assert reference_exclusion_reason(cancer_seq + "&from=x") is not None
    # 같은 경로의 다른 문서는 제외 목록이 아니다.
    assert reference_exclusion_reason(excluded.replace("6263", "6264")) is None
    assert reference_exclusion_reason(cancer_seq.replace("3797", "3798")) is None


# ── (i) 생성 검증 ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", REMOVED_ALIAS_IDS)
async def test_generation_drops_a_curated_alias_cited_on_a_cost_title(name):
    alias, canonical = REMOVED_ALIASES[name]
    title, topic = COST_TITLE_FOR[canonical]
    fetcher = _fetcher(alias)
    result = {
        "title": title,
        "body": f"## {topic}의 원인과 치료\n{topic} 진료 안내\n## 비용\n본문",
        "faq_question": None,
        "references": [{"title": topic, "url": alias}],
    }

    with override_reference_fetcher(fetcher):
        notes = await content_engine._verify_generated_references(
            result, {"target_keyword": topic}, required=True
        )

    assert result["references"] == []
    assert fetcher.calls == []  # 수기 목록 문서처럼 GET 전에 뺀다
    assert any("수기 목록 문서를 쓰지 않음" in note for note in notes)


@pytest.mark.parametrize(
    "alias",
    [
        CURATED_HYPERTENSION + "&utm_source=x",
        CURATED_HYPERTENSION.replace(".go.kr/", ".go.kr:443/", 1),
        KDCA_VIEW.format("06765"),
    ],
    ids=["utm", "port_443", "zero_padded_id"],
)
async def test_an_unwritten_cost_slot_citing_an_alias_goes_to_the_operator(monkeypatch, alias):
    """생성 경로 끝까지 — 별칭도 저장되지 않고, 쓰이지 않은 슬롯은 사람의 결정이 된다."""

    payload = _notice_payload([{"title": "고혈압", "url": alias}])
    payload["title"] = "마포 고혈압 진료비 — 검사·약값과 건강보험 본인부담"
    _stub_writer(monkeypatch, payload)
    fetcher = PageFetcher()
    fetcher.add_document(alias, "고혈압 | 국가건강정보포털 | 질병관리청", topic="고혈압")

    with override_reference_fetcher(fetcher):
        with pytest.raises(content_engine.MissingCitableReferencesError) as raised:
            await content_engine.generate_content(
                _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_hypertension()
            )

    assert raised.value.result["references"] == []
    assert fetcher.calls == []
    assert tasks._generation_left_references_to_operator(raised.value, _unwritten_slot())


async def test_generation_drops_an_outside_url_that_redirects_to_a_curated_document():
    title, topic = COST_TITLE_FOR[LOW_BACK]
    fetcher = _fetcher(redirect_to=LOW_BACK)
    result = {
        "title": title,
        "body": "## 요통의 원인과 치료\n요통 진료 안내",
        "faq_question": None,
        "references": [{"title": topic, "url": REDIRECTING}],
    }

    with override_reference_fetcher(fetcher):
        notes = await content_engine._verify_generated_references(
            result, {"target_keyword": topic}, required=True
        )

    assert result["references"] == []
    assert fetcher.calls == [REDIRECTING]  # 열어 보고서야 목록 문서임을 안다
    assert any("수기 목록 문서를 쓰지 않음" in note for note in notes)
    (check,) = [c for c in result["reference_checks"] if c["url"] == REDIRECTING]
    assert check["curated"] is True and check["final_url"] == LOW_BACK


async def test_generation_keeps_a_redirect_to_a_curated_document_on_a_medical_title():
    title, topic = MEDICAL_TITLE_FOR[LOW_BACK]
    fetcher = _fetcher(redirect_to=LOW_BACK)
    result = {
        "title": title,
        "body": "## 요통의 원인과 치료\n요통 진료 안내",
        "faq_question": None,
        "references": [{"title": topic, "url": REDIRECTING}],
    }

    with override_reference_fetcher(fetcher):
        await content_engine._verify_generated_references(
            result, {"target_keyword": topic}, required=True
        )

    assert [ref["url"] for ref in result["references"]] == [REDIRECTING]
    assert result["reference_checks"][-1]["reason"] == "curated_verified"


# ── 카탈로그 대조 주소(`judge_fetched_page`의 `catalog_url`) ─────────────────
# 주소 자신이 목록 문서를 돌려주면(반복 id는 첫 값) 그 주소의 카탈로그로, 아니면 GET의 최종 주소로
# 대조한다. 목록 문서 주소가 목록 밖으로 리다이렉트돼도 주소의 카탈로그가 말하고, 첫 값이 목록
# 문서가 아닌 반복 id 주소가 목록 문서로 리다이렉트되면 최종 주소의 카탈로그가 말한다.

_CATALOG_REDIRECTS = {
    # 목록 문서(요통) → 목록 밖 문서. 최종 주소로 대조하면 카탈로그가 없다.
    "catalog_to_outside": (LOW_BACK, KDCA_VIEW.format(9006), "요통이 오래갈 때 — 원인과 치료"),
    # 반복 id의 둘째 값만 목록 문서(빼는 판정에서만 목록 문서) → 고혈압 목록 문서.
    # 주소로 대조하면(어느 값이든 판정) 첫 값의 문서가 없어 카탈로그가 없다.
    "second_value_to_catalog": (
        KDCA_VIEW.format(1) + "&cntnts_sn=3796",
        KDCA_VIEW.format(6765),
        "고혈압 약을 먹기 시작할 때 — 생활 관리",
    ),
}


@pytest.mark.parametrize("name", sorted(_CATALOG_REDIRECTS))
async def test_the_catalog_is_the_served_document_of_the_url_or_else_the_final_url(name):
    url, final_url, title = _CATALOG_REDIRECTS[name]
    fetcher = PageFetcher()
    fetcher.add_document(final_url, "문서 | 국가건강정보포털 | 질병관리청", topic="문서")
    status, _final, html = fetcher.pages[final_url]
    fetcher.pages[url] = (status, final_url, html)

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[title]
    )

    (check,) = outcome.checks
    assert check["curated"] is True and check["final_url"] == final_url
    assert check["reason"] == "curated_verified"
    assert [ref["url"] for ref in outcome.kept] == [url]


# ── 의료 글은 별칭도 목록 문서로 인정한다 ─────────────────────────────


@pytest.mark.parametrize("name", ALIAS_IDS)
async def test_a_medical_post_citing_an_alias_passes_as_the_curated_document(name):
    alias, canonical = ALIASES[name]
    title, _topic = MEDICAL_TITLE_FOR[canonical]
    fetcher = _fetcher(alias)

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": alias}], topic_terms=[title]
    )

    assert [ref["url"] for ref in outcome.kept] == [alias]
    (check,) = outcome.checks
    assert check["reason"] == "curated_verified" and check["curated"] is True


# ── (ii) 발행 전 재검증 ────────────────────────────────────────────


def _scheduled_cost_post(url: str, *, canonical: str, status, checks=None):
    title, topic = COST_TITLE_FOR[canonical]
    item = _published(title, status)
    item.body = f"## {topic}의 원인과 치료\n{topic} 진료 안내"
    item.references_list = [{"title": topic, "url": url}]
    item.reference_checks = checks
    return _stamp(item)


@pytest.mark.parametrize("fresh", [False, True], ids=["unchecked", "fresh_page_pass"])
@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
@pytest.mark.parametrize("name", REMOVED_ALIAS_IDS)
async def test_publication_refresh_drops_a_curated_alias_on_a_cost_post(name, status, fresh):
    alias, canonical = REMOVED_ALIASES[name]
    item = _scheduled_cost_post(
        alias, canonical=canonical, status=status, checks=[_page_pass(alias)] if fresh else None
    )
    fetcher = _fetcher(alias)

    assert not publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.references_changed
    assert refresh.operator_decides and not refresh.healed and not refresh.deferred
    assert fetcher.calls == []
    assert apply_publication_reference_refresh(item, refresh)
    assert (item.references_list, item.content_revision) == ([], 8)


@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
async def test_publication_refresh_drops_a_redirect_to_a_curated_document(status):
    item = _scheduled_cost_post(REDIRECTING, canonical=LOW_BACK, status=status)
    fetcher = _fetcher(redirect_to=LOW_BACK)

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls == [REDIRECTING]
    assert refresh.references == [] and refresh.operator_decides and not refresh.healed


async def test_a_stored_pass_whose_final_url_is_curated_is_not_settled():
    """수정 전 코드가 남긴 통과 기록(목록 밖 판정)도 최종 주소가 목록 문서면 다시 보고 뺀다."""

    item = _scheduled_cost_post(
        REDIRECTING,
        canonical=LOW_BACK,
        status=tasks.ContentStatus.READY,
        checks=[_page_pass(REDIRECTING, final_url=LOW_BACK)],
    )
    fetcher = _fetcher(redirect_to=LOW_BACK)

    assert not publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.operator_decides
    assert fetcher.calls == []  # 기록의 최종 주소로 이미 목록 문서임을 안다


async def test_a_redirect_that_leaves_the_catalog_is_kept_on_a_cost_post():
    """대조군 — 목록 밖 문서로 가는 주소(최종 주소도 목록 밖)는 GET 통과대로 남는다."""

    other = KDCA_VIEW.format(9003)
    item = _scheduled_cost_post(REDIRECTING, canonical=LOW_BACK, status=tasks.ContentStatus.DRAFT)
    fetcher = _fetcher(other)
    status, _final, html = fetcher.pages[other]
    fetcher.pages[REDIRECTING] = (status, other, html)

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert [ref["url"] for ref in refresh.references] == [REDIRECTING]
    assert not refresh.operator_decides


@pytest.mark.parametrize("status", [tasks.ContentStatus.PUBLISHED, tasks.ContentStatus.WITHHELD])
@pytest.mark.parametrize("url", [ALIASES["utm"][0], REDIRECTING], ids=["alias", "redirect"])
async def test_a_published_post_with_an_alias_or_redirect_stays_byte_identical(url, status):
    item = _scheduled_cost_post(
        url,
        canonical=LOW_BACK,
        status=status,
        checks=[_page_pass(url, final_url=LOW_BACK)],
    )
    item.content_revision = 7
    before = json.dumps(vars(copy.deepcopy(item)), default=str, sort_keys=True)
    fetcher = _fetcher(url, redirect_to=LOW_BACK)

    assert publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.already_current and refresh.references == item.references_list
    assert not refresh.references_changed and not refresh.operator_decides
    assert fetcher.calls == []
    assert json.dumps(vars(item), default=str, sort_keys=True) == before


# ── (iii) 관리자 PATCH ────────────────────────────────────────────


@pytest.mark.parametrize("status", ["DRAFT", "READY"])
@pytest.mark.parametrize("name", REMOVED_ALIAS_IDS)
async def test_patch_rejects_a_curated_alias_before_any_get(monkeypatch, name, status):
    alias, canonical = REMOVED_ALIASES[name]
    title, topic = COST_TITLE_FOR[canonical]
    hospital, item = _patch_setup(monkeypatch, title=title, status=status)
    fetcher = _fetcher(alias)

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(hospital, item, references=[{"title": topic, "url": alias}])

    assert raised.value.status_code == 422
    detail = raised.value.detail
    assert detail["code"] == "CURATED_REFERENCE_NOT_ALLOWED"
    assert detail["urls"] == [alias]
    assert detail["message"].startswith(CURATED_REFERENCE_NOT_ALLOWED_MESSAGE)
    assert fetcher.calls == []
    assert item.references_list == [] and item.content_revision == 3


async def test_patch_rejects_a_url_that_redirects_to_a_curated_document(monkeypatch):
    """GET 전에는 모른다 — PATCH가 이미 하는 GET의 최종 주소로 같은 422를 낸다."""

    title, topic = COST_TITLE_FOR[LOW_BACK]
    hospital, item = _patch_setup(monkeypatch, title=title)
    fetcher = _fetcher(redirect_to=LOW_BACK)

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(hospital, item, references=[{"title": topic, "url": REDIRECTING}])

    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "CURATED_REFERENCE_NOT_ALLOWED"
    assert raised.value.detail["urls"] == [REDIRECTING]
    assert fetcher.calls == [REDIRECTING]
    assert item.references_list == [] and item.content_revision == 3


async def test_patch_accepts_a_redirect_to_a_curated_document_on_a_medical_post(monkeypatch):
    title, topic = MEDICAL_TITLE_FOR[LOW_BACK]
    hospital, item = _patch_setup(monkeypatch, title=title)

    with override_reference_fetcher(_fetcher(redirect_to=LOW_BACK)):
        await _patch(hospital, item, references=[{"title": topic, "url": REDIRECTING}])

    assert [ref["url"] for ref in item.references_list] == [REDIRECTING]
    assert [check["reason"] for check in item.reference_checks] == ["curated_verified"]


async def test_patch_keeps_a_different_document_id_on_a_cost_post(monkeypatch):
    """대조군 — 다른 id(3797)는 목록 문서가 아니다. 422 없이 실제 GET으로 판정한다."""

    other = KDCA_VIEW.format(3797)
    title, topic = COST_TITLE_FOR[LOW_BACK]
    hospital, item = _patch_setup(monkeypatch, title=title)
    fetcher = _fetcher(other)

    with override_reference_fetcher(fetcher):
        await _patch(hospital, item, references=[{"title": topic, "url": other}])

    assert [ref["url"] for ref in item.references_list] == [other]
    assert fetcher.calls == [other]
    assert [check["reason"] for check in item.reference_checks] == ["page_verified"]


@pytest.mark.parametrize("name", ALIAS_IDS)
async def test_an_alias_is_judged_by_the_catalog_when_its_site_is_down(name):
    """GET이 최종 주소를 주지 못해도(접속 불가) 별칭은 목록 문서다 — 미루지 않고 카탈로그로 판정한다."""

    alias, canonical = ALIASES[name]
    title, _topic = MEDICAL_TITLE_FOR[canonical]
    fetcher = PageFetcher({alias: TimeoutError("site down")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": alias}], topic_terms=[title], defer_transient=True
    )

    assert fetcher.calls == [alias]
    assert outcome.deferred == []
    assert [ref["url"] for ref in outcome.kept] == [alias]
    (check,) = outcome.checks
    assert check["reason"] == "curated_unreachable" and check["curated"] is True


@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
async def test_publication_refresh_drops_a_trailing_dot_host_alias_before_any_get(status):
    alias = LOW_BACK.replace(".go.kr/", ".go.kr./", 1)
    item = _scheduled_cost_post(alias, canonical=LOW_BACK, status=status)
    fetcher = _fetcher(alias)

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.operator_decides
    assert fetcher.calls == []
    assert refresh.checks == []  # 목록 문서로 빠졌다(인용 불가 판정 기록이 아니다)


# ── 제외 목록 문서로 리다이렉트되는 주소(5차 후속) ──────────────────────

EXCLUDED = REFERENCE_URL_EXCLUSIONS[0]["url"]  # KDCA cntnts_sn=6263 '소화불량'
REDIRECT_TO_EXCLUDED = KDCA_VIEW.format(9005)
DYSPEPSIA_TITLE = "소화불량이 계속될 때 — 원인과 치료"


def _excluded_redirect_fetcher(target: str = EXCLUDED) -> PageFetcher:
    """목록 밖 주소가 GET에서 `target`으로 리다이렉트되고, 받은 본문은 주제가 맞는 문서처럼 보인다."""

    fetcher = PageFetcher()
    fetcher.add_document(target, "소화불량 | 국가건강정보포털 | 질병관리청", topic="소화불량")
    status, _final, html = fetcher.pages[target]
    fetcher.pages[REDIRECT_TO_EXCLUDED] = (status, target, html)
    return fetcher


def _medical_post(url: str, *, status, checks=None):
    item = _published(DYSPEPSIA_TITLE, status)
    item.content_type = "DISEASE"
    item.body = "## 소화불량의 원인과 치료\n소화불량 진료 안내"
    item.references_list = [{"title": "소화불량", "url": url}]
    item.reference_checks = checks
    return _stamp(item)


async def test_a_redirect_to_an_excluded_document_is_judged_like_the_excluded_url():
    outcome = await ReferenceVerifier(_excluded_redirect_fetcher(), domain_spacing=0).verify(
        [{"title": "a", "url": REDIRECT_TO_EXCLUDED}, {"title": "b", "url": EXCLUDED}],
        topic_terms=[DYSPEPSIA_TITLE],
    )

    assert outcome.kept == []
    redirect, direct = outcome.checks
    assert (redirect["verdict"], redirect["reason"]) == (direct["verdict"], direct["reason"])
    assert redirect["reason"] == "excluded_source" and redirect["final_url"] == EXCLUDED


async def test_generation_drops_a_redirect_to_an_excluded_document():
    fetcher = _excluded_redirect_fetcher()
    result = {
        "title": DYSPEPSIA_TITLE,
        "body": "## 소화불량의 원인과 치료\n소화불량 진료 안내",
        "faq_question": None,
        "references": [{"title": "소화불량", "url": REDIRECT_TO_EXCLUDED}],
    }

    with override_reference_fetcher(fetcher):
        await content_engine._verify_generated_references(
            result, {"target_keyword": "소화불량"}, required=True
        )

    assert REDIRECT_TO_EXCLUDED not in [ref["url"] for ref in result["references"]]
    assert fetcher.calls[0] == REDIRECT_TO_EXCLUDED
    (check,) = [c for c in result["reference_checks"] if c["url"] == REDIRECT_TO_EXCLUDED]
    assert check["reason"] == "excluded_source"


@pytest.mark.parametrize("stored", [False, True], ids=["unchecked", "stored_page_pass"])
@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
async def test_publication_refresh_drops_a_redirect_to_an_excluded_document(status, stored):
    """수정 전 코드가 남긴 통과 기록(최종 주소가 제외 문서)도 통과로 치지 않고 다시 연다."""

    checks = [_page_pass(REDIRECT_TO_EXCLUDED, final_url=EXCLUDED)] if stored else None
    item = _medical_post(REDIRECT_TO_EXCLUDED, status=status, checks=checks)
    fetcher = _excluded_redirect_fetcher()

    assert not publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls[0] == REDIRECT_TO_EXCLUDED
    assert REDIRECT_TO_EXCLUDED not in [ref["url"] for ref in refresh.references]
    (check,) = [c for c in refresh.checks if c["url"] == REDIRECT_TO_EXCLUDED]
    assert check["reason"] == "excluded_source"
    assert apply_publication_reference_refresh(item, refresh)
    assert REDIRECT_TO_EXCLUDED not in [ref["url"] for ref in item.references_list]


@pytest.mark.parametrize("url", [REDIRECT_TO_EXCLUDED, EXCLUDED], ids=["redirect", "direct"])
async def test_patch_rejects_a_redirect_to_an_excluded_document_like_the_excluded_url(
    monkeypatch, url
):
    hospital, item = _patch_setup(monkeypatch, title=DYSPEPSIA_TITLE, content_type="DISEASE")

    with override_reference_fetcher(_excluded_redirect_fetcher()), pytest.raises(
        HTTPException
    ) as raised:
        await _patch(hospital, item, references=[{"title": "소화불량", "url": url}])

    assert raised.value.status_code == 400
    (failure,) = raised.value.detail["failed_references"]
    assert (failure["url"], failure["reason"]) == (url, "excluded_source")
    assert item.references_list == [] and item.content_revision == 3


@pytest.mark.parametrize(
    ("final_url", "reused"),
    [(EXCLUDED, False), (REDIRECT_TO_EXCLUDED, True)],
    ids=["final_url_excluded", "control_final_url_not_excluded"],
)
async def test_an_old_pass_whose_final_url_is_excluded_is_not_reused_on_outage(final_url, reused):
    """장애 폴백(`_reusable_previous_pass`) — 7일 재사용 기간 안이지만 24시간 신선도 밖의 통과 기록.

    신선한 통과(`check_is_fresh_pass`)가 아니라서 그 판정과 무관하다. 기록의 최종 주소가 제외
    문서면(수정 전 코드가 남긴 리다이렉트 통과) 장애 때도 재사용하지 않고 미룬다. 대조군은 같은
    기록의 최종 주소만 제외 문서가 아닌 것 — 재사용된다.
    """

    from datetime import timedelta

    from app.services.reference_verification import check_is_fresh_pass, topic_fingerprint

    fingerprint = topic_fingerprint([DYSPEPSIA_TITLE])
    old = _now() - timedelta(days=2)
    prior = reference_check_record(
        REDIRECT_TO_EXCLUDED,
        verdict="pass",
        reason="page_verified",
        checked_at=old,
        curated=False,
        status=200,
        final_url=final_url,
        page_title="소화불량 | 국가건강정보포털 | 질병관리청",
        text_len=800,
        verified_at=old,
        topic_fingerprint=fingerprint,
    )
    assert not check_is_fresh_pass(prior, now=_now(), topic_fingerprint=fingerprint)
    fetcher = PageFetcher({REDIRECT_TO_EXCLUDED: TimeoutError("site down")})

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "소화불량", "url": REDIRECT_TO_EXCLUDED}],
        topic_terms=[DYSPEPSIA_TITLE],
        previous_checks=[prior],
        reuse_fresh_checks=True,
        defer_transient=True,
    )

    assert fetcher.calls == [REDIRECT_TO_EXCLUDED]
    (check,) = outcome.checks
    if reused:
        assert check["reason"] == "reused_previous_pass"
        assert [ref["url"] for ref in outcome.kept] == [REDIRECT_TO_EXCLUDED]
    else:
        assert check["reason"] != "reused_previous_pass"
        assert outcome.kept == []
        assert [ref["url"] for ref in outcome.deferred] == [REDIRECT_TO_EXCLUDED]


async def test_a_redirect_to_a_document_that_is_not_excluded_is_unaffected(monkeypatch):
    """대조군 — 같은 경로의 다른 문서(6264)로 가는 리다이렉트는 실제 본문 판정대로 남는다."""

    other = EXCLUDED.replace("6263", "6264")
    outcome = await ReferenceVerifier(_excluded_redirect_fetcher(other), domain_spacing=0).verify(
        [{"title": "a", "url": REDIRECT_TO_EXCLUDED}], topic_terms=[DYSPEPSIA_TITLE]
    )
    assert [ref["url"] for ref in outcome.kept] == [REDIRECT_TO_EXCLUDED]
    assert outcome.checks[0]["reason"] == "page_verified"

    hospital, item = _patch_setup(monkeypatch, title=DYSPEPSIA_TITLE, content_type="DISEASE")
    with override_reference_fetcher(_excluded_redirect_fetcher(other)):
        await _patch(hospital, item, references=[{"title": "소화불량", "url": REDIRECT_TO_EXCLUDED}])
    assert [ref["url"] for ref in item.references_list] == [REDIRECT_TO_EXCLUDED]


# ── 전각 숫자 id는 목록 문서가 아니다(PR #183 후속) ──────────────────────
# 문서 id는 ASCII `0-9`만 읽는다. 서버가 전각 `３７９６`을 3796으로 읽는다는 관측이 없으므로, 전각
# id 주소는 목록 밖 주소로 실제 GET 판정을 받는다 — 목록 문서로 인정받지도(`curated_*`) 않고, GET
# 전에 목록 문서로 빠지지도 않는다. 진료비 글에서는 실제 본문 통과가 있어야만 남는다.

AMC_DETAIL = "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId={}"
ANAL_FISSURE = AMC_DETAIL.format(31773)  # 앞 정수 규칙 이름(`contentId`)의 목록 문서 — 치열
FULLWIDTH_COST_TITLE_FOR = {
    **COST_TITLE_FOR,
    ANAL_FISSURE: ("치열 수술 비용 — 보험 적용과 본인부담", "치열"),
}
FULLWIDTH_MEDICAL_TITLE_FOR = {
    **MEDICAL_TITLE_FOR,
    ANAL_FISSURE: ("치열이 생겼을 때 — 원인과 치료", "치열"),
}
FULLWIDTH = {
    "kdca_fullwidth": (KDCA_VIEW.format("３７９６"), LOW_BACK),
    "kdca_mixed": (KDCA_VIEW.format("3７96"), LOW_BACK),  # 숫자만 모으면 396
    "kdca_percent_encoded": (KDCA_VIEW.format("%EF%BC%93%EF%BC%97%EF%BC%99%EF%BC%96"), LOW_BACK),
    "amc_fullwidth": (AMC_DETAIL.format("３１７７３"), ANAL_FISSURE),
    "amc_mixed": (AMC_DETAIL.format("3１773"), ANAL_FISSURE),  # 앞 정수는 3
    "amc_percent_encoded": (AMC_DETAIL.format("%EF%BC%93%EF%BC%91%EF%BC%97%EF%BC%97%EF%BC%93"), ANAL_FISSURE),
}
FULLWIDTH_IDS = sorted(FULLWIDTH)
# 페이지 관측 세 가지 — 서버가 실제 문서를 준다 / 없는 문서(빈 템플릿) / 접속 불가.
PAGE_OUTCOMES = ("real_page", "empty_template", "site_down")


def _fullwidth_fetcher(url: str, canonical: str, outcome: str) -> PageFetcher:
    _title, topic = FULLWIDTH_MEDICAL_TITLE_FOR[canonical]
    fetcher = PageFetcher()
    if outcome == "real_page":
        fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    elif outcome == "empty_template":
        fetcher.pages[url] = (200, url, "<html><head><title>국가건강정보포털</title></head><body></body></html>")
    else:
        fetcher.pages[url] = TimeoutError("site down")
    return fetcher


def _assert_not_curated_check(check: dict) -> None:
    assert check["curated"] is False
    assert check["reason"] not in {"curated_verified", "curated_unreachable"}


@pytest.mark.parametrize("name", FULLWIDTH_IDS)
def test_a_fullwidth_id_is_not_a_catalog_document(name):
    from app.services.reference_verification import _serves_curated_document, names_curated_document
    from app.utils.authority_sources import reference_exclusion_reason

    url, canonical = FULLWIDTH[name]
    assert is_curated_source_url(canonical)  # 대조군 — ASCII id는 그 목록 문서다
    assert not is_curated_source_url(url)
    assert curated_source_entries(url) == []
    assert not _serves_curated_document(url)
    assert not names_curated_document({"url": url, "final_url": url})
    assert reference_exclusion_reason(url) is None


@pytest.mark.parametrize(
    ("url", "recognised", "removed"),
    [
        (KDCA_VIEW.format("3796３"), LOW_BACK, LOW_BACK),  # 전각 문자는 숫자가 아니다 — 모으면 3796
        (KDCA_VIEW.format("３3796"), LOW_BACK, LOW_BACK),
        # 앞 정수 31773 뒤의 비숫자 — 빼는 쪽은 앞 정수로 치열 문서다. 인정해 주는 쪽은 값 전체가
        # ASCII 숫자여야 한다(#185 1차 리뷰 후속: 서버는 `7３`에 500을 준다).
        (AMC_DETAIL.format("31773３"), None, ANAL_FISSURE),
        (AMC_DETAIL.format("３31773"), None, None),  # 앞이 숫자가 아니다
        (KDCA_VIEW.format("3７96"), None, None),  # 396 — 목록에 없는 문서
    ],
    ids=["kdca_trailing_fullwidth", "kdca_leading_fullwidth", "amc_trailing_fullwidth", "amc_leading_fullwidth", "kdca_396"],
)
def test_only_ascii_digits_are_read(url, recognised, removed):
    if recognised is None:
        assert curated_source_entries(url) == []
    else:
        assert curated_source_entries(url) == curated_source_entries(recognised) != []
    assert is_curated_source_url(url) is (removed is not None)


def test_a_fullwidth_id_never_equals_a_catalog_or_excluded_document():
    """목록·제외 목록의 모든 id를 전각으로 바꿔도 어떤 항목과도 같지 않다."""

    from app.utils.authority_sources import reference_exclusion_reason

    fullwidth = str.maketrans("0123456789", "０１２３４５６７８９")
    for url in [*CURATED_SOURCE_URLS, *(entry["url"] for entry in REFERENCE_URL_EXCLUSIONS)]:
        base, _, query = url.partition("?")
        if not query:
            continue
        variant = f"{base}?{query.translate(fullwidth)}"
        assert not is_curated_source_url(variant), variant
        assert reference_exclusion_reason(variant) is None, variant


@pytest.mark.parametrize("outcome", PAGE_OUTCOMES)
@pytest.mark.parametrize("name", FULLWIDTH_IDS)
async def test_a_medical_post_citing_a_fullwidth_id_is_judged_by_its_page(name, outcome):
    url, canonical = FULLWIDTH[name]
    title, _topic = FULLWIDTH_MEDICAL_TITLE_FOR[canonical]
    fetcher = _fullwidth_fetcher(url, canonical, outcome)

    result = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[title], defer_transient=True
    )

    assert fetcher.calls == [url]
    (check,) = result.checks
    _assert_not_curated_check(check)
    kept = [ref["url"] for ref in result.kept]
    if outcome == "real_page":
        assert kept == [url] and check["reason"] == "page_verified"
    elif outcome == "empty_template":
        assert kept == [] and result.deferred == []
    else:
        assert kept == [] and [ref["url"] for ref in result.deferred] == [url]


@pytest.mark.parametrize("outcome", PAGE_OUTCOMES)
@pytest.mark.parametrize("name", FULLWIDTH_IDS)
async def test_generation_judges_a_fullwidth_id_on_a_cost_title_by_its_page(name, outcome):
    url, canonical = FULLWIDTH[name]
    title, topic = FULLWIDTH_COST_TITLE_FOR[canonical]
    fetcher = _fullwidth_fetcher(url, canonical, outcome)
    result = {
        "title": title,
        "body": f"## {topic}의 원인과 치료\n{topic} 진료 안내\n## 비용\n본문",
        "faq_question": None,
        "references": [{"title": topic, "url": url}],
    }

    with override_reference_fetcher(fetcher):
        notes = await content_engine._verify_generated_references(
            result, {"target_keyword": topic}, required=True
        )

    assert fetcher.calls == [url]  # 목록 문서처럼 GET 전에 빼지 않는다
    assert not any("수기 목록 문서를 쓰지 않음" in note for note in notes)
    (check,) = [c for c in result["reference_checks"] if c["url"] == url]
    _assert_not_curated_check(check)
    kept = [ref["url"] for ref in result["references"]]
    if outcome == "real_page":
        assert kept == [url] and check["reason"] == "page_verified"
    else:
        assert kept == []  # 실제 본문 통과 없이는 남지 않는다(목록 밖 판단 불가는 제거)


@pytest.mark.parametrize("outcome", PAGE_OUTCOMES)
@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
@pytest.mark.parametrize("name", FULLWIDTH_IDS)
async def test_publication_refresh_judges_a_fullwidth_id_on_a_cost_post_by_its_page(
    name, status, outcome
):
    url, canonical = FULLWIDTH[name]
    title, topic = FULLWIDTH_COST_TITLE_FOR[canonical]
    item = _published(title, status)
    item.body = f"## {topic}의 원인과 치료\n{topic} 진료 안내"
    item.references_list = [{"title": topic, "url": url}]
    item.reference_checks = None
    fetcher = _fullwidth_fetcher(url, canonical, outcome)

    assert not publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert fetcher.calls == [url]  # GET 전에 목록 문서로 빠지지 않는다
    (check,) = [c for c in refresh.checks if c["url"] == url]
    _assert_not_curated_check(check)
    kept = [ref["url"] for ref in refresh.references]
    if outcome == "real_page":
        assert kept == [url] and not refresh.operator_decides
        assert check["reason"] == "page_verified"
    elif outcome == "empty_template":
        assert kept == [] and refresh.operator_decides and not refresh.healed
    else:
        # 목록 밖 주소의 일시 장애 — 남기지도 빼지도 않고 다음 시간대로 미룬다(통과가 아니다).
        assert refresh.deferred and not publication_references_settled(item)


@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
@pytest.mark.parametrize("name", FULLWIDTH_IDS)
async def test_a_stored_page_pass_keeps_a_fullwidth_id_on_a_cost_post(name, status):
    """실제 본문 통과 기록이 있으면(목록 밖 판정) 진료비 글에 남는다 — 다시 열지 않는다."""

    url, canonical = FULLWIDTH[name]
    title, topic = FULLWIDTH_COST_TITLE_FOR[canonical]
    item = _published(title, status)
    item.body = f"## {topic}의 원인과 치료\n{topic} 진료 안내"
    item.references_list = [{"title": topic, "url": url}]
    item.reference_checks = [_page_pass(url)]
    _stamp(item)
    fetcher = _fullwidth_fetcher(url, canonical, "site_down")

    assert publication_references_settled(item)
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.already_current and [ref["url"] for ref in refresh.references] == [url]
    assert fetcher.calls == []


@pytest.mark.parametrize("outcome", PAGE_OUTCOMES)
@pytest.mark.parametrize("name", FULLWIDTH_IDS)
async def test_patch_judges_a_fullwidth_id_on_a_cost_post_by_its_page(monkeypatch, name, outcome):
    url, canonical = FULLWIDTH[name]
    title, topic = FULLWIDTH_COST_TITLE_FOR[canonical]
    hospital, item = _patch_setup(monkeypatch, title=title)
    fetcher = _fullwidth_fetcher(url, canonical, outcome)

    with override_reference_fetcher(fetcher):
        if outcome == "real_page":
            await _patch(hospital, item, references=[{"title": topic, "url": url}])
        else:
            with pytest.raises(HTTPException) as raised:
                await _patch(hospital, item, references=[{"title": topic, "url": url}])

    assert fetcher.calls == [url]  # 422(목록 문서) 없이 실제 GET으로 판정한다
    if outcome == "real_page":
        assert [ref["url"] for ref in item.references_list] == [url]
        (check,) = item.reference_checks
        assert check["reason"] == "page_verified" and check["curated"] is False
    else:
        assert raised.value.status_code == 400
        assert raised.value.detail.get("code") != "CURATED_REFERENCE_NOT_ALLOWED"
        (failure,) = raised.value.detail["failed_references"]
        assert failure["url"] == url
        assert failure["reason"] not in {"curated_verified", "curated_unreachable"}
        assert item.references_list == [] and item.content_revision == 3


# ── 목록 문서로 인정할 때 앞 정수 이름은 ASCII 숫자만인 값(#185 1차 리뷰 후속) ──────────────
# `thtimt_cntnts_sn`·AMC `contentId`·`SEQ`·`cancer_seq`는 앞 정수로 읽는다. 목록 문서라서 **빼는**
# 판정은 그 관대한 해석 그대로다(`7a`도 7번 문서일 수 있다). 목록 문서로 **인정해 주는** 판정
# (카탈로그 주제 대조·장애 시 유지·검증기의 `curated`)은 값 전체가 ASCII 숫자일 때만이다 — 서버는
# `7３`에 500을 준다. 인정받지 못한 주소는 의료 글에서 실제 본문으로 판정된다.

CHECKUP_VIEW = (
    "https://health.kdca.go.kr/healthinfo/biz/health/ntcnInfo/healthSourc/thtimtCntnts/"
    "thtimtCntntsView.do?thtimt_cntnts_sn={}"
)
# 이름 → (URL 틀, 목록 id, 의료 제목, 진료비 제목, 주제어)
LEADING_INTEGER_DOCS = {
    "thtimt_cntnts_sn": (
        CHECKUP_VIEW,
        7,
        "건강검진 결과지 읽는 법 — 수치별 의미",
        "건강검진 비용 — 항목별 본인부담",
        "건강검진",
    ),
    "amc_contentId": (
        AMC_DETAIL,
        31773,
        "치열이 생겼을 때 — 원인과 치료",
        "치열 수술 비용 — 보험 적용과 본인부담",
        "치열",
    ),
}
# 값 → 퍼센트 인코딩된 질의 값(`parse_qsl`이 풀어 읽는다).
INEXACT_ID_VALUES = {
    "fullwidth_suffix": "{}%EF%BC%93",  # 7３
    "letter_suffix": "{}a",  # 7a
    "encoded_plus": "%2B{}",  # +7
    "encoded_space": "%20{}",  # ' 7'
    "raw_plus": "+{}",  # 질의의 `+`는 공백 — ' 7'
}
EXACT_ID_VALUES = {"plain": "{}", "zero_padded": "00{}"}
INEXACT_CASES = sorted((doc, value) for doc in LEADING_INTEGER_DOCS for value in INEXACT_ID_VALUES)


def _leading_integer_url(doc: str, value: str) -> tuple[str, str]:
    template, number, *_rest = LEADING_INTEGER_DOCS[doc]
    pattern = {**INEXACT_ID_VALUES, **EXACT_ID_VALUES}[value]
    return template.format(pattern.format(number)), template.format(number)


@pytest.mark.parametrize(("doc", "value"), INEXACT_CASES)
def test_an_inexact_leading_integer_id_is_not_recognised_but_still_removed(doc, value):
    from app.services.reference_verification import _serves_curated_document, names_curated_document

    url, canonical = _leading_integer_url(doc, value)
    assert curated_source_entries(canonical) != []
    assert curated_source_entries(url) == []  # 인정해 주는 쪽
    assert not _serves_curated_document(url)
    assert is_curated_source_url(url)  # 빼는 쪽 — 관대한 앞 정수 해석
    assert names_curated_document({"url": url})


@pytest.mark.parametrize("value", sorted(EXACT_ID_VALUES))
@pytest.mark.parametrize("doc", sorted(LEADING_INTEGER_DOCS))
def test_an_exact_leading_integer_id_is_recognised(doc, value):
    url, canonical = _leading_integer_url(doc, value)
    assert curated_source_entries(url) == curated_source_entries(canonical) != []
    assert is_curated_source_url(url)


@pytest.mark.parametrize("outcome", ["real_page", "site_down"])
@pytest.mark.parametrize(("doc", "value"), INEXACT_CASES)
async def test_a_medical_post_citing_an_inexact_leading_integer_id_is_judged_by_its_page(
    doc, value, outcome
):
    url, _canonical = _leading_integer_url(doc, value)
    _template, _number, medical_title, _cost_title, topic = LEADING_INTEGER_DOCS[doc]
    fetcher = PageFetcher()
    if outcome == "real_page":
        fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    else:
        fetcher.pages[url] = TimeoutError("site down")

    result = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[medical_title], defer_transient=True
    )

    assert fetcher.calls == [url]
    (check,) = result.checks
    _assert_not_curated_check(check)
    if outcome == "real_page":
        assert [ref["url"] for ref in result.kept] == [url] and check["reason"] == "page_verified"
    else:
        # 목록 문서로 인정되던 때는 `curated_unreachable`로 남았다 — 이제는 미룬다.
        assert result.kept == [] and [ref["url"] for ref in result.deferred] == [url]


@pytest.mark.parametrize("value", sorted(EXACT_ID_VALUES))
@pytest.mark.parametrize("doc", sorted(LEADING_INTEGER_DOCS))
async def test_a_medical_post_citing_an_exact_leading_integer_id_keeps_the_catalog(doc, value):
    url, _canonical = _leading_integer_url(doc, value)
    _template, _number, medical_title, _cost_title, _topic = LEADING_INTEGER_DOCS[doc]
    fetcher = PageFetcher({url: TimeoutError("site down")})

    result = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[medical_title], defer_transient=True
    )

    (check,) = result.checks
    assert check["reason"] == "curated_unreachable" and check["curated"] is True
    assert [ref["url"] for ref in result.kept] == [url]


@pytest.mark.parametrize(("doc", "value"), INEXACT_CASES)
async def test_generation_still_drops_an_inexact_leading_integer_id_on_a_cost_title(doc, value):
    url, _canonical = _leading_integer_url(doc, value)
    _template, _number, _medical_title, cost_title, topic = LEADING_INTEGER_DOCS[doc]
    fetcher = PageFetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    result = {
        "title": cost_title,
        "body": f"## {topic}의 원인과 치료\n{topic} 진료 안내\n## 비용\n본문",
        "faq_question": None,
        "references": [{"title": topic, "url": url}],
    }

    with override_reference_fetcher(fetcher):
        notes = await content_engine._verify_generated_references(
            result, {"target_keyword": topic}, required=True
        )

    assert result["references"] == [] and fetcher.calls == []
    assert any("수기 목록 문서를 쓰지 않음" in note for note in notes)


@pytest.mark.parametrize("status", [tasks.ContentStatus.DRAFT, tasks.ContentStatus.READY])
@pytest.mark.parametrize(("doc", "value"), INEXACT_CASES)
async def test_publication_refresh_still_drops_an_inexact_leading_integer_id_on_a_cost_post(
    doc, value, status
):
    url, _canonical = _leading_integer_url(doc, value)
    _template, _number, _medical_title, cost_title, topic = LEADING_INTEGER_DOCS[doc]
    item = _published(cost_title, status)
    item.body = f"## {topic}의 원인과 치료\n{topic} 진료 안내"
    item.references_list = [{"title": topic, "url": url}]
    item.reference_checks = None
    fetcher = PageFetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.operator_decides and fetcher.calls == []


@pytest.mark.parametrize(("doc", "value"), INEXACT_CASES)
async def test_patch_still_rejects_an_inexact_leading_integer_id_on_a_cost_post(
    monkeypatch, doc, value
):
    url, _canonical = _leading_integer_url(doc, value)
    _template, _number, _medical_title, cost_title, topic = LEADING_INTEGER_DOCS[doc]
    hospital, item = _patch_setup(monkeypatch, title=cost_title)
    fetcher = PageFetcher()

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(hospital, item, references=[{"title": topic, "url": url}])

    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "CURATED_REFERENCE_NOT_ALLOWED"
    assert fetcher.calls == [] and item.references_list == []
