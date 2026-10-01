"""템플릿 갱신의 비교 규칙·API·워커 진입점 — DB 없이."""

import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from test_report_redesign import monthly_view

from app.api.admin import operations
from app.models.operations import OperationRun, OperationRunState
from app.services.doctor_pdf_contracts import DoctorPdfExpectation
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.monthly_period import ReportBuildReason, plan_report_version
from app.services.monthly_template_refresh import (
    RefreshVerdict,
    TemplateRefreshRefused,
    compare_doctor_pdf_facts,
    doctor_view_expectations,
    missing_stored_paths,
    number_tokens,
    numeric_diff,
    pdf_fact_tokens,
)
from app.workers import autonomous_recovery, tasks


def test_numeric_diff_reports_changed_added_and_removed_numbers():
    old = {"a": 1, "b": {"c": [1, 2.5]}, "flag": True, "text": "12편"}
    assert numeric_diff(old, {"a": 1, "b": {"c": [1, 2.5]}, "flag": False, "text": "13편"}) == []
    assert numeric_diff(old, {"a": 2, "b": {"c": [1]}}) == [
        "a: 1.0 → 2.0",
        "b.c[1]: 2.5 → missing",
    ]


def test_missing_stored_paths_names_every_absent_field():
    missing = missing_stored_paths({"published_count": 1}, {"sov_pct": 1})
    assert "content_summary.contract_timing.observed_at" in missing
    assert "sov_summary.comparison.reason" in missing
    assert "content_summary.published_count" not in missing


def test_number_tokens_ignore_wording_but_keep_order():
    assert number_tokens(["약정 12편 중 11편", "100번 중 47번"]) == ["12", "11", "100", "47"]


def test_verdict_status_orders_blocker_over_diff_over_warn():
    verdict = RefreshVerdict()
    verdict.add("WARN", "SOV_RECOMPUTE_DRIFT")
    assert verdict.status == "PASS"
    verdict.add("DIFF", "PUBLISHED_COUNT")
    assert verdict.status == "DIFF"
    verdict.add("BLOCKER", "RECOVERY_WINDOW_OPEN")
    assert verdict.status == "BLOCKED"
    with pytest.raises(TemplateRefreshRefused, match="BLOCKED"):
        raise TemplateRefreshRefused(verdict)


def test_template_refresh_needs_a_version_to_supersede():
    from app.services.monthly_period import MonthlyPeriodError

    with pytest.raises(MonthlyPeriodError):
        plan_report_version(
            latest_report_id=None, latest_version=None,
            reason_code=ReportBuildReason.TEMPLATE_REFRESH, correlation_key="k",
        )
    report_id = uuid.uuid4()
    plan = plan_report_version(
        latest_report_id=report_id, latest_version=3,
        reason_code=ReportBuildReason.TEMPLATE_REFRESH, correlation_key="k",
    )
    assert (plan.version, plan.supersedes_report_id, plan.create) == (4, report_id, True)


# 옛 v3 원장 PDF 1쪽에서 숫자가 실린 문장(배포 전 템플릿 문구 그대로).
OLD_PAGE = (
    "지난달 33.3% 이번 달 66.7% 지난달 대비 +33.4%p 공통 눈금 0–100% · "
    "대상 월 약정 이행 · 12편 중 12편 최초 측정 17% / 이번 관측 67% "
    "확정 반복 합산 언급 비율의 95% 구간: 50.0% ~ 80.0%. 6번 중 6번"
)


def test_pdf_facts_ignore_old_static_scale_and_delta_but_keep_values():
    assert pdf_fact_tokens(OLD_PAGE, ignore=frozenset({"100%", "95%"})) == {
        "33.3%": 1, "66.7%": 1, "17%": 1, "67%": 1, "50.0%": 1, "80.0%": 1,
        "12편중12편": 1, "6번중6번": 1,
    }


def test_pdf_fact_comparison_passes_same_numbers_and_flags_any_change():
    same = (
        "지난달 33.3% 이번 달 66.7% 약속한 글 12편 중 12편 처음 측정 17% / 이번 달 67% "
        "대략 50.0% ~ 80.0% 6번 중 6번 ‘6번 중 2번’은 예시입니다"
    )
    assert compare_doctor_pdf_facts(OLD_PAGE, same) == []
    changed = same.replace("12편 중 12편", "12편 중 11편")
    assert compare_doctor_pdf_facts(OLD_PAGE, changed) == [
        "옛 PDF에만 있음: 12편중12편",
        "새 PDF에만 있음: 12편중11편",
    ]


def _pdf_text(view) -> str:
    expected = DoctorPdfExpectation(
        view["hospital_name"], view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
        appendix_expected=bool(view["appendix_rows"]),
    )
    rendered = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(rendered.pdf_bytes)).pages)


def test_rendering_the_same_view_twice_gives_identical_pdf_facts():
    view = monthly_view(cumulative_published_count=40)
    assert compare_doctor_pdf_facts(_pdf_text(view), _pdf_text(view)) == []


def test_new_pdf_facts_carry_the_stored_rates_and_no_change_in_points():
    coverage = {
        "planned_count": 2, "success_count": 2,
        "comparison": {
            "status": "COMPARABLE", "reason": "MATCHED_COHORT", "matched_cell_count": 2,
            "current_sov_pct": 66.7, "prior_sov_pct": 33.3,
        },
    }
    view = monthly_view(
        sov_pct=66.7, prev_sov_pct=33.3, comparison_reason="MATCHED_COHORT",
        sov_coverage=coverage, published_count=12, plan_quota=12,
    )
    text = _pdf_text(view)
    facts = pdf_fact_tokens(text)
    assert {"33.3%", "66.7%", "12편중12편"} <= set(facts)
    assert "%p" not in "".join(text.split())


def test_doctor_view_expectations_catch_a_tile_that_does_not_match_storage():
    view = monthly_view(published_count=12, plan_quota=12, contract_published_count=12)
    sov = {"sov_pct": 40.0, "comparison": {"reason": "NO_PRIOR_MANIFEST"}}
    content = {
        "published_count": 12,
        "operations": {"plan_quota": 12},
        "contract_timing": {"published_for_contract_count": 12},
    }
    assert doctor_view_expectations(view, sov_summary=sov, content_summary=content) == []
    content["contract_timing"]["published_for_contract_count"] = 11
    assert doctor_view_expectations(view, sov_summary=sov, content_summary=content) == [
        "tile 12편 중 12편 ≠ 12편 중 11편"
    ]


@pytest.mark.asyncio
async def test_template_only_requires_a_rebuild_request():
    with pytest.raises(HTTPException) as caught:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(), year=2025, month=12, rebuild=False, template_only=True,
            payload=None, db=AsyncMock(), idempotency_key="k",
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_template_only_is_refused_during_the_recovery_window(monkeypatch):
    monkeypatch.setattr(operations, "is_monthly_recovery_window", lambda *_a: True)
    with pytest.raises(HTTPException) as caught:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(), year=2025, month=12, rebuild=True, template_only=True,
            payload=operations.MonthlyReportBuildRequest(reason="새 템플릿 반영"),
            db=AsyncMock(), idempotency_key="k",
        )
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_template_only_dispatches_its_own_arguments_and_audit_mode(monkeypatch):
    hospital_id = uuid.uuid4()
    monkeypatch.setattr(operations, "is_monthly_recovery_window", lambda *_a: False)
    monkeypatch.setattr(
        operations, "_get_hospital_or_404", AsyncMock(return_value=SimpleNamespace(id=hospital_id))
    )
    seen: dict = {}

    async def prepare(*_args, **kwargs):
        seen["audit"] = kwargs
        return True

    async def enqueue(*_args, **kwargs):
        seen["args"] = kwargs["args"]
        run = OperationRun(
            id=uuid.uuid4(), hospital_id=hospital_id, operation_type="GENERATE_MONTHLY_REPORT",
            state=OperationRunState.QUEUED, request_payload={},
        )
        return SimpleNamespace(run=run, replayed=False)

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", enqueue)
    await operations.generate_monthly_report_operation(
        hospital_id, year=2025, month=12, rebuild=True, template_only=True,
        payload=operations.MonthlyReportBuildRequest(reason="새 템플릿 반영"),
        db=AsyncMock(), idempotency_key="template-refresh:k",
    )
    assert seen["args"] == [str(hospital_id), 2025, 12, True, False, True]
    assert seen["audit"]["template_only"] is True


def test_lost_template_refresh_dispatch_is_recoverable():
    policy = autonomous_recovery._OPERATION_REDISPATCH_POLICIES["GENERATE_MONTHLY_REPORT"]
    target = str(uuid.uuid4())
    payload = SimpleNamespace(target_id=target, task_args=(target, 2026, 9, True, False, True))
    assert autonomous_recovery._args_match_policy(payload, policy)
    wrong = SimpleNamespace(target_id=target, task_args=(target, 2026, 9, True, 0, True))
    assert not autonomous_recovery._args_match_policy(wrong, policy)


class _Session:
    def __init__(self, hospital):
        self.hospital = hospital
        self.rolled_back = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _model, _pk):
        return self.hospital

    def rollback(self):
        self.rolled_back += 1


def test_refused_template_refresh_fails_the_run_without_retrying(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="가상 의원")
    session = _Session(hospital)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "_hospital_requires_monthly_sov_success", lambda *_a: False)
    monkeypatch.setattr(tasks, "_mark_monthly_operation_run_running", lambda *_a: None)
    verdict = RefreshVerdict()
    verdict.add("DIFF", "PUBLISHED_COUNT", "저장 12 / 재계산 13")

    def refuse(*_args, **_kwargs):
        raise TemplateRefreshRefused(verdict)

    failed: list = []
    monkeypatch.setattr(tasks, "_build_monthly_template_refresh", refuse)
    monkeypatch.setattr(tasks, "_fail_monthly_operation_run", lambda *args: failed.append(args))
    monkeypatch.setattr(
        tasks, "_build_monthly_report_for_hospital",
        lambda *_a, **_k: pytest.fail("일반 재생성으로 떨어지면 안 된다"),
    )

    result = tasks.generate_monthly_report_for_hospital(
        str(hospital.id), 2026, 2, rebuild=True, template_only=True
    )

    assert result["status"] == "template_refresh_refused"
    assert result["verdict"] == "DIFF"
    assert session.rolled_back == 1
    assert len(failed) == 1
