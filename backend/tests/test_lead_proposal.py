"""Safe deterministic proposal boundaries with fictional measurements."""

from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.services.lead_proposal import build_lead_proposal
from app.services.lead_report import (
    LeadReportContact,
    LeadReportPayload,
    PlatformSegment,
    QueryDisclosure,
    render_lead_report_html,
    render_lead_report_pdf,
)


def payload(measured=6, mentioned=0, failed=0):
    return LeadReportPayload(
        hospital_name="검증용 가상 한글과 Unicode 의원",
        region="가상동",
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
        repeat_count=3,
        system_prompt="가상 측정 조건",
        judge_model="fictional",
        queries=tuple(
            QueryDisclosure(
                slot=i,
                kind="검증",
                text=f"가상동 {topic} 상담은 어디서 받나요?",
                measured_at=datetime(2026, 7, 1, tzinfo=UTC),
                planned=6,
                measured=measured,
                mentioned=mentioned,
                failed=failed,
            )
            for i, topic in enumerate(("건강검진", "만성질환"))
        ),
        segments=(
            PlatformSegment("chatgpt", "OpenAI API", "fictional", 6, measured, mentioned, failed),
        ),
        contact=LeadReportContact("가상 담당자", "가상 상담", "fictional@example.invalid", ""),
    )


@pytest.mark.parametrize(
    "measured,mentioned,failed,state",
    [
        (0, 0, 6, "UNAVAILABLE"),
        (6, 0, 0, "ZERO"),
        (6, 2, 0, "PARTIAL"),
        (6, 6, 0, "HIGH"),
        (3, 3, 3, "PARTIAL"),
    ],
)
def test_opportunity_states_preserve_missing_vs_zero(measured, mentioned, failed, state):
    result = build_lead_proposal(payload(measured, mentioned, failed))
    assert len(result.proposals) == 2
    assert {item.state for item in result.proposals} == {state}
    assert result.proposals[0].check != result.proposals[1].check


def test_internal_has_no_customer_cta_or_links_in_html_and_pdf():
    report = replace(payload(), internal=True)
    html = render_lead_report_html(report)
    assert 'class="cta"' not in html and "href=" not in html
    reader = PdfReader(BytesIO(render_lead_report_pdf(report)))
    assert len(reader.pages) == 4
    for page in reader.pages:
        assert "AE전용" in "".join(page.extract_text().split())
        assert not page.get("/Annots")


@pytest.mark.parametrize("value", ["가" * 201, "가상\x00병원"])
def test_absurd_names_fail_closed(value):
    with pytest.raises(ValueError, match="LEAD_REPORT_FIELD_LIMIT"):
        render_lead_report_html(replace(payload(), hospital_name=value))


def test_normal_long_korean_name_and_unicode_render_without_losing_text():
    name = "검증용 가상 서울특별시 한마음가정의학과 건강검진센터 의원"
    report = replace(payload(), hospital_name=name, region="가상동 · 검증")
    reader = PdfReader(BytesIO(render_lead_report_pdf(report)))
    assert "".join(name.split()) in "".join(reader.pages[0].extract_text().split())
    assert "2026-07-01" in "".join(page.extract_text() for page in reader.pages)
