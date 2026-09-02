"""report.html 렌더링 회귀 — repeat_count 동적 표기 + 측정 데이터 없음(None) 표기 (결함 2, 3)."""
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import doctor_report_artifact
from app.services.report_artifact_validation import (
    DoctorArtifactMetadata,
    DoctorPdfValidationError,
    ValidatedDoctorPdf,
)
from app.services.report_engine import (
    TEMPLATE_DIR,
    _successful_measurement,
    build_strategy_summary,
    report_pdf_filename,
)


def test_report_confirmation_excludes_incomplete_success_and_keeps_legacy_boolean():
    incomplete = SimpleNamespace(
        measurement_status="SUCCESS", mention_verdict=None, is_mentioned=None
    )
    legacy = SimpleNamespace(is_mentioned=False)

    assert _successful_measurement(incomplete) is False
    assert _successful_measurement(legacy) is True


def _render(**overrides) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    template = env.get_template("report.html")
    hospital = SimpleNamespace(
        name="장편한외과의원",
        region=["강남"],
        specialties=["대장항문외과"],
        plan="PLAN_16",
    )
    ctx = dict(
        hospital=hospital,
        report_type="V0",
        period_label="V0-진단",
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 8),
        sov_pct=12.5,
        sov_measured=True,
        published_count=0,
        repeat_count=5,
        generated_at=datetime(2026, 7, 8),
    )
    ctx.update(overrides)
    return template.render(**ctx)


def test_monthly_ae_pdf_filename_preserves_report_versions():
    first = report_pdf_filename(
        "jangpyeonhan",
        "2026-07",
        report_type="MONTHLY",
        report_version=1,
    )
    second = report_pdf_filename(
        "jangpyeonhan",
        "2026-07",
        report_type="MONTHLY",
        report_version=2,
    )

    assert first == "jangpyeonhan_2026-07_v1.pdf"
    assert second == "jangpyeonhan_2026-07_v2.pdf"
    assert first != second


def test_report_renders_actual_repeat_count_not_hardcoded_ten():
    html = _render(repeat_count=7)
    assert "7회 반복" in html
    assert "10회 반복" not in html


def test_report_renders_no_data_when_sov_unmeasured():
    html = _render(sov_pct=None, sov_measured=False)
    assert "측정 데이터 없음" in html
    # None을 %.1f 로 포매팅하려다 터지지 않아야 한다.
    assert "0.0%" not in html


def test_report_renders_percentage_when_measured():
    html = _render(sov_pct=42.0, sov_measured=True)
    assert "42.0%" in html


def test_strategy_summary_connects_targets_platform_sov_gaps_and_actions():
    target_id = "target-1"
    target = SimpleNamespace(
        id=target_id,
        name="강남 치질 수술 추천",
        priority="HIGH",
        status="ACTIVE",
        platforms=["CHATGPT", "GEMINI"],
        variants=[],
    )
    records = [
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=True,
            source_urls=["https://example.com/a"],
            competitor_mentions=[{"name": "경쟁병원", "is_mentioned": True}],
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q2",
            ai_platform="gemini",
            measurement_status="SUCCESS",
            is_mentioned=False,
            source_urls=[],
            competitor_mentions=[{"name": "경쟁병원", "is_mentioned": False}],
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q2",
            ai_platform="gemini",
            measurement_status="FAILED",
            is_mentioned=False,
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
    ]
    gap = SimpleNamespace(
        id="gap-1",
        query_target_id=target_id,
        gap_type="LOW_MENTION_SHARE",
        severity="HIGH",
        status="OPEN",
        evidence={"mention_rate": 50.0},
        query_target=target,
    )
    completed = SimpleNamespace(
        id="done-1",
        query_target_id=target_id,
        title="FAQ 콘텐츠 발행",
        description="환자 질문에 답하는 FAQ를 발행했습니다.",
        status="COMPLETED",
        completed_at=datetime(2026, 7, 20),
        due_month="2026-07",
        owner="AE",
        action_type="CONTENT",
        query_target=target,
        gap=gap,
        linked_content=SimpleNamespace(id="content-1", title="치질 수술 FAQ"),
    )
    next_action = SimpleNamespace(
        id="next-1",
        query_target_id=target_id,
        title="공식 근거 자료 보강",
        description="공식 페이지의 병원명과 진료 정보를 정리합니다.",
        status="OPEN",
        completed_at=None,
        due_month="2026-08",
        owner="MotionLabs Ops",
        action_type="SOURCE",
        query_target=target,
        gap=gap,
        linked_content=None,
    )

    summary = build_strategy_summary(
        hospital=SimpleNamespace(
            website_url="https://example.com",
            blog_url=None,
            kakao_channel_url=None,
            google_business_profile_url=None,
            google_maps_url=None,
            naver_place_url=None,
            aeo_domain="clinic.example.kr",
        ),
        query_targets=[target],
        sov_records=records,
        exposure_gaps=[gap],
        exposure_actions=[completed, next_action],
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 31, 23, 59, 59),
        next_month="2026-08",
    )

    outcome = summary["query_targets"][0]
    assert outcome["sov_pct"] == 50.0
    assert outcome["platform_sov"] == {"chatgpt": 100.0, "gemini": 0.0}
    assert outcome["source_backed_count"] == 1
    assert outcome["owned_source_count"] == 1
    assert outcome["owned_source_url_count"] == 1
    assert outcome["owned_citation_share_pct"] == 100.0
    assert outcome["competitor_outcomes"] == [{
        "name": "경쟁병원",
        "observed_count": 2,
        "mention_count": 1,
        "mention_pct": 50.0,
    }]
    assert outcome["successful_measurement_count"] == 2
    assert summary["exposure_gaps"][0]["gap_type"] == "LOW_MENTION_SHARE"
    assert summary["completed_actions"][0]["linked_content_title"] == "치질 수술 FAQ"
    assert summary["next_month_actions"][0]["title"] == "공식 근거 자료 보강"
    assert summary["compliance_caveat"]


def test_strategy_summary_excludes_action_completed_exactly_at_period_end():
    period_end = datetime(2026, 8, 1)
    action = SimpleNamespace(
        status="COMPLETED", completed_at=period_end, query_target_id=None
    )

    summary = build_strategy_summary(
        hospital=SimpleNamespace(),
        query_targets=[],
        sov_records=[],
        exposure_gaps=[],
        exposure_actions=[action],
        period_start=datetime(2026, 7, 1),
        period_end=period_end,
        next_month="2026-08",
    )

    assert summary["completed_actions"] == []


def test_monthly_report_renders_data_driven_strategy_instead_of_generic_recommendations():
    strategy = {
        "query_targets": [{
            "name": "강남 치질 수술 추천",
            "priority": "HIGH",
            "sov_pct": 50.0,
            "platform_sov": {"chatgpt": 100.0, "gemini": 0.0},
            "source_backed_count": 1,
            "owned_source_count": 1,
            "source_url_count": 2,
            "owned_citation_share_pct": 50.0,
            "successful_measurement_count": 2,
        }],
        "exposure_gaps": [{
            "query_target_name": "강남 치질 수술 추천",
            "gap_type": "LOW_MENTION_SHARE",
            "severity": "HIGH",
        }],
        "completed_actions": [{
            "title": "FAQ 콘텐츠 발행",
            "query_target_name": "강남 치질 수술 추천",
            "linked_content_title": "치질 수술 FAQ",
        }],
        "next_month": "2026-08",
        "next_month_actions": [{
            "title": "공식 근거 자료 보강",
            "description": "공식 페이지의 병원 정보를 정리합니다.",
            "query_target_name": "강남 치질 수술 추천",
            "owner": "MotionLabs Ops",
            "due_month": "2026-08",
        }],
        "compliance_caveat": "의료광고 관련 검수 후 실행합니다.",
    }

    html = _render(report_type="MONTHLY", strategy=strategy, attribution=None)

    assert "월간 AI 노출 콘텐츠 운영 리포트" in html
    assert "환자 질문 목표별 AI 노출 결과" in html
    assert "강남 치질 수술 추천" in html
    assert "ChatGPT 100.0%" in html
    assert "병원 공식 채널 인용" in html
    assert "수집 URL 중 50.0%" in html
    assert "LOW_MENTION_SHARE" in html  # legacy payload without display label remains readable
    assert "FAQ 콘텐츠 발행" in html
    assert "공식 근거 자료 보강" in html
    assert "의료광고 관련 검수 후 실행합니다." in html
    assert "리뷰 수집 캠페인 실행" not in html


def test_monthly_report_renders_the_articles_the_ai_actually_cited():
    citations = {
        "measured_cell_count": 30,
        "cited_cell_count": 4,
        "cited_cell_pct": 13.3,
        "content_cited_cell_count": 3,
        "hub_cited_cell_count": 1,
        "cited_content_count": 1,
        "cited_items": [{
            "content_id": "c1",
            "title": "치질 수술 FAQ",
            "content_type": "FAQ",
            "cited_cell_count": 3,
            "cited_url_count": 1,
            "queries": [{"query_text": "강남 치질 병원 추천해줘", "platform_label": "ChatGPT"}],
        }],
        "hub_pages": [{
            "page_key": "home",
            "label": "병원 홈",
            "cited_cell_count": 1,
            "queries": [{"query_text": "강남 항문외과 어디가 좋아?", "platform_label": "Gemini"}],
        }],
    }

    html = _render(report_type="MONTHLY", strategy=None, attribution=None, citations=citations)

    assert "AI가 인용한 우리 글" in html
    assert "치질 수술 FAQ" in html
    assert "강남 치질 병원 추천해줘 · ChatGPT" in html
    assert "인용된 병원 정보 페이지" in html
    assert "병원 홈" in html


def test_legacy_reports_without_citations_still_render():
    """`citations` 키가 없던 과거 리포트를 다시 렌더해도 섹션만 빠지고 깨지지 않는다."""
    html = _render(report_type="MONTHLY", strategy=None, attribution=None)

    assert "AI가 인용한 우리 글" not in html
    assert "월간 AI 노출 콘텐츠 운영 리포트" in html


def test_strategy_summary_uses_canonical_confirmation_not_raw_response_presence():
    target = SimpleNamespace(
        id="target-1",
        name="강남 치질 수술 추천",
        priority="HIGH",
        status="ACTIVE",
        platforms=["CHATGPT"],
        variants=[],
    )
    records = [
        SimpleNamespace(
            ai_query_target_id=target.id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="장편한외과 언급",
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target.id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=False,
            raw_response="",
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
    ]

    summary = build_strategy_summary(
        query_targets=[target],
        sov_records=records,
        exposure_gaps=[],
        exposure_actions=[],
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 31, 23, 59, 59),
        next_month="2026-08",
    )

    assert summary["query_targets"][0]["sov_pct"] == 50.0
    assert summary["query_targets"][0]["failed_measurement_count"] == 0


@pytest.mark.parametrize("failure_mode", ["mkdir", "write", "hash", "upload"])
def test_doctor_pdf_storage_failures_are_typed_and_remove_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    pdf_bytes = b"validated-pdf-bytes"
    digest = sha256(pdf_bytes).hexdigest()
    metadata = DoctorArtifactMetadata(
        validation_version="doctor-pdf-v1",
        validation_source="SYSTEM",
        page_count=1,
        page_size="A4",
        glyph_count=10,
        font_family="Pretendard",
        font_embedded=True,
        korean_to_unicode=True,
        link_count=1,
        expected_link_present=True,
        required_text_present=True,
        sha256=digest,
        byte_size=len(pdf_bytes),
    )
    rendered = ValidatedDoctorPdf(pdf_bytes, digest, len(pdf_bytes), metadata)
    monkeypatch.setattr(doctor_report_artifact.settings, "REPORT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        doctor_report_artifact, "render_validated_doctor_pdf", lambda **_kwargs: rendered
    )
    if failure_mode == "mkdir":
        monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    elif failure_mode == "write":
        monkeypatch.setattr(Path, "write_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    elif failure_mode == "hash":
        monkeypatch.setattr(Path, "read_bytes", lambda *_args, **_kwargs: b"mutated")
    else:
        monkeypatch.setattr(
            doctor_report_artifact,
            "_upload_to_gcs",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()),
        )

    hospital = SimpleNamespace(slug="jangpyeonhan")
    with pytest.raises(DoctorPdfValidationError) as exc:
        doctor_report_artifact.generate_doctor_pdf_report(
            hospital,
            uuid.UUID("d2410000-0000-0000-0000-000000000001"),
            datetime(2026, 7, 1),
            {
                "hospital_name": "장편한외과의원",
                "coverage_text": "측정 범위 안내",
            },
            "https://reputation.motionlabs.kr/jangpyeonhan",
        )

    assert exc.value.code == "DOCTOR_PDF_STORAGE_FAILED"
    assert not (
        tmp_path
        / "jangpyeonhan_2026-07_doctor_d2410000-0000-0000-0000-000000000001.pdf"
    ).exists()


def test_rebuilt_doctor_artifacts_keep_distinct_immutable_paths_and_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_id = uuid.UUID("d2410000-0000-0000-0000-000000000011")
    second_id = uuid.UUID("d2410000-0000-0000-0000-000000000012")
    rendered_items: list[ValidatedDoctorPdf] = []
    for body in (b"validated-v1", b"validated-v2"):
        digest = sha256(body).hexdigest()
        metadata = DoctorArtifactMetadata(
            validation_version="doctor-pdf-v1",
            validation_source="SYSTEM",
            page_count=1,
            page_size="A4",
            glyph_count=10,
            font_family="Pretendard",
            font_embedded=True,
            korean_to_unicode=True,
            link_count=1,
            expected_link_present=True,
            required_text_present=True,
            sha256=digest,
            byte_size=len(body),
        )
        rendered_items.append(ValidatedDoctorPdf(body, digest, len(body), metadata))
    monkeypatch.setattr(doctor_report_artifact.settings, "REPORT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        doctor_report_artifact,
        "render_validated_doctor_pdf",
        lambda **_kwargs: rendered_items.pop(0),
    )
    monkeypatch.setattr(
        doctor_report_artifact,
        "_upload_to_gcs",
        lambda local_path, *_args: str(local_path),
    )
    hospital = SimpleNamespace(slug="jangpyeonhan")
    view = {"hospital_name": "장편한외과의원", "coverage_text": "측정 범위 안내"}

    first = doctor_report_artifact.generate_doctor_pdf_report(
        hospital,
        first_id,
        datetime(2026, 7, 1),
        view,
        "https://reputation.motionlabs.kr/jangpyeonhan",
    )
    first_bytes = Path(first.path).read_bytes()
    second = doctor_report_artifact.generate_doctor_pdf_report(
        hospital,
        second_id,
        datetime(2026, 7, 1),
        view,
        "https://reputation.motionlabs.kr/jangpyeonhan",
    )

    assert first.path != second.path
    assert first.sha256 != second.sha256
    assert Path(first.path).read_bytes() == first_bytes == b"validated-v1"
    assert Path(second.path).read_bytes() == b"validated-v2"
    assert sha256(Path(first.path).read_bytes()).hexdigest() == first.sha256
    assert sha256(Path(second.path).read_bytes()).hexdigest() == second.sha256


# ── 자사 인용(owned source) 매칭 경계 ──────────────────────────────────


def _hospital_with(**overrides):
    fields = {
        "website_url": None,
        "blog_url": None,
        "kakao_channel_url": None,
        "google_business_profile_url": None,
        "google_maps_url": None,
        "naver_place_url": None,
        "aeo_domain": None,
        "slug": "jangpyeonhan",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_a_pathless_shared_host_never_claims_the_whole_host():
    """`blog_url`이 경로 없이 저장되면 남의 네이버 블로그가 우리 인용이 된다."""
    from app.services.report_engine import _matches_owned_source, _owned_source_roots

    roots = _owned_source_roots(_hospital_with(blog_url="https://blog.naver.com"))

    assert not _matches_owned_source("https://blog.naver.com/other-clinic/123", roots)
    assert not _matches_owned_source("https://blog.naver.com/", roots)


def test_an_owned_shared_host_path_still_matches_its_own_posts():
    from app.services.report_engine import _matches_owned_source, _owned_source_roots

    roots = _owned_source_roots(_hospital_with(blog_url="https://blog.naver.com/jangpyeonhan"))

    assert _matches_owned_source("https://blog.naver.com/jangpyeonhan/223", roots)
    assert _matches_owned_source("https://m.blog.naver.com/jangpyeonhan", roots) is False
    assert not _matches_owned_source("https://blog.naver.com/other-clinic/223", roots)


def test_owned_matching_normalizes_encoding_and_duplicate_slashes_like_citations():
    """인용 URL 정규화와 같은 규칙을 써야 한글 경로가 owned에서 빠지지 않는다."""
    from app.services.report_engine import _matches_owned_source, _owned_source_roots

    roots = _owned_source_roots(_hospital_with(website_url="https://clinic.example.kr/진료안내/"))

    assert _matches_owned_source(
        "https://clinic.example.kr//%EC%A7%84%EB%A3%8C%EC%95%88%EB%82%B4/%EB%8C%80%EC%9E%A5", roots
    )


def test_a_private_host_without_a_path_still_owns_its_whole_domain():
    from app.services.report_engine import _matches_owned_source, _owned_source_roots

    roots = _owned_source_roots(_hospital_with(website_url="https://clinic.example.kr"))

    assert _matches_owned_source("https://www.clinic.example.kr/doctor", roots)
