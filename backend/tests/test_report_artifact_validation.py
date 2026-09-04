from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app.services.report_artifact_validation import (
    DOCTOR_ARTIFACT_VALIDATION_VERSION,
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    parse_doctor_artifact_metadata,
    render_validated_doctor_pdf,
    validate_doctor_pdf,
)
from app.workers.monthly_artifact_reconciliation import _artifact_is_valid


def _blank_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595.28, height=841.89)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _expectation(*, appendix_expected: bool = False) -> DoctorPdfExpectation:
    return DoctorPdfExpectation(
        hospital_name="장편한외과의원",
        coverage_text="측정 범위: 챗GPT, 제미나이에서 계획한 답변 20개 중 20개를 확인했습니다.",
        caveat_text="이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        public_url="https://reputation.motionlabs.kr/jangpyeonhan",
        appendix_expected=appendix_expected,
    )


def test_validator_rejects_a_two_page_artifact_when_no_appendix_was_rendered() -> None:
    """부록이 없는데 2쪽이면 본문이 넘친 것이다 — 그 파일은 원장에게 나가면 안 된다."""
    with pytest.raises(DoctorPdfValidationError) as exc:
        validate_doctor_pdf(_blank_pdf(2), _expectation())

    assert exc.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"
    assert "1쪽" in exc.value.problem
    assert "원장님께 전달" in exc.value.customer_impact
    assert "리포트 다시 만들기" in exc.value.next_action


def test_validator_rejects_a_one_page_artifact_when_the_appendix_was_expected() -> None:
    """부록 행이 있는데 1쪽이면 표가 통째로 사라진 것이다."""
    with pytest.raises(DoctorPdfValidationError) as exc:
        validate_doctor_pdf(_blank_pdf(1), _expectation(appendix_expected=True))

    assert exc.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"
    assert "2쪽" in exc.value.problem


@pytest.mark.parametrize("pages", [3, 4])
def test_validator_never_allows_more_than_the_appendix_page(pages: int) -> None:
    with pytest.raises(DoctorPdfValidationError) as exc:
        validate_doctor_pdf(_blank_pdf(pages), _expectation(appendix_expected=True))

    assert exc.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"


def test_persisted_metadata_parser_fails_closed_for_incomplete_or_old_shapes() -> None:
    assert parse_doctor_artifact_metadata(None) is None
    assert parse_doctor_artifact_metadata({"page_count": 1, "glyph_count": 840}) is None
    assert (
        parse_doctor_artifact_metadata(
            {
                "validation_version": DOCTOR_ARTIFACT_VALIDATION_VERSION,
                "validation_source": "SYSTEM",
                "page_count": 3,
                "page_size": "A4",
                "glyph_count": 840,
                "font_family": "Pretendard",
                "font_embedded": True,
                "korean_to_unicode": True,
                "link_count": 1,
                "expected_link_present": True,
                "required_text_present": True,
            }
        )
        is None
    )


def test_reconciler_uses_closed_two_page_metadata_contract() -> None:
    metadata = {
        "validation_version": DOCTOR_ARTIFACT_VALIDATION_VERSION,
        "validation_source": "SYSTEM",
        "page_count": 2,
        "page_size": "A4",
        "glyph_count": 840,
        "font_family": "Pretendard",
        "font_embedded": True,
        "korean_to_unicode": True,
        "link_count": 1,
        "expected_link_present": True,
        "required_text_present": True,
        "sha256": "a" * 64,
        "byte_size": 4096,
    }
    report = SimpleNamespace(doctor_pdf_path="gs://private/doctor.pdf")
    artifact = SimpleNamespace(
        validated=True,
        path=report.doctor_pdf_path,
        sha256=metadata["sha256"],
        byte_size=metadata["byte_size"],
        validation_metadata=metadata,
    )

    assert _artifact_is_valid(report, artifact)
    artifact.validation_metadata = {**metadata, "page_count": "2"}
    assert not _artifact_is_valid(report, artifact)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://operator:secret@reputation.motionlabs.kr/hospital",
        "https://reputation.motionlabs.kr/hospital#internal",
        "https://reputation.motionlabs.kr/hos\npital",
        "https:///missing-host",
    ],
)
def test_renderer_rejects_unsafe_public_links_before_creating_an_annotation(
    unsafe_url: str,
) -> None:
    expectation = DoctorPdfExpectation(
        hospital_name="장편한외과의원",
        coverage_text="측정 범위 안내",
        caveat_text="이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        public_url=unsafe_url,
    )

    with pytest.raises(DoctorPdfValidationError) as exc:
        render_validated_doctor_pdf(
            view={},
            period_label="2026-07",
            public_url=unsafe_url,
            expectation=expectation,
        )

    assert exc.value.code == "DOCTOR_PDF_PUBLIC_URL_INVALID"
    assert "안전한 병원 공개 주소" in exc.value.problem


@pytest.mark.skipif(
    os.getenv("REQUIRE_PDF_RENDER") is None,
    reason="WeasyPrint 네이티브 의존성이 필요하다. CI에서 REQUIRE_PDF_RENDER=1로 강제한다.",
)
@pytest.mark.parametrize("fixture_name", ["minimum", "typical", "maximum", "appendix"])
def test_real_doctor_artifact_is_a4_with_korean_font_text_and_link(
    fixture_name: str,
) -> None:
    long = "환자분이 이해하기 쉬운 말로 검사 과정과 준비 방법을 안내합니다. " * 9
    evidence = {
        "minimum": {"found": None, "missing": None},
        "typical": {
            "found": {
                "question": "강남 대장내시경 병원",
                "excerpt": "장편한외과의원에서 검사 전 준비 방법을 확인할 수 있습니다.",
                "platform": "챗GPT",
                "measured_at": None,
                "competitors": [],
            },
            "missing": None,
        },
        "maximum": {
            "found": {
                "question": "강남 대장내시경 병원을 선택할 때 무엇을 확인해야 하나요?",
                "excerpt": long,
                "platform": "챗GPT",
                "measured_at": None,
                "competitors": [],
            },
            "missing": {
                "question": "치질 진료를 받을 병원을 고를 때 무엇을 확인해야 하나요?",
                "excerpt": long,
                "platform": "제미나이",
                "measured_at": None,
                "competitors": ["가나다외과의원", "편안한외과의원", "서울외과의원"],
            },
        },
        "appendix": {
            "found": {
                "question": "강남 대장내시경 병원을 선택할 때 무엇을 확인해야 하나요?",
                "excerpt": long,
                "platform": "챗GPT",
                "measured_at": None,
                "competitors": [],
            },
            "missing": None,
        },
    }[fixture_name]
    appendix_rows = [
        {
            "query_text": f"강남에서 치질 수술을 잘하는 병원을 추천해 주세요 {index}",
            "prev_label": "10번 중 4번",
            "current_label": "10번 중 6번",
            "competitor": "가나다외과의원",
            "cited_title": "치질 수술 FAQ",
        }
        for index in range(15)
    ] if fixture_name == "appendix" else []
    coverage_text = (
        "측정 범위: 챗GPT, 제미나이에서 계획한 답변 20개 중 20개를 확인했습니다."
    )
    caveat_text = "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다."
    view = {
        "measured": True,
        "hospital_name": "장편한외과의원",
        "headline": {"of_hundred": 47, "delta_sentence": "전월보다 8번 늘었습니다."},
        "summary": "환자 질문 100번 중 AI가 병원을 답변에 넣은 횟수는 47번입니다.",
        "coverage_text": coverage_text,
        "tiles": [
            {"label": "이번 달 발행한 글", "value": "16편 중 12편", "hint": "약정 편수 기준"},
            {"label": "새로 확인된 병원 언급", "value": "3개", "hint": "같은 조건 기준"},
            {"label": "자주 나온 다른 병원", "value": "가나다외과의원", "hint": "같은 질문 기준"},
        ],
        "published_items": [
            {"title": "치질 수술 뒤 회복 기간 안내", "cited": True},
            {"title": "대장내시경 검사 전날 준비 안내", "cited": False},
        ],
        "citation_line": "AI 답변이 저희 병원 글·페이지를 인용한 횟수: 4건(확인한 답변 30개 중)",
        "lost_mention_sentences": [
            {"query_text": "치질 수술 후 회복 기간이 얼마나 되나요", "platform_label": "ChatGPT"},
        ],
        "v0_baseline": {
            "of_hundred": 31,
            "current_of_hundred": 47,
            "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
        },
        "appendix_rows": appendix_rows,
        "evidence": evidence,
        "next_actions": {
            "ours": ["다음 달에도 계획한 글을 예정대로 발행합니다.", "아직 병원이 나오지 않는 질문을 다음 글에 반영합니다."],
            "yours": ["월 1회 통화에서 환자분들이 많이 묻는 내용을 알려주세요."],
        },
        "footnotes": [
            "AI 답변은 같은 질문에도 시점과 표현에 따라 달라질 수 있습니다.",
            caveat_text,
        ],
    }
    expectation = DoctorPdfExpectation(
        hospital_name=view["hospital_name"],
        coverage_text=coverage_text,
        caveat_text=caveat_text,
        public_url="https://reputation.motionlabs.kr/jangpyeonhan",
        appendix_expected=bool(appendix_rows),
    )

    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-07",
        public_url=expectation.public_url,
        expectation=expectation,
    )

    assert rendered.metadata.page_count == (2 if appendix_rows else 1)
    assert rendered.metadata.page_size == "A4"
    assert rendered.metadata.font_family == "Pretendard"
    assert rendered.metadata.font_embedded is True
    assert rendered.metadata.korean_to_unicode is True
    assert rendered.metadata.link_count > 0
    assert rendered.metadata.expected_link_present is True
    assert rendered.metadata.required_text_present is True
    assert rendered.metadata.glyph_count > 0
    assert rendered.sha256 == rendered.metadata.sha256
    assert rendered.byte_size == len(rendered.pdf_bytes)
    assert Path("app/assets/fonts/PretendardVariable.woff2").exists()
    if evidence_root := os.getenv("PDF_EVIDENCE_DIR"):
        output_dir = Path(evidence_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"doctor-{fixture_name}.pdf").write_bytes(rendered.pdf_bytes)
        (output_dir / f"doctor-{fixture_name}.json").write_text(
            json.dumps(rendered.metadata.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── 최대 밀도 리포트가 실제로 1쪽(+부록 1쪽)에 들어가는가 ─────────────────


def _dense_view(*, with_appendix: bool):
    """트리밍 사다리가 감당해야 하는 **최악의 입력**으로 뷰를 만든다.

    각주 전부(측정·오차·주의·V0·첫 측정·비교 불가), 가장 긴 요약·측정 범위·다음 달
    계획, 새/빠진 질문 각 3개, 글 제목 3개, 긴 인용문 2개. 부록은 가장 긴 문자열
    15행. 페이지 수가 1(또는 부록 포함 2)을 넘으면 검증이 PDF를 통째로 버리고
    Admin은 그 달을 `doctor_artifact_missing`으로 잠근다.
    """
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.services.report_engine import build_doctor_report_view

    long_answer = "환자분이 이해하기 쉬운 말로 검사 과정과 준비 방법을 자세히 안내합니다. " * 9
    questions = [
        "강남역 근처에서 치질 수술과 대장내시경 검사를 함께 잘하는 항문외과 병원을 "
        f"야간 진료까지 가능한 곳으로 추천해 주시겠어요 그리고 주차도 되는 곳이면 좋겠습니다 {index:02d}"
        for index in range(15)
    ]
    competitor = "가나다라마바사아자차카타파하외과의원 강남역 본원 및 분원"
    cited_title = (
        "치질 수술 후 회복 기간과 일상 복귀 시점, 통증 관리 방법에 대해 "
        "환자분들이 자주 묻는 질문을 모두 모아 정리한 안내문"
    )

    def _record(question: str, *, mentioned: bool, platform: str):
        return SimpleNamespace(
            is_mentioned=mentioned,
            raw_response=f"장편한외과의원 {long_answer}" if mentioned else long_answer,
            measurement_status="SUCCESS",
            ai_platform=platform,
            measured_at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            competitor_mentions=[{"name": competitor, "is_mentioned": True}],
            query=SimpleNamespace(query_text=question),
            ai_query_target=None,
        )

    records = [
        _record(question, mentioned=index == 0, platform=platform)
        for index, question in enumerate(questions)
        for platform in ("chatgpt", "gemini")
    ]
    question_rows = [
        {
            "query_key": f"q{index}",
            "query_text": question,
            "current_attempts_used": 10,
            "current_mentioned_attempts": 6,
            "prior_attempts_used": 10,
            "prior_mentioned_attempts": 4,
            "prior_measured": True,
        }
        for index, question in enumerate(questions)
    ]
    return build_doctor_report_view(
        hospital=SimpleNamespace(name="의료법인 장편한외과의원 강남역 본원"),
        sov_pct=47.0,
        prev_sov_pct=39.0,
        published_count=12,
        plan_quota=16,
        attribution={
            "new_mention_count": 3,
            "first_measured_mention_count": 2,
            "non_comparable_count": 1,
            "lost_mention_count": 3,
            "has_prior_month": True,
            "new_mention_cells": [
                {"query_text": question, "platform_label": "ChatGPT"}
                for question in questions[:3]
            ],
            "lost_mention_cells": [
                {"query_text": question, "platform_label": "Gemini"}
                for question in questions[3:6]
            ],
            "question_rows": question_rows if with_appendix else [],
        },
        records=records,
        citations={
            "measured_cell_count": 30,
            "cited_cell_count": 6,
            "cited_content_count": 3,
            "cited_items": [
                {
                    "title": cited_title,
                    "cited_cell_count": 6,
                    "queries": [
                        {"query_text": question, "platform_label": "ChatGPT"}
                        for question in questions
                    ],
                }
            ],
        },
        published_contents=[
            SimpleNamespace(
                title=cited_title,
                content_type="FAQ",
                query_target_id=None,
            ),
            SimpleNamespace(
                title="대장내시경 검사 전날 식사와 장 정결제 복용 방법 안내",
                content_type="TREATMENT",
                query_target_id=None,
            ),
            SimpleNamespace(
                title="겨울철 항문 질환을 예방하는 생활 습관 다섯 가지",
                content_type="HEALTH",
                query_target_id=None,
            ),
        ],
        v0_baseline={
            "of_hundred": 31,
            "current_of_hundred": 47,
            "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
        },
        platforms=["chatgpt", "gemini"],
        sov_coverage={
            "planned_count": 30,
            "success_count": 30,
            "margin_of_hundred": 12,
            "significance": "WITHIN_NOISE",
            "attempts_used": 150,
            "mention_frequency": 0.47,
            "ci95_low": 35.0,
            "ci95_high": 59.0,
            "measurement_basis": {
                "question_count": 15,
                "platform_count": 2,
                "cell_count": 30,
                "repeat_count": 4,
                "repeat_min": 3,
                "repeat_max": 5,
                "attempts_used": 150,
            },
        },
        comparison_reason="MATCHED_COHORT",
    )


def test_the_densest_view_still_trims_to_one_page_before_the_appendix() -> None:
    """사다리는 최악의 입력에서도 1쪽 예산 안으로 들어와야 한다."""
    from app.services.report_engine import DOCTOR_PAGE1_LINE_BUDGET, _page1_line_cost

    view = _dense_view(with_appendix=False)

    cost = _page1_line_cost(
        summary=view["summary"],
        coverage_text=view["coverage_text"],
        delta_sentence=view["headline"]["delta_sentence"],
        v0_baseline=view["v0_baseline"],
        published_items=view["published_items"],
        citation_line=view["citation_line"],
        new_mentions=view["new_mention_sentences"],
        lost_mentions=view["lost_mention_sentences"],
        next_actions=view["next_actions"],
        evidence=view["evidence"],
        footnotes=view["footnotes"],
    )

    assert cost <= DOCTOR_PAGE1_LINE_BUDGET
    assert view["trimmed"], "최대 밀도인데 아무것도 덜어내지 않았다면 예산 계산이 틀렸다"
    # 의료 주의 문구는 검증이 PDF에서 존재를 확인하므로 절대 덜어내지 않는다.
    assert "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다." in view["footnotes"]


def test_the_densest_appendix_rows_are_bounded_by_characters_not_only_rows() -> None:
    from app.services.report_engine import (
        DOCTOR_APPENDIX_CITED_TITLE_CHARS,
        DOCTOR_APPENDIX_COMPETITOR_CHARS,
        DOCTOR_APPENDIX_QUERY_CHARS,
        DOCTOR_APPENDIX_ROW_LIMIT,
    )

    rows = _dense_view(with_appendix=True)["appendix_rows"]

    assert len(rows) == DOCTOR_APPENDIX_ROW_LIMIT
    for row in rows:
        assert len(row["query_text"]) <= DOCTOR_APPENDIX_QUERY_CHARS
        assert len(row["competitor"]) <= DOCTOR_APPENDIX_COMPETITOR_CHARS
        assert len(row["cited_title"]) <= DOCTOR_APPENDIX_CITED_TITLE_CHARS
    # 잘렸다는 사실이 보여야 한다 — 조용히 사라지면 원장이 다른 질문으로 읽는다.
    assert rows[0]["query_text"].endswith("…")
    assert rows[0]["cited_title"].endswith("…")


@pytest.mark.skipif(
    os.getenv("REQUIRE_PDF_RENDER") is None,
    reason="WeasyPrint 네이티브 의존성이 필요하다. CI에서 REQUIRE_PDF_RENDER=1로 강제한다.",
)
@pytest.mark.parametrize("with_appendix", [False, True])
def test_the_densest_possible_report_renders_to_one_or_two_pages(with_appendix: bool) -> None:
    """최대 밀도 + 최장 부록도 1쪽(부록 포함 2쪽)을 넘지 않는다.

    넘치면 `DOCTOR_PDF_PAGE_COUNT_INVALID`로 아티팩트가 만들어지지 않고, Admin이
    그 달을 `doctor_artifact_missing`으로 잠가 원장 리포트가 전달되지 않는다.
    """
    view = _dense_view(with_appendix=with_appendix)
    expectation = DoctorPdfExpectation(
        hospital_name=view["hospital_name"],
        coverage_text=view["coverage_text"],
        caveat_text="이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        public_url="https://reputation.motionlabs.kr/jangpyeonhan",
        appendix_expected=bool(view["appendix_rows"]),
    )

    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-07",
        public_url=expectation.public_url,
        expectation=expectation,
    )

    assert rendered.metadata.page_count == (2 if with_appendix else 1)
