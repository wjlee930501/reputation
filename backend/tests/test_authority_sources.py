import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.utils.authority_sources import (  # noqa: E402
    SOURCE_TYPE_GOV_KR,
    infer_source_type,
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
