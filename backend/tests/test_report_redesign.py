"""Fictional, offline contracts for the report redesign."""

from types import SimpleNamespace

import pytest

from app.services.report_engine import build_doctor_report_view


def monthly_view(**overrides):
    values = dict(
        hospital=SimpleNamespace(name="검증용 가상 새봄의원", id="fictional", slug="fictional"),
        sov_pct=40.0,
        prev_sov_pct=20.0,
        published_count=0,
        plan_quota=12,
        attribution=None,
        records=[],
        report_kind="MONTHLY",
        comparison_reason="NO_PRIOR_MANIFEST",
    )
    values.update(overrides)
    return build_doctor_report_view(**values)


def test_monthly_has_distinct_typed_narrative_and_retains_baseline():
    # Given a first measurement, not a fabricated pre-service baseline.
    baseline = {"of_hundred": 20, "current_of_hundred": 40, "sentence": "초기 측정 참고 20%"}
    # When the monthly narrative is built.
    view = monthly_view(v0_baseline=baseline)
    # Then the baseline survives and comparison is withheld.
    assert view["narrative"].title == "AI 답변 노출 월간 보고서"
    assert view["narrative"].previous is None
    assert view["v0_baseline"] == baseline
    assert view["trimmed"] == []


@pytest.mark.parametrize(
    "kind,title", [("INITIAL", "첫 측정 보고서"), ("MONTHLY", "AI 답변 노출 월간 보고서")]
)
def test_report_kind_owns_title(kind, title):
    view = monthly_view(report_kind=kind)
    assert view["narrative"].title == title


@pytest.mark.parametrize(
    "current,prior,word",
    [
        (60, 30, "지난달보다 AI 답변에 더 자주 언급됐습니다."),
        (30, 30, "지난달과 비슷하게 꾸준히 언급되고 있습니다."),
        (25, 30, "AI 언급 횟수를 늘리기 위해, 더 넓은 키워드를 공략하겠습니다."),
        (0, 30, "AI 언급 횟수를 늘리기 위해, 더 넓은 키워드를 공략하겠습니다."),
        (0, 0, "AI 답변에 언급되도록, 더 넓은 키워드와 새로운 질문 유형을 공략하겠습니다."),
        (None, 30, "이번 달 측정을 다시 진행해, 결과를 확인하는 대로 알려 드리겠습니다."),
    ],
)
def test_monthly_direction_requires_matching_comparison(current, prior, word):
    comparison = {
        "status": "COMPARABLE",
        "reason": "MATCHED_COHORT",
        "matched_cell_count": 2,
        "current_sov_pct": current,
        "prior_sov_pct": prior,
    }
    view = monthly_view(
        sov_pct=current,
        comparison_reason="MATCHED_COHORT",
        sov_coverage={"comparison": comparison, "planned_count": 2, "success_count": 2},
    )
    assert word in view["narrative"].conclusion
    assert "환자 수가 아니라 AI 답변 횟수입니다." in view["narrative"].denominator


@pytest.mark.parametrize(
    "reason",
    [
        "NO_PRIOR_MANIFEST",
        "ANSWER_MODEL_CHANGED",
        "MEASUREMENT_POLICY_CHANGED",
        "QUERY_TEXT_CHANGED",
        "NO_MATCHED_CELLS",
    ],
)
def test_incompatible_baseline_never_produces_comparison(reason):
    view = monthly_view(comparison_reason=reason)
    assert view["narrative"].previous is None
    assert not view["new_mention_sentences"]
    assert not view["lost_mention_sentences"]


def test_v3_is_rendered_and_rejects_missing_evidence_page():
    from dataclasses import replace
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.doctor_pdf_v3 import v3_expectation
    from app.services.report_artifact_validation import validate_doctor_pdf

    view = monthly_view()
    expectation = DoctorPdfExpectation(
        view["hospital_name"],
        view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
    )
    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-08",
        public_url=expectation.public_url,
        expectation=expectation,
    )
    assert rendered.metadata.validation_version == "doctor-pdf-v3"
    assert rendered.metadata.page_count == 4
    reader = PdfReader(BytesIO(rendered.pdf_bytes))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index == 1:
            writer.add_blank_page(width=595.28, height=841.89)
        else:
            writer.add_page(page)
    corrupted = BytesIO()
    writer.write(corrupted)
    bound = v3_expectation(view, replace(expectation, period_label="2026-08"), 4)
    with pytest.raises(DoctorPdfValidationError, match="본문 2쪽") as caught:
        validate_doctor_pdf(corrupted.getvalue(), bound)
    assert caught.value.code == "DOCTOR_PDF_MAIN_TEXT_MISSING"


def test_same_title_does_not_attribute_a_different_content_id():
    from uuid import UUID

    from app.services.report_work_evidence import published_work_evidence

    hospital = SimpleNamespace(
        id=UUID(int=1), slug="fictional", aeo_domain="fictional.example.invalid"
    )
    content = SimpleNamespace(
        id=UUID(int=2), hospital_id=hospital.id, status="PUBLISHED", title="가상 동일 제목"
    )
    evidence = {
        "measured_cell_count": 1,
        "cited_items": [
            {
                "content_id": str(UUID(int=3)),
                "title": content.title,
                "cited_cell_count": 1,
                "queries": [],
            }
        ]
    }
    work = published_work_evidence(hospital, [content], evidence)[0]
    assert work.cited_cells == 0
    assert work.url == f"https://fictional.example.invalid/contents/{content.id}"
    content.hospital_id = UUID(int=4)
    assert published_work_evidence(hospital, [content], evidence)[0].url is None


def test_v3_retains_dense_questions_baseline_positive_and_negative_evidence():
    from io import BytesIO

    from pypdf import PdfReader

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf

    rows = [
        {
            "query_text": f"검증용 가상 질문 {index:03d} 건강검진 상담을 받을 수 있는 곳은 어디인가요?",
            "prior_measured": True,
            "prior_comparable": True,
            "prior_attempts_used": 6,
            "prior_mentioned_attempts": 2,
            "current_attempts_used": 6,
            "current_mentioned_attempts": 0,
        }
        for index in range(40)
    ]
    attribution = {"question_rows": rows, "has_prior_month": True}
    view = monthly_view(
        attribution=attribution,
        v0_baseline={"of_hundred": 10, "current_of_hundred": 40, "sentence": "초기 기준선"},
    )
    for key, excerpt in (
        ("found", "검증용 긍정 근거를 보존합니다."),
        ("missing", "검증용 부정 근거를 보존합니다."),
    ):
        view["evidence"][key] = {
            "question": "검증용 가상 질문",
            "excerpt": excerpt,
            "platform": "OpenAI API",
            "measured_at": None,
            "competitors": [],
        }
    expectation = DoctorPdfExpectation(
        view["hospital_name"],
        view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
        appendix_expected=True,
    )
    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-08",
        public_url=expectation.public_url,
        expectation=expectation,
    )
    pages = PdfReader(BytesIO(rendered.pdf_bytes)).pages
    assert len(pages) > 4
    text = "".join("".join(page.extract_text().split()) for page in pages)
    assert all("".join(row["query_text"].split()) in text for row in rows)
    assert "검증용긍정근거를보존합니다" in text
    assert "검증용부정근거를보존합니다" in text
    assert "처음측정했을때와비교" in text


@pytest.mark.parametrize("name", ["가" * 201, "검증\x00병원"])
def test_doctor_absurd_fields_fail_closed(name):
    from app.services.doctor_pdf_contracts import DoctorPdfExpectation, DoctorPdfValidationError
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf

    view = monthly_view(hospital=SimpleNamespace(name=name))
    expected = DoctorPdfExpectation(
        name, view["coverage_text"], "고지", "https://fictional.example.invalid/"
    )
    with pytest.raises(DoctorPdfValidationError) as caught:
        render_validated_doctor_pdf(
            view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
        )
    assert caught.value.code == "DOCTOR_PDF_LAYOUT_LIMIT"


def test_partial_measurement_keeps_failed_ambiguous_and_pending_slots():
    coverage = {
        "planned_count": 2,
        "success_count": 1,
        "platforms": [
            {
                "platform": "chatgpt",
                "mention_rate": 0.0,
                "planned_count": 2,
                "success_count": 1,
                "failed_count": 1,
                "excluded_count": 0,
                "attempts_used": 1,
                "mentioned_attempts": 0,
                "answer_models": ["fictional"],
            }
        ],
        "cells": [
            {
                "platform": "chatgpt",
                "observation_slots": {
                    "planned": 6,
                    "confirmed": 1,
                    "ambiguous": 2,
                    "answer_failed": 1,
                    "judgment_failed": 1,
                    "pending": 1,
                },
            }
        ],
    }
    view = monthly_view(sov_pct=0, sov_coverage=coverage)
    methods = " ".join(view["narrative"].methods)
    assert "판단하기 어려움 2번" in methods and "답변 실패 1번" in methods
    assert "기다리는 중 1번" in methods
    assert view["narrative"].current == 0
    assert view["narrative"].previous is None


def test_baseline_renderer_is_initial_measurement_not_monthly_work():
    from io import BytesIO

    from pypdf import PdfReader

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf

    view = monthly_view(report_kind="INITIAL")
    expected = DoctorPdfExpectation(
        view["hospital_name"],
        view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
    )
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    text = "".join(
        "".join(page.extract_text().split())
        for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages
    )
    assert "첫측정보고서" in text
    assert "약속한글발행을보고하는문서는아닙니다" in text
    assert "아직올리지못했습니다" not in text


def test_unavailable_citation_appendix_has_explicit_unknown_state():
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    view = monthly_view(
        sov_pct=None, citations={"measured_cell_count": 0, "cited_cell_count": 0, "cited_items": []}
    )
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "app/templates"))
    html = environment.get_template("doctor_report_v3.html").render(
        view=view, period_label="2026-08", public_url="https://fictional.example.invalid/"
    )
    appendix = html.split('id="appendix-start"')[1]
    assert "우리 병원 글이 출처로 쓰였는지 알 수 없습니다" in appendix
    assert "0번이라는 뜻은 아닙니다" in appendix
