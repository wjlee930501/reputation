"""AE monthly PDF renders baseline-honest attribution copy."""

from datetime import datetime
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.report_engine import TEMPLATE_DIR


def _render(attribution, **overrides):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    template = env.get_template("report.html")
    context = {
        "hospital": SimpleNamespace(
            name="장편한외과의원",
            region=["강남"],
            specialties=["대장항문외과"],
            plan="PLAN_16",
        ),
        "report_type": "MONTHLY",
        "period_label": "2026-07",
        "period_start": datetime(2026, 7, 1),
        "period_end": datetime(2026, 7, 31),
        "sov_pct": 42.0,
        "sov_measured": True,
        "published_count": 3,
        "repeat_count": 5,
        "attribution": attribution,
        "generated_at": datetime(2026, 7, 31),
        **overrides,
    }
    return template.render(**context)


def _cell(classification, label, query, platform, *, related=()):
    return {
        "query_text": query,
        "platform_label": platform,
        "classification": classification,
        "classification_label": label,
        "meaning": "지난달 측정이 완료되지 않았습니다.",
        "customer_impact": "새 언급 수에서 제외했습니다.",
        "next_action": "다음 달 정상 측정 후 비교하세요.",
        "related_contents": list(related),
    }


def _sample_attribution(**overrides):
    payload = {
        "content_type_counts": dict.fromkeys(
            ["FAQ", "DISEASE", "TREATMENT", "COLUMN", "HEALTH", "LOCAL", "NOTICE"], 0
        ),
        "prev_content_type_counts": dict.fromkeys(
            ["FAQ", "DISEASE", "TREATMENT", "COLUMN", "HEALTH", "LOCAL", "NOTICE"], 0
        ),
        "published_count": 3,
        "prev_published_count": 1,
        "new_mention_cells": [_cell(
            "NEW_MENTION",
            "지난달보다 새로 확인된 언급",
            "강남 치질 수술",
            "ChatGPT",
            related=("치질 FAQ",),
        )],
        "first_measured_mention_cells": [_cell(
            "FIRST_MEASURED_MENTION",
            "이번 달 처음 확인된 언급",
            "강남 탈장 수술",
            "Gemini",
        )],
        "non_comparable_cells": [_cell(
            "NON_COMPARABLE",
            "지난달과 비교할 수 없는 언급",
            "강남 맹장 수술",
            "ChatGPT",
        )],
        "new_mention_queries": [],
        "new_mention_count": 1,
        "first_measured_mention_count": 1,
        "non_comparable_count": 1,
        "sov_pct": 42.0,
        "prev_sov_pct": 30.0,
        "change_pct": 12.0,
        **overrides,
    }
    return payload


def test_report_renders_plain_korean_attribution_states():
    html = _render(_sample_attribution())
    section = html[html.index("콘텐츠 발행과 AI 언급 변화"):]

    for expected in [
        "강남 치질 수술",
        "ChatGPT",
        "치질 FAQ",
        "지난달보다 새로 확인된 언급",
        "이번 달 처음 확인된 언급",
        "새 언급으로 계산하지 않습니다",
        "지난달과 비교할 수 없는 언급",
        "고객 영향",
        "지금 할 일",
    ]:
        assert expected in section
    for hidden in ["NEW_MENTION", "FIRST_MEASURED_MENTION", "NON_COMPARABLE", "덕분에"]:
        assert hidden not in section
    for banned in ["1등", "최고", "완치", "100%", "유일", "성공률"]:
        assert banned not in section


def test_report_renders_empty_new_mentions_branch():
    html = _render(_sample_attribution(
        new_mention_cells=[],
        first_measured_mention_cells=[],
        non_comparable_cells=[],
        new_mention_count=0,
        first_measured_mention_count=0,
        non_comparable_count=0,
    ))
    assert "지난달과 같은 기준으로 새로 확인된 언급은 없습니다" in html


def test_report_attribution_coheres_with_no_sov_data():
    attribution = _sample_attribution(sov_pct=None, prev_sov_pct=None, change_pct=None)
    html = _render(attribution, sov_pct=None, sov_measured=False)

    assert "측정 데이터 없음" in html
    assert "0.0%" not in html


def test_report_without_attribution_omits_section():
    html = _render(None)
    assert "콘텐츠 발행과 AI 언급 변화" not in html


def test_report_renders_content_operations_truthfully():
    html = _render(
        None,
        content_operations={
            "plan_quota": 16,
            "published_count": 15,
            "shortfall_count": 1,
            "post_publish_review": {
                "required_sample_count": 2,
                "pending_count": 0,
            },
            "delivery_warnings": ["약정 콘텐츠 16편 중 15편만 발행되었습니다."],
        },
    )

    assert "약정 편수" in html
    assert "16편" in html
    assert "15편" in html
    assert "약정 콘텐츠 16편 중 15편만 발행되었습니다." in html
    assert "다음 달 복구 계획" in html
