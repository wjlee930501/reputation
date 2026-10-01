"""수기 목록 문서의 별칭·리다이렉트도 수기 목록 문서다(PR #177 리뷰 3차 F1).

같은 문서를 가리키는 주소 표기(`&utm_source=x`·`:443`·`cntnts_sn=03796`·MedlinePlus `?from=x`)가
목록 밖 URL로 판정되면, 제목이 그 문서의 주제어를 품은 진료비 글("요통 도수치료 비용")에서 실제
GET으로 통과해 남았다. 발행 전 진료비·병원 선택 글의 수기 목록 문서 금지(2026-09-29 실장 결정)를
표기만 바꿔 비껴 간 것이다. 이제 동일성 키(`authority_sources.curated_document_keys`)로 판정하고,
GET의 최종 주소가 목록 문서면(리다이렉트) 그 주소도 목록 문서로 본다.

4차 리뷰: 실제 KDCA 서버는 id 값의 앞 정수를 읽고(`3796abc`·`3796%2B`), 목록 항목이 쓰지 않는
id 이름(`contentId`·`SEQ`·`thtimt_cntnts_sn`)은 무시하며, 같은 이름이 반복되면 그 가운데 한 값을
쓴다. 그래서 동일성 키 대신 목록 항목별 판정(`authority_sources._matching_documents`)으로 본다 —
위치가 같고 항목 URL 자신의 id 이름마다 앞 정수가 같은 값이 하나라도 있으면 그 항목이다.

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
    "repeated_other_first": (KDCA_VIEW.format("1") + "&cntnts_sn=3796", LOW_BACK),
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
        canonical = next((c for a, c in ALIASES.values() if a == url), url)
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
        KDCA_VIEW.format("%EF%BC%93%EF%BC%97%EF%BC%99%EF%BC%96"),  # 전각 ３７９６
        KDCA_VIEW.format("0" * 5000 + "3796"),
        LOW_BACK.replace("cntnts_sn=", "cntnts%5Fsn="),
        LOW_BACK.replace("https://", "https://user:pw@"),
    ],
    ids=[
        "explicit_80",
        "http_443",
        "www_fragment",
        "jsessionid",
        "repeated_same_id",
        "full_width_digits",
        "many_leading_zeros",
        "percent_encoded_name",
        "userinfo",
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


def test_a_repeated_id_naming_two_catalog_documents_matches_both():
    """서버가 어느 값을 쓰는지 모르면 두 항목 모두다 — 목록 문서이고, 어느 쪽 주제와도 대조된다.

    (3차에는 '목록 문서지만 항목 없음'이라 의료 글에서 주제 불일치로 빠졌다.)
    """

    both = LOW_BACK + "&cntnts_sn=6765"
    reversed_order = KDCA_VIEW.format(6765) + "&cntnts_sn=3796"
    (low_back,) = curated_source_entries(LOW_BACK)
    for url in (both, reversed_order):
        assert is_curated_source_url(url)
        assert sorted(entry["url"] for entry in curated_source_entries(url)) == sorted(
            [low_back["url"], _OTHER_KDCA_SOURCE["url"]]
        )


@pytest.mark.parametrize(
    "title",
    ["요통이 오래갈 때 — 원인과 치료", "고혈압 약을 먹기 시작할 때 — 생활 관리"],
    ids=["first_entry_topic", "second_entry_topic"],
)
async def test_a_medical_post_citing_a_two_document_url_matches_either_topic(title):
    url = LOW_BACK + "&cntnts_sn=6765"
    fetcher = PageFetcher()
    fetcher.add_document(url, "요통 | 국가건강정보포털 | 질병관리청", topic="요통")

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=[title]
    )

    (check,) = outcome.checks
    assert check["reason"] == "curated_verified" and check["curated"] is True


def test_the_exclusion_list_uses_the_same_document_matcher():
    """제외 목록도 별칭으로 비껴 가지 못한다 — 목록 문서와 같은 판정이다."""

    from app.utils.authority_sources import reference_exclusion_reason

    excluded = REFERENCE_URL_EXCLUSIONS[0]["url"]  # KDCA cntnts_sn=6263
    assert excluded.endswith("cntnts_sn=6263")
    for alias in (
        excluded.replace("6263", "06263"),
        excluded.replace("6263", "6263abc"),
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


@pytest.mark.parametrize("name", ALIAS_IDS)
async def test_generation_drops_a_curated_alias_cited_on_a_cost_title(name):
    alias, canonical = ALIASES[name]
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
@pytest.mark.parametrize("name", ALIAS_IDS)
async def test_publication_refresh_drops_a_curated_alias_on_a_cost_post(name, status, fresh):
    alias, canonical = ALIASES[name]
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
@pytest.mark.parametrize("name", ALIAS_IDS)
async def test_patch_rejects_a_curated_alias_before_any_get(monkeypatch, name, status):
    alias, canonical = ALIASES[name]
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
