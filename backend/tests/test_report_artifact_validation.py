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


def _two_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595.28, height=841.89)
    writer.add_blank_page(width=595.28, height=841.89)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _expectation() -> DoctorPdfExpectation:
    return DoctorPdfExpectation(
        hospital_name="장편한외과의원",
        coverage_text="측정 범위: 챗GPT, 제미나이에서 계획한 답변 20개 중 20개를 확인했습니다.",
        caveat_text="이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        public_url="https://reputation.motionlabs.kr/jangpyeonhan",
    )


def test_validator_rejects_a_two_page_artifact_before_it_can_be_saved() -> None:
    with pytest.raises(DoctorPdfValidationError) as exc:
        validate_doctor_pdf(_two_page_pdf(), _expectation())

    assert exc.value.code == "DOCTOR_PDF_PAGE_COUNT_INVALID"
    assert "1쪽" in exc.value.problem
    assert "원장님께 전달" in exc.value.customer_impact
    assert "리포트 다시 만들기" in exc.value.next_action


def test_persisted_metadata_parser_fails_closed_for_incomplete_or_old_shapes() -> None:
    assert parse_doctor_artifact_metadata(None) is None
    assert parse_doctor_artifact_metadata({"page_count": 1, "glyph_count": 840}) is None
    assert (
        parse_doctor_artifact_metadata(
            {
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
@pytest.mark.parametrize("fixture_name", ["minimum", "typical", "maximum"])
def test_real_doctor_artifact_is_one_page_a4_with_korean_font_text_and_link(
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
    }[fixture_name]
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
    )

    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-07",
        public_url=expectation.public_url,
        expectation=expectation,
    )

    assert rendered.metadata.page_count == 1
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
