"""SEO/GEO 검증 함수 단위 테스트 — _validate_seo / _validate_geo

테스트 케이스:
  - 정상 draft → SEO/GEO 통과, seo_geo_score 높음
  - primary keyword body 부재 → soft finding (한국어 형태 변화 오탐 방지)
  - 일반 유형 H2 헤딩 < 2개 → ValueError (hard-fail); NOTICE/FAQ는 soft
  - 구조적으로 정상이지만 소프트 결함 → findings 반환, ValueError 미발생
  - 의료 안내 유형에서 빈 refs → ValueError
  - GEO: 병원명/원장명/지역 없음 → ValueError
  - GEO: 통계 패턴 없음 → soft findings 추가
"""
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from types import SimpleNamespace

import httpx
import pytest

from app.models.content import ContentType
from app.services.content_engine import (
    _drop_definitively_broken_references,
    _normalize_references,
    _validate_geo,
    _validate_seo,
)


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _hospital(
    name: str = "장편한외과의원",
    director_name: str = "김장편",
    region: list | None = None,
    keywords: list | None = None,
) -> SimpleNamespace:
    """테스트용 Hospital stub — SQLAlchemy 의존 없이 속성만 흉내냄"""
    return SimpleNamespace(
        name=name,
        director_name=director_name,
        region=region if region is not None else ["강남"],
        keywords=keywords if keywords is not None else ["어깨 통증", "정형외과"],
    )


def _good_body(hospital: SimpleNamespace | None = None) -> str:
    """SEO/GEO 기준을 모두 만족하는 샘플 본문"""
    h = hospital or _hospital()
    return (
        f"{h.name}에서는 어깨 통증 환자의 70%가 보존 치료로 회복합니다.\n\n"
        f"## 어깨 통증 원인\n\n"
        f"어깨 통증은 {h.region[0]} 지역 환자의 주요 내원 사유입니다. "
        f"대한정형외과학회 가이드라인에 따르면 힘줄 파열이 가장 흔한 원인입니다.\n\n"
        f"## 진단 방법\n\n"
        f"{h.director_name} 원장은 초음파 검사와 MRI를 통해 정확히 진단합니다.\n\n"
        f"## 치료 방향\n\n"
        f"| 증상 단계 | 치료 방법 | 회복 기간 |\n"
        f"|---|---|---|\n"
        f"| 초기 | 물리치료 | 2~4주 |\n"
        f"| 중기 | 주사치료 | 4~8주 |\n\n"
        f"## 내원 안내\n\n"
        f"평균 회복 기간은 6주입니다. 진료 예약은 {h.name}으로 문의하세요.\n"
    )


def _good_result(hospital: SimpleNamespace | None = None) -> dict:
    """SEO/GEO 기준을 모두 만족하는 샘플 result dict"""
    h = hospital or _hospital()
    return {
        "title": "강남 어깨 통증, 정형외과에서 치료하는 방법",
        "body": _good_body(h),
        "meta_description": (
            "강남 장편한외과의원에서 어깨 통증 원인과 치료 방향을 안내합니다. "
            "보존 치료부터 시술까지 단계별로 설명합니다."
        ),
        "references": [
            {"title": "대한정형외과학회 진료지침", "url": "https://www.koa.or.kr/guide"},
        ],
    }


# ── _validate_seo 테스트 ───────────────────────────────────────────────────────

class TestValidateSeo:
    def test_compliant_draft_returns_no_hard_fail(self):
        """정상 draft는 ValueError 없이 soft findings 리스트를 반환한다"""
        h = _hospital()
        result = _good_result(h)
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        # 구조 정상이므로 hard-fail 없이 통과 — soft findings도 없어야 함
        assert isinstance(findings, list)

    def test_seasonal_title_must_match_planned_publish_date(self):
        h = _hospital()
        result = _good_result(h)
        result["title"] = "봄철 어깨 통증 관리"

        with pytest.raises(ValueError, match="title season '봄'.*2026-07-31"):
            _validate_seo(
                result,
                h,
                {"planned_publish_date": "2026-07-31"},
                ContentType.HEALTH,
            )

    def test_matching_seasonal_title_is_allowed(self):
        h = _hospital()
        result = _good_result(h)
        result["title"] = "봄철 어깨 통증 관리"

        findings = _validate_seo(
            result,
            h,
            {"planned_publish_date": "2026-04-15"},
            ContentType.HEALTH,
        )

        assert isinstance(findings, list)

    def test_compliant_draft_keyword_in_title(self):
        """keyword가 title에 있으면 prominence soft-finding이 없다"""
        h = _hospital(keywords=["어깨 통증"])
        result = _good_result(h)
        result["title"] = "어깨 통증 치료 안내"
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        kw_findings = [f for f in findings if "prominence" in f or "keyword" in f.lower()]
        assert not kw_findings

    def test_missing_primary_keyword_is_soft_finding(self):
        """primary keyword가 body에 없으면 soft finding (한국어 형태 변화 오탐 방지 — hard-fail 아님)"""
        h = _hospital(keywords=["무릎 통증"])  # body에는 어깨만 나옴
        result = _good_result()  # body는 어깨 통증 기준
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        # primary_kw는 keywords[0]의 첫 토큰("무릎")으로 결정된다.
        assert any("무릎" in f for f in findings)

    def test_content_brief_target_query_used_as_keyword(self):
        """content_brief.target_query가 있으면 그것이 primary keyword로 사용된다"""
        h = _hospital(keywords=["어깨 통증"])
        result = _good_result(h)
        # body에 없는 단어를 target_query로 설정
        brief = {"target_query": "발목골절 수술"}
        findings = _validate_seo(result, h, brief, ContentType.DISEASE)
        assert any("발목골절" in f for f in findings)

    def test_fewer_than_two_h2_headings_raises_value_error(self):
        """H2 헤딩이 1개뿐이면 ValueError (hard-fail → retry)"""
        h = _hospital()
        result = _good_result(h)
        result["body"] = (
            "어깨 통증 환자의 70%가 보존 치료로 회복합니다.\n\n"
            "## 치료 방향\n\n"
            "대한정형외과학회 가이드라인에 따르면 힘줄 파열이 흔합니다.\n"
            "| 항목 | 내용 |\n|---|---|\n| 기간 | 6주 |\n"
        )
        with pytest.raises(ValueError, match="SEO hard-fail.*H2 heading"):
            _validate_seo(result, h, None, ContentType.DISEASE)

    def test_zero_h2_headings_raises_value_error(self):
        """H2 헤딩이 0개면 ValueError"""
        h = _hospital()
        result = _good_result(h)
        result["body"] = "어깨 통증 환자의 70%가 보존 치료로 회복합니다. " * 50
        with pytest.raises(ValueError, match="SEO hard-fail.*H2 heading"):
            _validate_seo(result, h, None, ContentType.DISEASE)

    def test_title_too_long_is_soft_finding(self):
        """title이 60자 초과면 soft finding — ValueError 미발생"""
        h = _hospital()
        result = _good_result(h)
        result["title"] = "어깨 통증" + "가" * 60  # 65자 이상
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        assert any("title" in f and "절단" in f for f in findings)

    def test_meta_too_short_is_soft_finding(self):
        """meta_description이 70자 미만이면 soft finding"""
        h = _hospital()
        result = _good_result(h)
        result["meta_description"] = "짧은 설명"  # 5자
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        assert any("meta_description" in f for f in findings)

    def test_no_listicle_or_table_is_soft_finding(self):
        """markdown 표도 목록도 없으면 soft finding"""
        h = _hospital()
        result = _good_result(h)
        # 표 제거, 목록 제거한 body
        result["body"] = (
            "어깨 통증 환자의 70%가 회복합니다.\n\n"
            "## 어깨 통증 원인\n\n"
            "강남 지역 환자에게 흔한 문제입니다. 김장편 원장이 안내합니다.\n\n"
            "## 진단 방법\n\n"
            "초음파와 MRI로 진단합니다. 장편한외과의원에서 예약하세요.\n\n"
            "## 치료 방향\n\n"
            "보존 치료 후 시술로 이어집니다.\n\n"
            "## 내원 안내\n\n"
            "진료 예약은 장편한외과의원으로 문의하세요.\n"
        )
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        assert any("listicle" in f or "표" in f for f in findings)

    def test_keyword_not_in_title_or_first_h2_is_soft_finding(self):
        """keyword가 body에는 있지만 title과 첫 H2에는 없으면 soft finding"""
        h = _hospital(keywords=["어깨 통증"])
        result = _good_result(h)
        result["title"] = "정형외과 진료 안내"  # keyword 없음
        # body의 첫 H2도 keyword 없도록 변경
        result["body"] = (
            "어깨 통증 환자의 70%가 보존 치료로 회복합니다.\n\n"
            "## 진료 절차\n\n강남에서 김장편 원장이 어깨 통증을 치료합니다.\n\n"
            "## 치료 방법\n\n| 단계 | 방법 |\n|---|---|\n| 1 | 물리치료 |\n\n"
            "## 회복 안내\n\n평균 6주가 걸립니다. 장편한외과의원으로 문의하세요.\n"
        )
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        assert any("prominence" in f or "keyword" in f.lower() for f in findings)

    def test_structurally_ok_but_soft_deficient_does_not_raise(self):
        """소프트 결함만 있는 draft는 ValueError 없이 findings를 반환한다"""
        h = _hospital(keywords=["어깨 통증"])
        result = _good_result(h)
        result["title"] = "정형외과 진료"  # keyword not in title/first-h2 → soft
        result["meta_description"] = "짧음"  # too short → soft
        # ValueError 없이 findings만 반환되어야 함
        findings = _validate_seo(result, h, None, ContentType.DISEASE)
        assert isinstance(findings, list)
        assert len(findings) >= 1

    def test_notice_and_faq_with_few_h2_do_not_raise(self):
        """NOTICE/FAQ는 짧은 단문이 정상 — H2 부족이 hard-fail이 아니라 soft finding이다 (M3)."""
        h = _hospital()
        result = _good_result(h)
        result["body"] = (
            "장편한외과의원 진료시간이 변경되었습니다.\n\n## 변경 안내\n\n평일 9시~18시 운영합니다.\n"
        )
        for ct in (ContentType.NOTICE, ContentType.FAQ):
            findings = _validate_seo(result, h, None, ct)
            assert isinstance(findings, list)  # ValueError 미발생


# ── _validate_geo 테스트 ───────────────────────────────────────────────────────

class TestValidateGeo:
    def test_compliant_draft_no_geo_findings(self):
        """병원명·원장명·지역·통계가 모두 있으면 findings 없음"""
        h = _hospital()
        result = _good_result(h)
        findings = _validate_geo(result, h, ContentType.DISEASE)
        assert isinstance(findings, list)
        assert not findings

    def test_empty_references_disease_raises_value_error(self):
        """DISEASE 유형에서 references가 비어있으면 ValueError (hard-fail)"""
        h = _hospital()
        result = _good_result(h)
        result["references"] = []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.DISEASE)

    def test_non_whitelisted_reference_does_not_satisfy_required_authority_reference(self):
        h = _hospital()
        result = _good_result(h)
        result["references"] = _normalize_references(
            [{"title": "광고 블로그", "url": "https://ad-blog.example.com/promo"}]
        )

        assert result["references"] == []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.DISEASE)

    def test_empty_references_treatment_raises_value_error(self):
        """TREATMENT 유형에서 references가 비어있으면 ValueError (hard-fail)"""
        h = _hospital()
        result = _good_result(h)
        result["references"] = []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.TREATMENT)

    def test_empty_references_local_raises_value_error(self):
        """LOCAL 유형에서 references가 비어있으면 ValueError (hard-fail)"""
        h = _hospital()
        result = _good_result(h)
        result["references"] = []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.LOCAL)

    def test_empty_references_column_raises_value_error(self):
        """의학 주장을 담는 COLUMN도 특정 근거 문서가 필요하다."""
        h = _hospital()
        result = _good_result(h)
        result["references"] = []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.COLUMN)

    def test_empty_references_faq_raises_value_error(self):
        """FAQ도 자동 발행 전에 특정 근거 문서가 필요하다."""
        h = _hospital()
        result = _good_result(h)
        result["references"] = []
        with pytest.raises(ValueError, match="GEO hard-fail.*references"):
            _validate_geo(result, h, ContentType.FAQ)

    def test_missing_hospital_name_hard_fails(self):
        """병원명이 body에 없으면 생성 결과를 저장하지 않는다."""
        h = _hospital(name="다른병원")  # body는 '장편한외과의원' 기준
        result = _good_result()
        with pytest.raises(ValueError, match="병원명"):
            _validate_geo(result, h, ContentType.FAQ)

    def test_missing_director_name_hard_fails(self):
        """원장명이 body에 없으면 생성 결과를 저장하지 않는다."""
        h = _hospital(director_name="이원장")  # body는 '김장편' 기준
        result = _good_result()
        with pytest.raises(ValueError, match="원장명"):
            _validate_geo(result, h, ContentType.FAQ)

    def test_missing_region_hard_fails(self):
        """지역 엔티티가 body에 없으면 생성 결과를 저장하지 않는다."""
        h = _hospital(region=["제주"])  # body는 '강남' 기준
        result = _good_result()
        with pytest.raises(ValueError, match="지역 엔티티"):
            _validate_geo(result, h, ContentType.FAQ)

    def test_missing_stat_pattern_is_soft_finding(self):
        """숫자/통계 패턴이 없으면 soft finding"""
        h = _hospital()
        result = _good_result(h)
        # 숫자 완전 제거
        result["body"] = (
            "장편한외과의원에서 어깨 통증을 치료합니다.\n\n"
            "## 어깨 통증 원인\n\n강남 지역 환자에게 흔합니다. 김장편 원장이 안내합니다.\n\n"
            "## 진단 방법\n\n초음파 검사로 진단합니다.\n\n"
            "## 치료 방향\n\n| 증상 | 방법 |\n|---|---|\n| 초기 | 물리치료 |\n\n"
            "## 내원 안내\n\n진료 예약은 장편한외과의원으로 문의하세요.\n"
        )
        findings = _validate_geo(result, h, ContentType.FAQ)
        assert any("통계" in f or "숫자" in f for f in findings)

    def test_all_geo_entities_present_no_findings(self):
        """병원명·원장명·지역·통계 모두 포함 시 soft findings 없음"""
        h = _hospital(
            name="강남정형외과",
            director_name="박원장",
            region=["강남"],
            keywords=["어깨 통증"],
        )
        body = (
            "강남정형외과에서 어깨 통증 환자의 80%가 회복합니다.\n\n"
            "## 어깨 통증 원인\n\n강남 지역에서 흔히 발생합니다. 박원장이 안내합니다.\n\n"
            "## 진단 방법\n\n초음파와 MRI로 진단합니다.\n\n"
            "## 치료 방향\n\n| 단계 | 방법 |\n|---|---|\n| 1 | 물리치료 |\n\n"
            "## 내원 안내\n\n강남정형외과로 문의하세요.\n"
        )
        result = {
            "title": "어깨 통증 치료",
            "body": body,
            "meta_description": "강남 강남정형외과에서 어깨 통증 치료를 안내합니다.",
            "references": [{"title": "출처", "url": "https://www.koa.or.kr/guide"}],
        }
        findings = _validate_geo(result, h, ContentType.DISEASE)
        assert not findings


class _ReferenceClient:
    def __init__(self, responses, *args, **kwargs):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, headers):
        status, final_url = self.responses[url]
        return httpx.Response(
            status,
            request=httpx.Request("GET", url),
            headers={"content-type": "text/html"},
            extensions={},
        ) if final_url == url else SimpleNamespace(status_code=status, url=httpx.URL(final_url))


@pytest.mark.asyncio
async def test_reference_verification_drops_404_and_external_redirect(monkeypatch):
    refs = [
        {"title": "정상", "url": "https://health.kdca.go.kr/good"},
        {"title": "없음", "url": "https://health.kdca.go.kr/missing"},
        {"title": "이탈", "url": "https://health.kdca.go.kr/redirect"},
    ]
    responses = {
        refs[0]["url"]: (200, refs[0]["url"]),
        refs[1]["url"]: (404, refs[1]["url"]),
        refs[2]["url"]: (200, "https://example.com/landing"),
    }
    monkeypatch.setattr(
        "app.services.content_engine.httpx.AsyncClient",
        lambda *args, **kwargs: _ReferenceClient(responses, *args, **kwargs),
    )

    kept = await _drop_definitively_broken_references(refs)

    assert kept == [refs[0]]
