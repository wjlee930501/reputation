import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput

BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _cell(
    query_key: str,
    platform: str,
    *,
    state: str,
    intent: str = "LOCAL",
    repeats: int = 1,
    mentioned: int = 1,
):
    attempts = (
        tuple(
            CellAttempt(uuid.uuid4(), BASE_TIME, True, index < mentioned)
            for index in range(repeats)
        )
        if state == "SUCCESS"
        else ()
    )
    return ManifestCellInput(
        query_key=query_key,
        query_text=f"환자 질문 {query_key}",
        platform=platform,
        query_intent=intent,
        state=state,
        query_matrix_id=uuid.uuid4(),
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=attempts,
    )


def test_ae_pdf_discloses_counts_and_non_comparable_action_without_raw_codes() -> None:
    payload = build_monthly_sov(
        (
            _cell("q1", "chatgpt", state="SUCCESS"),
            _cell("q1", "gemini", state="FAILED"),
            _cell("q2", "chatgpt", state="EXCLUDED"),
            _cell("q2", "unknown", state="SUCCESS", intent="INFO"),
        ),
        ("chatgpt", "gemini", "unknown"),
    ).to_payload()
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("report.html")

    html = template.render(
        hospital=SimpleNamespace(
            name="테스트의원", region=["서울"], specialties=["내과"], plan="PLAN_12"
        ),
        report_type="MONTHLY",
        period_start=BASE_TIME,
        period_end=BASE_TIME,
        generated_at=BASE_TIME,
        sov_pct=payload["sov_pct"],
        sov_measured=payload["sov_pct"] is not None,
        published_count=0,
        repeat_count=5,
        sov_coverage=payload,
        strategy={
            "query_targets": [
                {
                    "name": "환자 질문 목표",
                    "priority_label": "우선",
                    "priority": "HIGH",
                    "platform_sov": {"unknown": 25.0},
                    "sov_pct": 25.0,
                    "competitor_outcomes": [],
                    "source_backed_count": 0,
                    "successful_measurement_count": 1,
                }
            ],
            "exposure_gaps": [],
            "completed_actions": [],
            "next_month": "2026-09",
            "next_month_actions": [],
            "compliance_caveat": "인과관계로 단정하지 마세요.",
        },
    )

    assert "계획한 3개 조합 중 2개를 측정했고" in html
    assert "1개는 측정하지 못했으며 1개는 사전에 제외했습니다" in html
    assert "기타 AI 서비스" in html
    assert "unknown" not in html
    assert "NON_COMPARABLE" not in html
    assert "SoV" not in html
    assert "PLAN_12" not in html
    assert "스타터 · 월 12편" in html
    assert "AI 답변 내 병원 언급률" in html
    assert payload["comparison"]["next_action"] in html
    # 각주가 사실과 맞아야 한다 — 예전 문구는 "성공 결과 1개만 사용", "통계적 평균값"이었다.
    assert "성공 결과 1개만 사용" not in html
    assert "통계적 평균값" not in html


def test_ae_pdf_footnote_states_the_actual_repeat_sample_and_interval() -> None:
    payload = build_monthly_sov(
        (
            _cell("q1", "chatgpt", state="SUCCESS", repeats=5, mentioned=3),
            _cell("q2", "chatgpt", state="SUCCESS", repeats=5, mentioned=2),
        ),
        ("chatgpt",),
    ).to_payload()
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("report.html")

    html = template.render(
        hospital=SimpleNamespace(
            name="테스트의원", region=["서울"], specialties=["내과"], plan="PLAN_12"
        ),
        report_type="MONTHLY",
        period_start=BASE_TIME,
        period_end=BASE_TIME,
        generated_at=BASE_TIME,
        sov_pct=payload["sov_pct"],
        sov_measured=True,
        published_count=0,
        repeat_count=5,
        sov_coverage=payload,
    )

    assert payload["sov_pct"] == 50.0  # 성공 시도 10건 중 5건 언급
    assert "성공한 반복 측정을 모두 사용해" in html
    assert "질문 2개 ×" in html
    assert "반복 5회 = 측정 10건입니다" in html
    assert "95% 신뢰구간" in html


def test_ae_pdf_footnote_writes_a_repeat_range_when_repeats_were_uneven() -> None:
    """반복 수가 질문마다 다른 달에 평균 하나로 적으면 없던 표본을 말하게 된다."""
    payload = build_monthly_sov(
        (
            _cell("q1", "chatgpt", state="SUCCESS", repeats=5, mentioned=3),
            _cell("q2", "chatgpt", state="SUCCESS", repeats=1, mentioned=0),
        ),
        ("chatgpt",),
    ).to_payload()
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("report.html")

    html = template.render(
        hospital=SimpleNamespace(
            name="테스트의원", region=["서울"], specialties=["내과"], plan="PLAN_12"
        ),
        report_type="MONTHLY",
        period_start=BASE_TIME,
        period_end=BASE_TIME,
        generated_at=BASE_TIME,
        sov_pct=payload["sov_pct"],
        sov_measured=True,
        published_count=0,
        repeat_count=5,
        sov_coverage=payload,
    )

    assert payload["measurement_basis"]["repeat_min"] == 1
    assert payload["measurement_basis"]["repeat_max"] == 5
    assert "반복 1~5회 = 측정 6건입니다" in html
    assert "반복 3회" not in html


def test_ae_pdf_still_renders_a_legacy_payload_without_the_new_keys() -> None:
    """DB에 남아 있는 예전 sov_summary에도 템플릿이 깨지지 않아야 한다."""
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
    template = Environment(loader=FileSystemLoader(template_dir)).get_template("report.html")

    html = template.render(
        hospital=SimpleNamespace(
            name="테스트의원", region=["서울"], specialties=["내과"], plan="PLAN_12"
        ),
        report_type="MONTHLY",
        period_start=BASE_TIME,
        period_end=BASE_TIME,
        generated_at=BASE_TIME,
        sov_pct=40.0,
        sov_measured=True,
        published_count=0,
        repeat_count=5,
        sov_coverage={
            "planned_count": 2,
            "success_count": 2,
            "failed_count": 0,
            "excluded_count": 0,
            "platforms": [],
            "queries": [],
            "cells": [],
            "segments": {},
            "comparison": {"status": "NON_COMPARABLE", "next_action": "이번 달 수치만 전달"},
        },
    )

    assert "95% 신뢰구간" not in html
    assert "계획한 2개 조합 중 2개를 측정했고" in html
