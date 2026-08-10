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
        ),
        queries=queries,
        contact=lead_report.LeadReportContact(
            name="김효진",
            role="Re:putation 마케팅 팀장",
            email="hjkim@motionlabs.kr",
            phone="070-8671-0100",
        ),
    )


def test_html_has_intentional_summary_and_reputation_pages(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)

    assert 'class="page page-summary"' in html
    assert 'class="page page-reputation"' in html


def test_first_page_finishes_with_measurement_method_and_rationale(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    first_page = html.split('class="page page-reputation"', maxsplit=1)[0]

    assert first_page.index('id="result"') < first_page.index('id="importance"')
    assert first_page.index('id="importance"') < first_page.index('id="query-results"')
    assert first_page.index('id="query-results"') < first_page.index('id="verification"')
    assert "왜 이 방식이어야 하나요?" in first_page


def test_second_page_starts_from_reputation_meaning_before_service(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    second_page = html.split('class="page page-reputation"', maxsplit=1)[1]

    assert second_page.index("Re:putation은 ‘평판’이라는 뜻입니다") < second_page.index(
        "Re:putation의 운영 방식"
    )


def test_report_packages_and_uses_only_pretendard(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)

    assert lead_report.PRETENDARD_FONT_PATH.is_file()
    assert 'font-family: "Pretendard";' in html
    assert "NanumGothic" not in html
    assert "Apple SD Gothic Neo" not in html


def test_contact_exposes_the_representative_number_as_a_phone_link(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)

    assert 'href="tel:070-8671-0100"' in html
    assert ">070-8671-0100</a>" in html


def test_result_headline_exposes_mentions_over_successful_answers(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    first_page = html.split('class="page page-reputation"', maxsplit=1)[0]

    assert "2 / 8" in first_page
    assert "환자 질문에 답한 8건 중 2건에서 우리 병원 이름이 언급됐습니다" in first_page


def test_reputation_page_explains_channel_scope_and_complementary_content_hub(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    second_page = html.split('class="page page-reputation"', maxsplit=1)[1]

    assert "왜 ChatGPT·Gemini부터 확인하나요?" in second_page
    assert "Claude와 Perplexity가 중요하지 않다는 뜻은 아닙니다" in second_page
    assert "기존 홈페이지를 대체하지 않습니다" in second_page
    assert "AEO/GEO 콘텐츠 허브" in second_page


def test_reputation_page_shows_current_tier_prices_and_volumes(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    second_page = html.split('class="page page-reputation"', maxsplit=1)[1]

    assert "STARTER" in second_page and "월 60만 원" in second_page
    assert "GROWER" in second_page and "월 90만 원" in second_page
    assert "LEADER" in second_page and "월 120만 원" in second_page
    assert "월 12편 발행" in second_page
    assert "월 16편 발행" in second_page
    assert "월 20편 발행" in second_page
    assert second_page.count("부가세 별도") == 3
    assert "월 8편" not in second_page


def test_reputation_page_states_measurement_boundary_and_brand_line(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)
    second_page = html.split('class="page page-reputation"', maxsplit=1)[1]

    assert "측정할 수 없는 것은 약속드리지 않습니다" in second_page
    assert "특정 AI 순위 보장" in second_page
    assert "고정 노출 보장" in second_page
    assert "환자 수·매출 절대 증가" in second_page
    assert "모션랩스가 하면 AI 최적화도 다릅니다." in second_page


def test_report_uses_editorial_rules_instead_of_saas_card_effects(
    design_payload: lead_report.LeadReportPayload,
) -> None:
    html = lead_report.render_lead_report_html(design_payload)

    assert "#233b52" in html
    assert "border-radius" not in html
    assert "box-shadow" not in html
    assert "linear-gradient" not in html
