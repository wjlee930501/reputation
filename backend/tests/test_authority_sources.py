import os

import pytest

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.utils.authority_sources import (  # noqa: E402
    SOURCE_TYPE_CLINIC,
    SOURCE_TYPE_GOV_GLOBAL,
    SOURCE_TYPE_GOV_KR,
    infer_source_type,
    is_citable_reference_url,
    is_whitelisted_url,
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


def test_select_curated_authority_sources_supports_orthopedic_faq_content():
    sources = select_curated_authority_sources(
        "노원구 정형외과 병원 선택 기준 — 통증 종류별 진단·치료 항목 비교",
    )

    assert [source["url"].rsplit("=", 1)[-1] for source in sources] == [
        "3796",
        "5969",
        "3348",
    ]


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
