from __future__ import annotations

import io
import json
import os
from pathlib import Path

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
