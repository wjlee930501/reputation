import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput

BASE_TIME = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _cell(query_key: str, platform: str, *, state: str, intent: str = "LOCAL"):
    attempts = (
        CellAttempt(uuid.uuid4(), BASE_TIME, True, True),
    ) if state == "SUCCESS" else ()
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
