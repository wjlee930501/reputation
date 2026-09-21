from datetime import datetime, timezone

import pytest

from app.services import lead_report


@pytest.fixture
def design_payload() -> lead_report.LeadReportPayload:
    measured_at = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    queries = tuple(
        lead_report.QueryDisclosure(
            slot=slot,
            kind="진료과형",
            text=text,
            measured_at=measured_at,
            planned=6,
            measured=6,
            mentioned=2 if slot == 1 else 0,
            failed=0,
        )
        for slot, text in (
            (1, "수서역 근처 외과 병원 추천해줘"),
            (2, "수서역 근처 대장내시경 병원 추천해줘"),
            (3, "치질이 있는데 수서역 근처 병원 어디로 가야해?"),
        )
    )
    return lead_report.LeadReportPayload(
        hospital_name="장편한외과의원",
        region="수서역",
        generated_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        repeat_count=3,
        system_prompt="지역 병원 정보를 잘 아는 의료 정보 도우미입니다.",
        judge_model="gpt-4o-mini-2024-07-18",
        # 실제 리포트는 항상 두 경로다. 그리고 **결측 수가 서로 다른** 경우를 쓴다 —
        # 합산 헤드라인이 왜 위험한지가 드러나는 유일한 형태이기 때문이다.
        segments=(
            lead_report.PlatformSegment(
                platform="chatgpt",
                vendor_label="OpenAI API",
                model="gpt-5.6-luna",
                planned=9,
                measured=8,
                mentioned=2,
                failed=1,
            ),
            lead_report.PlatformSegment(
                platform="gemini",
                vendor_label="Google Gemini API",
                model="gemini-3.6-flash",
                planned=9,
                measured=8,
                mentioned=1,
                failed=0,
                ambiguous=1,
            ),
        ),
        queries=queries,
        contact=lead_report.LeadReportContact(
            name="김효진",
            role="Re:putation 마케팅 팀장",
            email="hjkim@motionlabs.kr",
            phone="070-8671-0100",
        ),
    )


def test_html_has_three_intentional_main_pages_and_measurement_appendix(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    assert all(
        f'id="main-{index}-start"' in html and f'id="main-{index}-end"' in html
        for index in range(1, 4)
    )
    assert 'id="lead-details"' in html


def test_opportunities_precede_query_specific_proposals_and_commercial_scope(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    assert (
        html.index('id="result"')
        < html.index('id="main-2-start"')
        < html.index('id="main-3-start"')
    )
    assert "Re:putation은 ‘평판’이라는 뜻입니다" not in html


def test_report_packages_and_uses_only_pretendard(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    assert lead_report.PRETENDARD_FONT_PATH.is_file()
    assert 'font-family: "Pretendard";' in html
    assert "NanumGothic" not in html
    assert "Apple SD Gothic Neo" not in html


def test_one_configured_contact_is_the_dominant_cta(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    assert html.count('class="cta"') == 1
    assert 'href="tel:070-8671-0100"' in html


def test_result_headline_reports_each_platform_separately(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    first_page = html.split('id="main-2-start"', maxsplit=1)[0]
    for segment in design_payload.segments:
        assert f"{segment.mentioned} / {segment.measured}" in first_page
        assert segment.vendor_label in first_page
    assert f"{design_payload.total_mentioned} / {design_payload.total_measured}" not in first_page


def test_operating_page_keeps_prices_and_volumes_separate_from_recommendations(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    third_page = html.split('id="main-3-start"')[1].split('id="lead-details"')[0]
    for price, volume in ((60, 12), (90, 16), (120, 20)):
        assert f"월 {price}만 원" in third_page
        assert f"월 {volume}편 발행" in third_page
    assert third_page.count("부가세 별도") == 3
    assert "특정 AI 순위 보장" in third_page
    assert "고정 노출 보장" in third_page
    assert "환자 수·매출 절대 증가" in third_page


def test_report_uses_editorial_rules_instead_of_saas_card_effects(design_payload):
    html = lead_report.render_lead_report_html(design_payload)
    assert "#99522e" in html
    assert "border-radius" not in html
    assert "box-shadow" not in html
    assert "linear-gradient" not in html
