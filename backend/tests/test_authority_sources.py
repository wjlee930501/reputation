import os

import pytest

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")

from app.services.reference_verification import curated_sources_for_topic  # noqa: E402
from app.utils.authority_sources import (  # noqa: E402
    CURATED_MEDICAL_SOURCE_PAGES,
    SOURCE_TYPE_CLINIC,
    SOURCE_TYPE_GOV_GLOBAL,
    SOURCE_TYPE_GOV_KR,
    infer_source_type,
    is_citable_reference_url,
    is_whitelisted_url,
    render_source_hint_block,
    select_curated_authority_sources,
)


def test_is_whitelisted_url_accepts_exact_domain():
    assert is_whitelisted_url("https://kdca.go.kr/notice") is True


def test_is_whitelisted_url_accepts_subdomain():
    assert is_whitelisted_url("https://health.kdca.go.kr/portal") is True


def test_is_whitelisted_url_rejects_spoofed_suffix_domain():
    # 회귀 가드: 'kdca.go.kr.evil.com' 은 hostname이 evil.com이지 kdca.go.kr이 아니다.
    # 과거 문자열 포함 검사('.{domain} in lowered')는 이 스푸핑 도메인을 통과시켰다.
    assert is_whitelisted_url("https://kdca.go.kr.evil.com/notice") is False


def test_is_whitelisted_url_rejects_domain_embedded_in_path():
    # 문자열 포함 검사라면 path에 도메인이 등장해도 통과했을 수 있다.
    assert is_whitelisted_url("https://evil.com/kdca.go.kr/notice") is False


def test_is_whitelisted_url_rejects_lookalike_domain():
    assert is_whitelisted_url("https://notkdca.go.kr/notice") is False


def test_is_whitelisted_url_rejects_empty_url():
    assert is_whitelisted_url("") is False


def test_infer_source_type_returns_none_for_spoofed_domain():
    assert infer_source_type("https://kdca.go.kr.evil.com/notice") is None


def test_infer_source_type_returns_expected_type_for_exact_domain():
    assert infer_source_type("https://www.kdca.go.kr/notice") == SOURCE_TYPE_GOV_KR


def test_citable_reference_gate_rejects_home_roots_but_accepts_curated_document():
    assert is_citable_reference_url("https://health.kdca.go.kr") is False
    assert is_citable_reference_url("https://www.kdca.go.kr/") is False
    assert is_citable_reference_url("https://www.hira.or.kr/") is False
    assert is_citable_reference_url("https://www.koa.or.kr/") is False
    assert is_citable_reference_url("https://www.kosem.or.kr") is False
    assert is_citable_reference_url("https://law.go.kr") is False
    assert (
        is_citable_reference_url(
            "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
            "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5463"
        )
        is True
    )


def test_select_curated_authority_sources_returns_topic_specific_document_pages():
    sources = select_curated_authority_sources(
        "수원 대장내시경 장정결과 대장용종 절제 안내",
        limit=3,
    )

    assert [source["url"] for source in sources] == [
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5254",
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6531",
    ]
    assert all(source["source_type"] == SOURCE_TYPE_GOV_KR for source in sources)


def test_select_curated_authority_sources_does_not_guess_for_unknown_topic():
    assert select_curated_authority_sources("알 수 없는 새 진료 주제") == []


def test_select_curated_authority_sources_supports_cardiovascular_topics():
    sources = select_curated_authority_sources(
        "고혈압과 허혈성 심장질환, 뇌졸중 위험 안내",
        limit=3,
    )

    assert [source["url"].rsplit("=", 1)[-1] for source in sources] == [
        "6765",
        "6566",
        "5495",
    ]
    assert all(source["source_type"] == SOURCE_TYPE_GOV_KR for source in sources)


def test_select_curated_authority_sources_does_not_route_on_a_specialty_name():
    """진료과 이름('심장내과')은 주제가 아니다 — 고혈압·협심증·뇌졸중 문서를 붙이지 않는다.

    예전에는 이 이름만으로 세 문서를 골랐다. 진료과 이름만 겹친 글에 특정 질환 문서를 붙이면
    가짜 근거다(2026-09-29 팀장 결정, `keyword_names_provider`). 질환 이름이 있으면 그대로 고른다.
    """
    assert select_curated_authority_sources("심장내과 순환기 질환 안내", limit=3) == []
    assert [
        source["url"].rsplit("=", 1)[-1]
        for source in select_curated_authority_sources("심장내과 고혈압 관리 안내", limit=3)
    ] == ["6765"]


def test_select_curated_authority_sources_supports_dehydration_content():
    sources = select_curated_authority_sources(
        "소아 발열이 이어질 때 탈수 징후와 수분 보충 방법",
    )

    assert [source["url"] for source in sources] == [
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5285",
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6551",
    ]
    assert all(source["source_type"] == SOURCE_TYPE_GOV_KR for source in sources)


def test_select_curated_authority_sources_supports_pediatric_fever():
    sources = select_curated_authority_sources("소아 발열 치료 비용")

    assert sources[0]["url"].endswith("cntnts_sn=5285")


def test_select_curated_authority_sources_supports_breast_ultrasound():
    sources = select_curated_authority_sources("유방초음파 검사 비용")

    assert sources == [
        {
            "title": "국립암센터 — 국가암검진 검진주기 및 검진방법",
            "url": "https://edu.cancer.go.kr/lay1/S1T553C555/contents.do",
            "source_type": SOURCE_TYPE_GOV_KR,
        }
    ]


def test_select_curated_authority_sources_supports_general_health_screening():
    sources = select_curated_authority_sources("건강검진 비용과 검사 항목")

    assert sources[0]["url"].startswith("https://www.nhis.or.kr/")
    assert sources[0]["source_type"] == SOURCE_TYPE_GOV_KR


def test_select_curated_authority_sources_supports_trauma_emergency_content():
    sources = select_curated_authority_sources(
        "경증 응급·외상 처치와 골절 평가, 상처 봉합",
    )

    assert [source["url"].rsplit("=", 1)[-1] for source in sources] == [
        "5463",
        "5679",
        "5696",
    ]
    assert all("?cntnts_sn=" in source["url"] for source in sources)
    assert all(source["source_type"] == SOURCE_TYPE_GOV_KR for source in sources)


def test_select_curated_authority_sources_does_not_route_a_clinic_choice_faq():
    """정형외과·병원선택·통증종류(병원 고르기 경로 키워드)만 겹친 글은 문서를 받지 않는다.

    예전에는 요통 3796·무릎 5969·디스크 3348을 골랐다 — 병원 고르기를 뒷받침하지 않는 가짜 근거다.
    허리통증·디스크 같은 질환 이름이 있으면 그 문서만 고른다.
    """
    assert (
        select_curated_authority_sources(
            "노원구 정형외과 병원 선택 기준 — 통증 종류별 진단·치료 항목 비교",
        )
        == []
    )
    assert [
        source["url"].rsplit("=", 1)[-1]
        for source in select_curated_authority_sources(
            "노원구 정형외과 병원 선택 기준 — 허리디스크 통증 종류별 진단·치료 항목 비교",
        )
    ] == ["3796", "3348"]


def test_select_curated_authority_sources_supports_spine_joint_pain_query():
    sources = select_curated_authority_sources(
        "척추 관절 통증 진료를 받으려는데 노원구 어느 병원으로 가야 해?",
    )

    assert [source["url"].rsplit("=", 1)[-1] for source in sources] == [
        "3796",
        "3348",
    ]
    assert all(source["source_type"] == SOURCE_TYPE_GOV_KR for source in sources)


def test_select_curated_authority_sources_supports_eswt_query():
    sources = select_curated_authority_sources(
        "상계동 체외충격파 치료 가능한 병원 추천해줘",
    )

    assert sources == [
        {
            "title": (
                "Extracorporeal shock wave therapy is effective in treating chronic "
                "plantar fasciitis: A meta-analysis of RCTs"
            ),
            "url": "https://pubmed.ncbi.nlm.nih.gov/28403111/",
            "source_type": SOURCE_TYPE_GOV_GLOBAL,
        },
        {
            "title": (
                "The evolving use of extracorporeal shock wave therapy in managing "
                "musculoskeletal and neurological diagnoses"
            ),
            "url": (
                "https://www.mayoclinic.org/medical-professionals/"
                "physical-medicine-rehabilitation/news/"
                "the-evolving-use-of-extracorporeal-shock-wave-therapy-in-managing-"
                "musculoskeletal-and-neurological-diagnoses/mac-20527246"
            ),
            "source_type": SOURCE_TYPE_CLINIC,
        },
    ]


def test_orthopedic_faq_documents_are_citable_but_koa_homepage_is_not():
    urls = [
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3796",
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5969",
        "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3348",
    ]

    assert all(is_citable_reference_url(url) for url in urls)
    assert is_citable_reference_url("https://www.koa.or.kr") is False
    assert is_citable_reference_url("https://www.koa.or.kr/") is False


@pytest.mark.parametrize(
    "focus",
    (
        "환자 상태에 따라 유방초음파 검사 계획을 안내합니다",
        "환자 상태에 따라 항문 열상(치열)과 치핵 치료를 안내합니다",
    ),
)
def test_select_curated_authority_sources_does_not_misclassify_patient_status_as_trauma(
    focus,
):
    sources = select_curated_authority_sources(focus)

    trauma_document_ids = {"5463", "5679", "5696"}
    selected_document_ids = {source["url"].rsplit("=", 1)[-1] for source in sources}
    assert selected_document_ids.isdisjoint(trauma_document_ids)


def test_source_hint_block_prefers_verified_documents_and_forbids_guessing():
    """확신 없는 URL을 '다른 문서로 바꿔 넣으라'는 압력이 문서 번호 추측을 낳았다(2026-09-29).

    검증된 목록 URL을 그대로 쓰게 하고, 확실하지 않은 항목은 빼게 한다. 비면 시스템이
    검증된 목록으로 채우거나 발행을 보류한다 — 작가가 지어낼 이유가 없다.
    """
    block = render_source_hint_block()

    assert "그 URL을 그대로" in block
    assert "추측해 URL을 지어내지 마세요" in block
    assert "확실하지 않은 항목은 빼세요" in block
    for pressure in (
        "최소 1개",
        "빈 references는 저장되지 않습니다",
        "확신이 있는 다른 문서로 바꿔 넣으세요",
        "references가 비면 글 전체가 저장되지 않습니다",
    ):
        assert pressure not in block


def test_source_hint_block_is_byte_stable_for_the_prompt_cache():
    """정적 시스템 블록의 접두어라 호출마다 같아야 한다(시각·UUID 금지)."""
    assert render_source_hint_block() == render_source_hint_block()


@pytest.mark.parametrize(
    "topic",
    ["위내시경 전 확인할 점", "어깨 통증 진료 안내", "손목 통증과 저림", "무릎 관절 주사 치료"],
)
def test_generic_words_do_not_pull_an_unrelated_curated_document(topic):
    """'내시경'·'통증'·'관절'만으로 대장내시경·요통·디스크 문서를 붙이지 않는다(점검 후속)."""
    urls = [source["url"] for source in select_curated_authority_sources(topic, limit=10)]

    assert not any(
        marker in url for url in urls for marker in ("cntnts_sn=3796", "cntnts_sn=3348")
    )
    colonoscopy = [
        entry["url"]
        for entry in CURATED_MEDICAL_SOURCE_PAGES
        if "대장내시경" in str(entry["title"])
    ]
    assert not set(colonoscopy) & set(urls)


def test_specific_spine_and_colonoscopy_words_still_select_their_documents():
    spine = [s["url"] for s in select_curated_authority_sources("허리 디스크와 요통 관리", limit=10)]
    assert any("cntnts_sn=3796" in url for url in spine)
    assert any("cntnts_sn=3348" in url for url in spine)
    colon = select_curated_authority_sources("대장내시경 전 장정결 방법")
    assert colon


_LOW_BACK = "cntnts_sn=3796"
_DISC = "cntnts_sn=3348"


@pytest.mark.parametrize(
    "title",
    [
        "도수치료 후 회복 기간, 얼마나 걸리나요?",
        "허리디스크 초기 증상",
        "허리·다리 저림, 어느 과로 가야 하나요?",
    ],
)
def test_manual_therapy_and_lumbar_disc_titles_select_low_back_and_disc_documents(title):
    """'도수치료'·'허리디스크'·'허리다리'는 요통(3796)·추간판탈출증(3348) 문서를 붙인다
    (2026-09-29 보류 재생: 도수치료 4편·허리 1편이 이 두 문서로 치유된다)."""
    urls = [s["url"] for s in select_curated_authority_sources(title, limit=50)]
    topic_urls = [s["url"] for s in curated_sources_for_topic([title], limit=50)]

    for picked in (urls, topic_urls):
        assert any(_LOW_BACK in url for url in picked), picked
        assert any(_DISC in url for url in picked), picked


@pytest.mark.parametrize(
    "title",
    [
        "대사증후군 진단 기준 — 허리둘레·혈압·혈당",  # '허리' ⊂ 허리둘레
        "허리둘레 줄이는 운동과 대사증후군",
        "두통 빈도수가 늘었다면 확인할 것",  # '도수' ⊂ 빈도수
        "알코올 도수와 간 건강",  # 알코올 도수
        "안경 도수 맞추기",
        "어깨 통증 원인",
        "손목 통증 진료",
        "무릎 통증 치료",
    ],
)
def test_short_waist_or_degree_words_do_not_pull_low_back_or_disc_documents(title):
    """선택은 공백·구두점을 지운 부분 문자열 비교다 — 단독 '허리'·'도수'나 '통증'만으로는
    요통·디스크 문서를 붙이지 않는다."""
    urls = [s["url"] for s in select_curated_authority_sources(title, limit=50)]
    topic_urls = [s["url"] for s in curated_sources_for_topic([title], limit=50)]

    for picked in (urls, topic_urls):
        assert not any(marker in url for url in picked for marker in (_LOW_BACK, _DISC)), picked


def test_low_back_and_disc_keywords_have_no_bare_waist_or_degree_word():
    for marker in (_LOW_BACK, _DISC):
        entry = next(e for e in CURATED_MEDICAL_SOURCE_PAGES if str(e["url"]).endswith(marker))
        keywords = set(entry["keywords"])
        assert {"도수치료", "허리디스크", "허리다리"} <= keywords
        assert not keywords & {"허리", "도수", "통증", "관절"}


@pytest.mark.parametrize(
    "title,expected",
    [
        ("대상포진 예방접종 대상과 시기", {"002024.htm", "cntnts_sn=6679"}),
        ("독감(인플루엔자) 예방접종, 언제 맞아야 하나요?", {"002024.htm", "cntnts_sn=5232"}),
        ("자궁경부암 백신(HPV) 접종 안내", {"cntnts_sn=3987"}),
        ("B형간염 보유자, 정기 검사는 얼마나 자주 받아야 하나요?", {"cntnts_sn=6672"}),
        ("간경화(간경변) 진단 후 관리와 추적검사", {"cntnts_sn=6560", "contentId=30480"}),
        (
            "하남시 위례 간질환 환자 진료 흐름 — 초음파·혈액검사·추적",
            {"cntnts_sn=6553", "cntnts_sn=6560", "contentId=31687"},
        ),
        ("PRP 자가혈 재생치료 비용", {"platelet-rich-plasma-prp-injection"}),
        ("위례 수액치료 병원 — 개인 상태 평가 후 맞춤형 처방", {"21635-iv-fluids"}),
        ("대구 동구 CT 검사, 신기한속내과연합의원에서 가능합니다", {"ctscans.html"}),
        ("마산 골밀도검사, 병원 방문 전 내과에서 먼저 확인할 것들", {"osteoporosis.html"}),
    ],
)
def test_held_topic_groups_select_their_new_documents(title, expected):
    urls = [s["url"] for s in select_curated_authority_sources(title, limit=50)]

    for marker in expected:
        assert any(url.endswith(marker) for url in urls), (marker, urls)


@pytest.mark.parametrize(
    "title",
    [
        "치질 수술 후 회복 기간 — 질환과 생활 관리",  # '간질환' ⊂ 기간질환
        "고압산소치료 회복 기간 — 질환별 차이",
        "주사 치료 전 의사(doctor)와 상담할 것",  # 'ct' ⊂ doctor·injection
        "어깨 통증 원인",
    ],
)
def test_new_documents_do_not_attach_to_unrelated_titles(title):
    new_markers = (
        "cntnts_sn=6553",
        "cntnts_sn=6560",
        "contentId=31687",
        "ctscans.html",
        "platelet-rich-plasma-prp-injection",
    )
    urls = [s["url"] for s in select_curated_authority_sources(title, limit=50)]

    assert not any(url.endswith(marker) for url in urls for marker in new_markers), urls
