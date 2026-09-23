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
        "comparison_reason": "MATCHED_COHORT",
        "new_mention_empty_text": "지난달과 같은 기준으로 새로 확인된 언급은 없습니다.",
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


def test_report_does_not_claim_no_change_when_comparison_is_unavailable():
    html = _render(_sample_attribution(
        new_mention_cells=[],
        new_mention_count=0,
        comparison_reason="ANSWER_MODEL_CHANGED",
        new_mention_empty_text=(
            "지난달과 같은 조건으로 비교할 수 없어 새 언급을 계산하지 않았습니다."
        ),
    ))

    assert "비교할 수 없어 새 언급을 계산하지 않았습니다" in html
    assert "같은 기준으로 새로 확인된 언급은 없습니다" not in html


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
            "plan_quota": 12,
            "published_count": 15,
            "contracted_published_count": 12,
            "shortfall_count": 0,
            "early_publication_count": 1,
            "late_recovery_count": 4,
            "post_publish_review": {
                "required_sample_count": 2,
                "pending_count": 0,
            },
            "delivery_warnings": [],
        },
    )

    assert "약정 편수" in html
    assert "스타터 · 월 12편" in html
    assert "그로워 · 월 16편" not in html
    assert "15편" in html
    assert "12편 (기간 전 1편) (마감 후 4편)" in html


# ── 원장 미팅 토킹 포인트(AE 전용) ─────────────────────────────────────
# 원장용 PDF와 같은 숫자를 쓰되, 이 섹션은 내부 PDF에만 있다.


_POINTS = [
    "이번 달 약정 16편 중 12편을 발행했습니다.",
    "환자 질문 100번 중 병원이 나온 횟수는 47번이고, 지난달 39번 → 이번 달 47번 (정상 변동 범위 안입니다).",
    "다음 달 계획: 아직 병원이 나오지 않는 질문을 겨냥해 다음 글의 주제를 정합니다.",
]


def test_ae_report_renders_the_meeting_talking_points():
    html = _render(_sample_attribution(), talking_points=_POINTS)

    assert "원장 미팅 토킹 포인트" in html
    for line in _POINTS:
        assert line in html
    assert "원장 전달용 PDF에는 들어가지 않습니다" in html


def test_ae_report_omits_the_section_when_no_talking_points_exist():
    """구버전 리포트를 다시 열어도 빈 제목만 남지 않는다."""
    html = _render(_sample_attribution(), talking_points=[])

    assert "원장 미팅 토킹 포인트" not in html


def test_v0_report_never_shows_meeting_talking_points():
    html = _render(None, report_type="V0", talking_points=_POINTS)

    assert "원장 미팅 토킹 포인트" not in html


def test_ae_report_shares_the_editorial_rules_without_saas_card_effects():
    """내부 리포트도 원장용·진단서와 같은 헤어라인 편집 체계를 쓴다."""
    html = _render(_sample_attribution(), talking_points=_POINTS)

    assert 'class="masthead"' in html and "#ff3d00" in html
    assert "border-radius" not in html
    assert "box-shadow" not in html
    assert "linear-gradient" not in html
    assert "#1A4B8C" not in html and "#fff8e1" not in html
    assert "nth-child(even)" not in html


def test_gutter_grids_reach_both_rails():
    """나란한 모듈은 본문 양쪽 레일에 정확히 닿아야 한다.

    `border-spacing`으로 단 사이 간격을 만들면 표 바깥쪽에도 같은 간격이 생긴다. 음수
    여백으로 왼쪽만 당기고 폭을 100%로 두면 오른쪽 끝이 간격 두 배만큼 짧아진다 —
    PR #148 첫 렌더에서 KPI 스트립·자산 3단·수행/관측 2단이 모두 레일보다 32pt 짧았다.
    음수 여백을 쓰는 규칙은 같은 간격 두 배를 폭에 더해야 한다.
    """
    import re

    html = _render(_sample_attribution(), talking_points=_POINTS)
    rules = re.findall(r"([.\w-]+) \{([^}]*calc\(-1 \* var\((--s\d)\)\)[^}]*)\}", html)
    names = {name for name, _, _ in rules}
    assert {".metrics", ".asset-grid", ".readout", ".kpi-grid"} <= names
    for name, body, gutter in rules:
        assert f"width:calc(100% + 2 * var({gutter}))" in body, name


def test_reports_carry_the_newvisit_signal_system():
    """보고서 지면은 뉴비짓 소개서와 같은 신호 체계를 쓴다.

    주황은 두 값으로 나눈다. `#ff3d00`은 막대·룰·면처럼 크기가 있는 표시에만 쓰고, 작은
    글자는 흰 바탕 대비 5:1인 `#d13200`으로 쓴다. `#ff3d00` 단독은 3.6:1이라 9pt 라벨이
    읽히지 않는다. 옛 파랑과 청회색이 남으면 두 브랜드가 한 문서에 섞인다.
    """
    html = _render(_sample_attribution(), talking_points=_POINTS)
    assert "--accent:#ff3d00" in html and "--accent-ink:#d13200" in html
    for retired in ("#0672ed", "#525b69", "#dce3ed", "#eef5ff", "#b9c4d2"):
        assert retired not in html, retired
    assert "by Newvisit" in html and "NEWVISIT · RE:PUTATION" in html


def test_ae_report_uses_the_configured_contact_address():
    """문의 주소는 진단 제안서 CTA와 같은 설정값에서 온다. 옛 co.kr 주소가 남지 않는다."""
    html = _render(_sample_attribution(), contact_email="hjkim@motionlabs.kr")
    assert "문의: hjkim@motionlabs.kr" in html
    assert "motionlabs.co.kr" not in html
    # 주소가 비어 있으면 빈 '문의:' 줄을 남기지 않는다.
    assert "문의:" not in _render(_sample_attribution(), contact_email="")
