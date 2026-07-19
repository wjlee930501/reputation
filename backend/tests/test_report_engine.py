"""report.html 렌더링 회귀 — repeat_count 동적 표기 + 측정 데이터 없음(None) 표기 (결함 2, 3)."""
from datetime import datetime
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.report_engine import TEMPLATE_DIR, build_strategy_summary


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


def test_strategy_summary_connects_targets_platform_sov_gaps_and_actions():
    target_id = "target-1"
    target = SimpleNamespace(
        id=target_id,
        name="강남 치질 수술 추천",
        priority="HIGH",
        status="ACTIVE",
        platforms=["CHATGPT", "GEMINI"],
        variants=[],
    )
    records = [
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=True,
            source_urls=["https://example.com/a"],
            competitor_mentions=[{"name": "경쟁병원", "is_mentioned": True}],
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q2",
            ai_platform="gemini",
            measurement_status="SUCCESS",
            is_mentioned=False,
            source_urls=[],
            competitor_mentions=[{"name": "경쟁병원", "is_mentioned": False}],
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target_id,
            query_id="q2",
            ai_platform="gemini",
            measurement_status="FAILED",
            is_mentioned=False,
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
    ]
    gap = SimpleNamespace(
        id="gap-1",
        query_target_id=target_id,
        gap_type="LOW_MENTION_SHARE",
        severity="HIGH",
        status="OPEN",
        evidence={"mention_rate": 50.0},
        query_target=target,
    )
    completed = SimpleNamespace(
        id="done-1",
        query_target_id=target_id,
        title="FAQ 콘텐츠 발행",
        description="환자 질문에 답하는 FAQ를 발행했습니다.",
        status="COMPLETED",
        completed_at=datetime(2026, 7, 20),
        due_month="2026-07",
        owner="AE",
        action_type="CONTENT",
        query_target=target,
        gap=gap,
        linked_content=SimpleNamespace(id="content-1", title="치질 수술 FAQ"),
    )
    next_action = SimpleNamespace(
        id="next-1",
        query_target_id=target_id,
        title="공식 근거 자료 보강",
        description="공식 페이지의 병원명과 진료 정보를 정리합니다.",
        status="OPEN",
        completed_at=None,
        due_month="2026-08",
        owner="MotionLabs Ops",
        action_type="SOURCE",
        query_target=target,
        gap=gap,
        linked_content=None,
    )

    summary = build_strategy_summary(
        query_targets=[target],
        sov_records=records,
        exposure_gaps=[gap],
        exposure_actions=[completed, next_action],
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 31, 23, 59, 59),
        next_month="2026-08",
    )

    outcome = summary["query_targets"][0]
    assert outcome["sov_pct"] == 50.0
    assert outcome["platform_sov"] == {"chatgpt": 100.0, "gemini": 0.0}
    assert outcome["source_backed_count"] == 1
    assert outcome["competitor_outcomes"] == [{
        "name": "경쟁병원",
        "observed_count": 2,
        "mention_count": 1,
        "mention_pct": 50.0,
    }]
    assert outcome["successful_measurement_count"] == 2
    assert summary["exposure_gaps"][0]["gap_type"] == "LOW_MENTION_SHARE"
    assert summary["completed_actions"][0]["linked_content_title"] == "치질 수술 FAQ"
    assert summary["next_month_actions"][0]["title"] == "공식 근거 자료 보강"
    assert summary["compliance_caveat"]


def test_monthly_report_renders_data_driven_strategy_instead_of_generic_recommendations():
    strategy = {
        "query_targets": [{
            "name": "강남 치질 수술 추천",
            "priority": "HIGH",
            "sov_pct": 50.0,
            "platform_sov": {"chatgpt": 100.0, "gemini": 0.0},
            "source_backed_count": 1,
            "successful_measurement_count": 2,
        }],
        "exposure_gaps": [{
            "query_target_name": "강남 치질 수술 추천",
            "gap_type": "LOW_MENTION_SHARE",
            "severity": "HIGH",
        }],
        "completed_actions": [{
            "title": "FAQ 콘텐츠 발행",
            "query_target_name": "강남 치질 수술 추천",
            "linked_content_title": "치질 수술 FAQ",
        }],
        "next_month": "2026-08",
        "next_month_actions": [{
            "title": "공식 근거 자료 보강",
            "description": "공식 페이지의 병원 정보를 정리합니다.",
            "query_target_name": "강남 치질 수술 추천",
            "owner": "MotionLabs Ops",
            "due_month": "2026-08",
        }],
        "compliance_caveat": "의료광고 관련 검수 후 실행합니다.",
    }

    html = _render(report_type="MONTHLY", strategy=strategy, attribution=None)

    assert "월간 AI 노출 콘텐츠 운영 리포트" in html
    assert "환자 질문 목표별 AI 노출 결과" in html
    assert "강남 치질 수술 추천" in html
    assert "ChatGPT 100.0%" in html
    assert "LOW_MENTION_SHARE" in html  # legacy payload without display label remains readable
    assert "FAQ 콘텐츠 발행" in html
    assert "공식 근거 자료 보강" in html
    assert "의료광고 관련 검수 후 실행합니다." in html
    assert "리뷰 수집 캠페인 실행" not in html


def test_strategy_summary_excludes_empty_explicit_success_measurement():
    target = SimpleNamespace(
        id="target-1",
        name="강남 치질 수술 추천",
        priority="HIGH",
        status="ACTIVE",
        platforms=["CHATGPT"],
        variants=[],
    )
    records = [
        SimpleNamespace(
            ai_query_target_id=target.id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=True,
            raw_response="장편한외과 언급",
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
        SimpleNamespace(
            ai_query_target_id=target.id,
            query_id="q1",
            ai_platform="chatgpt",
            measurement_status="SUCCESS",
            is_mentioned=False,
            raw_response="",
            source_urls=[],
            competitor_mentions=None,
            measured_at=datetime(2026, 7, 10),
        ),
    ]

    summary = build_strategy_summary(
        query_targets=[target],
        sov_records=records,
        exposure_gaps=[],
        exposure_actions=[],
        period_start=datetime(2026, 7, 1),
        period_end=datetime(2026, 7, 31, 23, 59, 59),
        next_month="2026-08",
    )

    assert summary["query_targets"][0]["sov_pct"] == 100.0
    assert summary["query_targets"][0]["failed_measurement_count"] == 1
