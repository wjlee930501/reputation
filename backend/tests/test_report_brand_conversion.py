"""Conversion and safe state contracts using fictional records only."""
from dataclasses import replace
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from test_lead_proposal import payload
from test_report_redesign import monthly_view

from app.services.lead_report import TEMPLATE_VERSION, render_lead_report_html
from app.services.report_narrative import PublishedWork


@pytest.mark.parametrize("measured,mentioned,failed", [(6, 0, 0), (6, 6, 0), (0, 0, 6)])
def test_diagnosis_connects_why_now_assets_and_regional_fit(measured, mentioned, failed):
    # Given fictional observations in distinct states.
    report = payload(measured, mentioned, failed)
    # When the customer document renders.
    html = render_lead_report_html(report)
    # Then the approved decision narrative exists independently of results.
    assert TEMPLATE_VERSION == "lead-v12"
    assert "우리 병원의 강점이," in html and "AI가 답할 근거가 되도록." in html
    assert "기다린 시간은 발행 이력이 되지 않습니다." in html.replace("<br>", " ")
    assert "공개 정보 허브" in html and "전담 마케터" in html
    assert "동일 지역·유사 진료 분야는 기존 운영 병원과의 중복을 확인한 뒤 안내합니다." in html
    assert "우리 지역 운영 가능 여부 확인" in html
    assert "기존 홈페이지와 블로그는 그대로" in html
    assert 'class="statement-band"' in html and 'class="proposal"' in html
    assert "#0672ed" in html and "#99522e" not in html


@pytest.mark.parametrize("cited_cells,label", [(None, "인용 집계 미확인"), (0, "관측한 인용 0개 조합"), (2, "출처로 확인 · 2개 질문×플랫폼 조합")])
def test_portfolio_distinguishes_unknown_absence_and_citation(cited_cells, label):
    # Given fictional work with independently specified citation coverage.
    view = monthly_view()
    view["narrative"] = replace(view["narrative"], works=(PublishedWork("가상 검진 안내", "fictional-1", None, cited_cells, ()),))
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "app/templates"), autoescape=select_autoescape(("html",)))
    # When the main portfolio renders.
    html = environment.get_template("doctor_report_v3.html").render(view=view, period_label="2026-08", public_url="https://fictional.example.invalid/")
    page = html.split('id="main-2-start"')[1].split('id="main-3-start"')[0]
    # Then unknown cannot be presented as observed absence.
    assert label in page
    assert "전담 마케터" in html and 'class="delivery-ledger"' in html


def test_comparison_bars_have_equal_tracks_and_visible_proportional_fills():
    from weasyprint import HTML

    from app.services.report_typography import keep_korean_words

    # Given known values on the shared production comparison surface.
    view = monthly_view()
    view["narrative"] = replace(view["narrative"], previous=25.0, current=50.0)
    templates = Path(__file__).parents[1] / "app/templates"
    environment = Environment(loader=FileSystemLoader(templates))
    html = environment.get_template("doctor_report_v3.html").render(
        view=view, period_label="2026-08", public_url="https://fictional.example.invalid/"
    )
    # When the actual print layout resolves sizes and colors.
    document = HTML(string=keep_korean_words(html), base_url=str(templates)).render()
    boxes = list(document.pages[0]._page_box.descendants())
    tracks = [box for box in boxes if box.element is not None and box.element.get("class") == "scale"]
    fills = [box for box in boxes if box.element is not None and box.element.get("class") in {"bar", "bar prior"}]
    # Then both proportions are visible against the same-scale track.
    assert len(tracks) == len(fills) == 2
    assert tracks[0].width == pytest.approx(tracks[1].width)
    for track, fill, rate in zip(tracks, fills, (0.25, 0.5), strict=True):
        assert fill.width == pytest.approx(track.width * rate)
        assert fill.style["background_color"] != track.style["background_color"]


def test_no_work_and_no_observations_cannot_claim_completed_publication():
    view = monthly_view()
    view['narrative'] = replace(view['narrative'], works=())
    view['evidence'] = {'found': None, 'missing': None}
    templates = Path(__file__).parents[1] / 'app/templates'
    environment = Environment(loader=FileSystemLoader(templates))
    html = environment.get_template('doctor_report_v3.html').render(
        view=view, period_label='2026-08', public_url='https://fictional.example.invalid/'
    )
    proof_page = html.split('id="main-2-start"')[1].split('id="main-3-start"')[0]
    assert '공개 기록은 있습니다.' not in proof_page
    assert '확인할 근거부터' in proof_page


def test_diagnosis_outcome_is_understanding_and_evidence_not_promised_patients():
    html = render_lead_report_html(payload(6, 0, 0))
    assert '환자가 병원을 이해할 근거,' in html
    assert '원장님이 변화를 확인할 기록.' in html
    assert '환자 수·매출 절대 증가는 계약 범위가 아닙니다.' in html
    assert '독점 보장' not in html
    assert '2개월 안에' not in html
