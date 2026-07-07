"""report.html 렌더링 회귀 — repeat_count 동적 표기 + 측정 데이터 없음(None) 표기 (결함 2, 3)."""
from datetime import datetime
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.report_engine import TEMPLATE_DIR


def _render(**overrides) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    template = env.get_template("report.html")
    hospital = SimpleNamespace(
        name="장편한외과의원",
        region=["강남"],
        specialties=["대장항문외과"],
        plan="PLAN_16",
    )
    ctx = dict(
        hospital=hospital,
        report_type="V0",
        period_label="V0-진단",
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 8),
        sov_pct=12.5,
        sov_measured=True,
        published_count=0,
        repeat_count=5,
        generated_at=datetime(2026, 7, 8),
    )
    ctx.update(overrides)
    return template.render(**ctx)


def test_report_renders_actual_repeat_count_not_hardcoded_ten():
    html = _render(repeat_count=7)
    assert "7회 반복" in html
    assert "10회 반복" not in html


def test_report_renders_no_data_when_sov_unmeasured():
    html = _render(sov_pct=None, sov_measured=False)
    assert "측정 데이터 없음" in html
    # None을 %.1f 로 포매팅하려다 터지지 않아야 한다.
    assert "0.0%" not in html


def test_report_renders_percentage_when_measured():
    html = _render(sov_pct=42.0, sov_measured=True)
    assert "42.0%" in html
