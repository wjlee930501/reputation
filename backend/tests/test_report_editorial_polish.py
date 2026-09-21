"""Production PDF boundaries for editorial reporting, using fictional records."""

from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader
from test_lead_proposal import payload
from test_report_redesign import monthly_view

from app.services.doctor_pdf_contracts import DoctorPdfExpectation
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.lead_report import LeadReportContact, render_lead_report_pdf
from app.services.report_narrative import PublishedWork


@pytest.mark.parametrize("prior", [None, -1, 101, float("nan"), float("inf")])
def test_invalid_prior_withholds_bar_and_delta(prior):
    # Given a claimed matching cohort with an unusable prior value.
    view = monthly_view(
        comparison_reason="MATCHED_COHORT",
        sov_coverage={
            "planned_count": 2,
            "success_count": 2,
            "comparison": {
                "status": "COMPARABLE",
                "reason": "MATCHED_COHORT",
                "matched_cell_count": 2,
                "current_sov_pct": 40,
                "prior_sov_pct": prior,
            },
        },
    )
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "app/templates"))
    # When rendering the actual template.
    html = environment.get_template("doctor_report_v3.html").render(
        view=view, period_label="2026-08", public_url="https://fictional.example.invalid/"
    )
    # Then the current metric exists without a fabricated comparison.
    assert view["narrative"].previous is None
    assert 'class="bar prior"' not in html
    assert 'class="bar"' in html
    assert "%p" not in html


def test_cited_work_precedes_uncited_and_internal_identifiers_are_not_printed():
    from app.services.report_narrative import build_monthly_narrative

    # Given three real publication records, with only the last cited.
    works = tuple(
        PublishedWork(f"가상 발행 글 {i}", f"internal-id-{i}", None, 2 if i == 3 else 0, ())
        for i in range(1, 4)
    )
    # When building the production narrative.
    narrative = build_monthly_narrative(
        kind="MONTHLY",
        coverage=None,
        attribution=None,
        citations=None,
        works=works,
        current=40,
        previous=None,
        comparison_reason=None,
        shortfall=0,
    )
    # Then cited work is first, and every work remains available to the appendix.
    assert narrative.works[0] == works[2]
    assert set(narrative.works) == set(works)
    view = monthly_view()
    view["narrative"] = narrative
    expected = DoctorPdfExpectation(
        view["hospital_name"],
        view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
    )
    rendered = render_validated_doctor_pdf(
        view=view,
        period_label="2026-08",
        public_url=expected.public_url,
        expectation=expected,
    )
    texts = [page.extract_text() for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages]
    assert "internal-id" not in "".join(texts)
    assert all(work.title.replace(" ", "") in "".join(texts[3:]).replace(" ", "") for work in works)


@pytest.mark.parametrize("measured,mentioned", [(0, 0), (6, 6)])
def test_no_contact_long_clinic_name_preserves_unicode_and_has_no_links(measured, mentioned):
    # Given a normal unspaced clinic name and no configured contact.
    name = "검증용 가상 서울한마음가정의학과건강검진센터의원"
    report = replace(
        payload(measured, mentioned, 6 if not measured else 0),
        hospital_name=name,
        contact=LeadReportContact("", "", "", ""),
    )
    # When generating the validated binary.
    pages = PdfReader(BytesIO(render_lead_report_pdf(report))).pages
    # Then Unicode identity survives and no consultation URI is invented.
    assert len(pages) == 4
    assert "".join(name.split()) in "".join(pages[0].extract_text().split())
    assert all(not page.get("/Annots") for page in pages)
