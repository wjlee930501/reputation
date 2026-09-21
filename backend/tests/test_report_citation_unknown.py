"""No successful citation observations must never become zero citations."""
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.report_work_evidence import published_work_evidence


@pytest.mark.parametrize("citations,expected", [
    (None, None),
    ({"measured_cell_count": 0, "cited_items": []}, None),
    ({"measured_cell_count": 4, "cited_items": []}, 0),
])
def test_published_work_distinguishes_unknown_from_observed_zero(citations, expected):
    hospital = SimpleNamespace(id=UUID(int=1), slug="fictional", aeo_domain=None)
    content = SimpleNamespace(
        id=UUID(int=2), hospital_id=hospital.id,
        status="PUBLISHED", title="가상 공식 진료 안내",
    )
    work = published_work_evidence(hospital, [content], citations)[0]
    assert work.cited_cells == expected
    assert work.url is not None


def test_unknown_citations_are_not_printed_as_zero_in_monthly_pdf():
    from dataclasses import replace
    from io import BytesIO

    from pypdf import PdfReader
    from test_report_redesign import monthly_view

    from app.services.doctor_pdf_contracts import DoctorPdfExpectation
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.report_narrative import PublishedWork

    view = monthly_view()
    work = PublishedWork("가상 진료 안내", str(UUID(int=2)), None, None, ())
    view["narrative"] = replace(view["narrative"], works=(work,))
    expectation = DoctorPdfExpectation(
        view["hospital_name"], view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
    )
    result = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expectation.public_url,
        expectation=expectation,
    )
    text = "".join("".join(page.extract_text().split()) for page in PdfReader(BytesIO(result.pdf_bytes)).pages)
    assert "소유URL인용미확인" in text
    assert "소유URL인용0개조합" not in text
