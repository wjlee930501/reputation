"""템플릿 갱신의 비교 규칙·API·워커 진입점 — DB 없이."""

import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from test_report_plain_language import SCENARIOS, _view
from test_report_redesign import monthly_view

from app.api.admin import operations
from app.models.operations import OperationRun, OperationRunState
from app.services.doctor_pdf_contracts import DoctorPdfExpectation
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.monthly_period import ReportBuildReason, plan_report_version
from app.services.monthly_template_refresh import (
    REQUIRE_STORED_BACKING,
    RefreshVerdict,
    TemplateRefreshRefused,
    compare_doctor_facts,
    doctor_view_expectations,
    doctor_view_facts,
    missing_stored_paths,
    number_tokens,
    numeric_diff,
    pdf_fact_problems,
    stored_only_facts,
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


def _stored_summaries(scenario: dict) -> tuple[dict, dict]:
    """시나리오(뷰 입력)와 같은 값을 저장 요약 모양으로 되돌린다 — 템플릿 갱신은 이 값에서 뷰를 만든다."""
    sov = {**(scenario.get("sov_coverage") or {}), "sov_pct": scenario["sov_pct"]}
    content = {
        "published_count": 0,
        "operations": {"plan_quota": 12, "supplementary_count": 0},
        "contract_timing": {"published_for_contract_count": 0},
        "attribution": scenario.get("attribution"),
        "citations": scenario.get("citations"),
    }
    return sov, content


MONTHLY_SCENARIOS = sorted(name for name in SCENARIOS if name != "initial")


@pytest.mark.parametrize("name", MONTHLY_SCENARIOS)
def test_view_facts_match_facts_rebuilt_from_stored_summaries(name):
    """뷰가 그리는 숫자 사실은 저장 요약에서 다시 만든 사실과 키마다 같다(누락·추가 없음)."""
    view = _view(name)
    sov, content = _stored_summaries(SCENARIOS[name])
    assert doctor_view_expectations(view, sov_summary=sov, content_summary=content) == []
    facts = doctor_view_facts(view)
    assert facts["tile.contract"] == "12편 중 0편"
    # 지난달 참고 값은 저장 요약이 아니라 지난달 보고서가 근거다.
    assert ("rate.reference_previous" in facts) == (name == "method_reference")


@pytest.mark.parametrize("name", ["up", "first", "method_reference"])
def test_every_canonical_fact_is_printed_in_the_rendered_pdf(name):
    """이차 확인: 정본 사실의 값이 실제 렌더된 PDF 본문에 모두 찍힌다."""
    view = _view(name)
    sov, _content = _stored_summaries(SCENARIOS[name])
    facts = {**doctor_view_facts(view), **stored_only_facts(sov)}
    assert pdf_fact_problems(_pdf_text(view), facts) == []


def test_copy_only_changes_do_not_change_the_verdict():
    """2026-10-06 사고: 범위 표기·횟수 문장만 바뀐 같은 숫자는 막히지 않는다."""
    facts = {
        "ci.low": "16.7%", "ci.high": "30.0%",
        "adequacy.planned_slots": "150", "adequacy.confirmed_slots": "150",
        "rate.current": "66.7%", "tile.contract": "12편 중 12편",
    }
    before = "범위 16.7~30.0% 같은 질문을 반복해 150번 중 150번 답을 확인 이번 달 66.7% 약속한 글 12편 중 12편"
    after = "범위 16.7%~30.0% 같은 질문을 반복해 물은 150회 전부 답을 확인 이번 달 66.7% 약속한 글 12편 중 12편"
    assert pdf_fact_problems(before, facts) == []
    assert pdf_fact_problems(after, facts) == []


def test_a_changed_or_dropped_number_is_still_refused():
    facts = {"rate.current": "66.7%", "tile.contract": "12편 중 12편", "ci.low": "8.2%"}
    text = "이번 달 66.7% 약속한 글 12편 중 12편 범위 8.2% ~ 20.0%"
    assert pdf_fact_problems(text, facts) == []
    assert pdf_fact_problems(text.replace("66.7%", "67.7%"), facts) == [
        "새 PDF에서 찾지 못함: rate.current=66.7%"
    ]
    assert pdf_fact_problems(text.replace("12편 중 12편", "12편 중 11편"), facts) == [
        "새 PDF에서 찾지 못함: tile.contract=12편 중 12편"
    ]
    # 저장된 사실을 PDF가 조용히 버리는 일도 막는다(예전에는 stored_facts 뺄셈이 가렸다).
    assert pdf_fact_problems(text.replace("8.2%", ""), facts) == [
        "새 PDF에서 찾지 못함: ci.low=8.2%"
    ]


def test_pdf_fact_check_respects_digit_boundaries():
    """'16.7%'가 있다고 '6.7%'나 '16.75%'가 있는 것은 아니다."""
    assert pdf_fact_problems("이번 달 16.7%", {"rate.current": "6.7%"}) != []
    assert pdf_fact_problems("이번 달 16.75%", {"rate.current": "16.7%"}) != []
    assert pdf_fact_problems("이번 달 16.7%", {"rate.current": "16.7%"}) == []


def test_pdf_fact_check_keeps_counts_not_just_the_set_of_numbers():
    """집합 비교는 개수를 버렸다 — '5개 중 2개'를 '2개 중 5개'로 뒤집어도 숫자 집합은 같다."""
    facts = {"highlight.measured_questions": "5", "highlight.mentioned_questions": "2"}
    assert pdf_fact_problems("질문 5개 중 2개 언급", facts) == []
    assert pdf_fact_problems("질문 5개 중 3개 언급", facts) == [
        "새 PDF에서 찾지 못함: highlight.measured_questions=5"
    ]
    # 3→4: 같은 숫자가 근처 다른 곳에 있어도 '12편 중 3편' 꼴이 그대로 있어야 한다.
    tile = {"tile.contract": "12편 중 3편"}
    assert pdf_fact_problems("약속한 글 12편 중 3편 발행", tile) == []
    assert pdf_fact_problems("약속한 글 12편 중 4편 발행 (3편 12편)", tile) == [
        "새 PDF에서 찾지 못함: tile.contract=12편 중 3편"
    ]


def test_facts_the_template_does_not_draw_are_not_expected_in_the_pdf():
    facts = {"tile.chatgpt": "33.3%", "highlight.published_this_month": "7", "rate.current": "50.0%"}
    assert pdf_fact_problems("이번 달 50.0%", facts) == []


def test_compare_doctor_facts_reports_differences_and_unbacked_view_facts():
    expected = {"rate.current": "66.7%", "tile.contract": "12편 중 12편"}
    actual = {"rate.current": "66.7%", "tile.contract": "12편 중 11편", "rate.reference_previous": "40.0%"}
    problems = compare_doctor_facts(
        expected, actual, require_backing=REQUIRE_STORED_BACKING | {"rate.reference_previous"}
    )
    assert "tile.contract: 저장 12편 중 12편 ≠ 뷰 12편 중 11편" in problems
    assert "저장값 근거 없음: rate.reference_previous=40.0%" in problems
    # 기본값은 지난달 참고 값을 저장 요약 근거로 요구하지 않는다(호출부가 지난달 보고서로 맞춘다).
    assert not any("reference_previous" in problem for problem in compare_doctor_facts(expected, actual))
    assert compare_doctor_facts(expected, {"rate.current": "66.7%"}) == [
        "원장 뷰에 없음: tile.contract=12편 중 12편"
    ]
    # 뷰에만 있는 값이라도 현재 행에서 읽는 누적 발행 편수는 차이가 아니다.
    assert compare_doctor_facts(
        expected, {**expected, "highlight.cumulative_published": "40"}
    ) == []


def test_stored_facts_cover_range_and_slot_counts_for_the_secondary_check():
    sov = {
        "ci95_low": 8.2, "ci95_high": 20.0, "planned_count": 6, "success_count": 6,
        "observation_adequacy": {"planned_slots": 150, "confirmed_slots": 150},
    }
    assert stored_only_facts(sov) == {
        "ci.low": "8.2%", "ci.high": "20.0%",
        "adequacy.planned_slots": "150", "adequacy.confirmed_slots": "150",
        "coverage.planned_count": "6", "coverage.success_count": "6",
    }
    assert stored_only_facts({}) == {}
    assert stored_only_facts({"observation_adequacy": {"planned_slots": "150"}}) == {}


def test_rendering_the_same_view_twice_gives_identical_pdf_facts():
    view = monthly_view(cumulative_published_count=40)
    facts = doctor_view_facts(view)
    assert pdf_fact_problems(_pdf_text(view), facts) == []
    assert facts["highlight.cumulative_published"] == "40"


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
        "tile.contract: 저장 12편 중 11편 ≠ 뷰 12편 중 12편"
    ]


def test_doctor_view_expectations_catch_a_changed_stored_rate_and_question_count():
    scenario = SCENARIOS["up"]
    view = _view("up")
    sov, content = _stored_summaries(scenario)
    sov["comparison"] = {**sov["comparison"], "prior_sov_pct": 31.0}
    content["attribution"] = {
        **content["attribution"],
        "question_rows": content["attribution"]["question_rows"][:1],
    }
    problems = doctor_view_expectations(view, sov_summary=sov, content_summary=content)
    assert "rate.previous: 저장 31.0% ≠ 뷰 30.0%" in problems
    assert any(problem.startswith("highlight.measured_questions") for problem in problems)


@pytest.mark.asyncio
async def test_template_only_requires_a_rebuild_request():
    with pytest.raises(HTTPException) as caught:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(), year=2025, month=12, rebuild=False, template_only=True,
            payload=None, db=AsyncMock(), idempotency_key="k",
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_window", [False, True])
async def test_template_only_dispatches_its_own_arguments_and_audit_mode(
    monkeypatch, recovery_window
):
    """복구 기간에도 API는 받는다 — 막을 병원은 워커의 판정(RECOVERY_PENDING)이 고른다."""
    hospital_id = uuid.uuid4()
    monkeypatch.setattr(operations, "is_monthly_recovery_window", lambda *_a: recovery_window)
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


@pytest.mark.asyncio
async def test_allow_recovery_pending_is_only_for_template_refresh():
    with pytest.raises(HTTPException) as caught:
        await operations.generate_monthly_report_operation(
            uuid.uuid4(), year=2025, month=12, rebuild=True, template_only=False,
            allow_recovery_pending=True,
            payload=operations.MonthlyReportBuildRequest(reason="새 템플릿 반영"),
            db=AsyncMock(), idempotency_key="k",
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_allow_recovery_pending_adds_its_task_argument_and_audit_flag(monkeypatch):
    hospital_id = uuid.uuid4()
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
        allow_recovery_pending=True,
        payload=operations.MonthlyReportBuildRequest(reason="새 템플릿 반영"),
        db=AsyncMock(), idempotency_key="template-refresh:k:allow-recovery-pending",
    )
    assert seen["args"] == [str(hospital_id), 2025, 12, True, False, True, True]
    assert seen["audit"]["allow_recovery_pending"] is True


def test_lost_template_refresh_dispatch_is_recoverable():
    policy = autonomous_recovery._OPERATION_REDISPATCH_POLICIES["GENERATE_MONTHLY_REPORT"]
    target = str(uuid.uuid4())
    payload = SimpleNamespace(target_id=target, task_args=(target, 2026, 9, True, False, True))
    assert autonomous_recovery._args_match_policy(payload, policy)
    allowed = SimpleNamespace(
        target_id=target, task_args=(target, 2026, 9, True, False, True, True)
    )
    assert autonomous_recovery._args_match_policy(allowed, policy)
    wrong = SimpleNamespace(target_id=target, task_args=(target, 2026, 9, True, 0, True))
    assert not autonomous_recovery._args_match_policy(wrong, policy)


@pytest.mark.parametrize(
    ("stored_args", "flags"),
    [
        ([None, 2026, 9, True], (True,)),
        ([None, 2026, 9, True, True], (True, True)),
        ([None, 2026, 9, True, False, True], (True, False, True)),
        ([None, 2026, 9, True, False, True, True], (True, False, True, True)),
    ],
)
def test_lost_template_refresh_is_redispatched_as_a_template_refresh(stored_args, flags):
    """유실된 템플릿 갱신을 일반 재생성으로 되살리면 지금 행으로 숫자를 다시 센다."""
    run = SimpleNamespace(
        request_payload={"_dispatch": {"task_args": stored_args}}, result_summary={}
    )
    assert autonomous_recovery._stored_monthly_report_flags(run) == flags


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


@pytest.mark.parametrize(
    ("template_only", "allow", "gated"),
    [(True, False, True), (False, True, True), (True, True, False)],
)
def test_measurement_gate_is_skipped_only_for_an_allowed_template_refresh(
    monkeypatch, template_only, allow, gated
):
    """필수 측정 관문은 운영자가 명시한 템플릿 갱신에서만 건너뛴다 — 숫자는 저장값을 옮긴다."""
    hospital = SimpleNamespace(id=uuid.uuid4(), name="가상 의원")
    session = _Session(hospital)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "_hospital_requires_monthly_sov_success", lambda *_a: True)
    monkeypatch.setattr(tasks, "_monthly_sov_measurement_succeeded", lambda *_a: False)
    monkeypatch.setattr(tasks, "_mark_monthly_report_measurement_incomplete", lambda *_a: None)
    monkeypatch.setattr(tasks, "_mark_monthly_operation_run_running", lambda *_a: None)
    seen: dict = {}

    def refresh(*_args, **kwargs):
        seen["allow"] = kwargs["allow_recovery_pending"]
        return "created"

    monkeypatch.setattr(tasks, "_build_monthly_template_refresh", refresh)
    monkeypatch.setattr(tasks, "_build_monthly_report_for_hospital", lambda *_a, **_k: "created")
    monkeypatch.setattr(tasks, "_finish_monthly_operation_run", lambda *_a, **_k: None, raising=False)

    result = tasks.generate_monthly_report_for_hospital(
        str(hospital.id), 2026, 2, rebuild=True, template_only=template_only,
        allow_recovery_pending=allow,
    )

    if gated:
        assert result["status"] == "measurement_not_succeeded"
        assert "allow" not in seen
    else:
        assert seen["allow"] is True


def test_last_month_reference_value_is_backed_by_last_months_stored_report(monkeypatch):
    """측정 방식이 바뀐 달의 '지난달(참고)' 칸은 지난달 보고서에 저장된 값 — 그 값만 허용한다."""
    from app.utils import monthly_template_refresh as refresh_cli

    seen = {}

    def reported(_db, hospital_id, year, month):
        seen["period"] = (hospital_id, year, month)
        return 40.0

    monkeypatch.setattr(tasks, "reported_monthly_sov_pct", reported)
    report = SimpleNamespace(hospital_id="h-1", period_year=2026, period_month=9)

    backing = refresh_cli._prior_reference_fact(None, report)

    assert backing == {"rate.reference_previous": "40.0%"}
    assert seen["period"] == ("h-1", 2026, 8)
    keys = frozenset(backing)
    assert compare_doctor_facts(backing, {"rate.reference_previous": "40.0%"}, require_backing=keys) == []
    assert compare_doctor_facts(backing, {"rate.reference_previous": "41.0%"}, require_backing=keys) == [
        "rate.reference_previous: 저장 40.0% ≠ 뷰 41.0%"
    ]
    # 지난달 값이 없는데 새 뷰가 참고 값을 적으면 근거 없는 숫자다.
    monkeypatch.setattr(tasks, "reported_monthly_sov_pct", lambda *_a: None)
    assert refresh_cli._prior_reference_fact(None, report) == {}
    assert compare_doctor_facts({}, {"rate.reference_previous": "41.0%"}, require_backing=keys) == [
        "저장값 근거 없음: rate.reference_previous=41.0%"
    ]
    monkeypatch.setattr(tasks, "reported_monthly_sov_pct", reported)
    refresh_cli._prior_reference_fact(None, SimpleNamespace(hospital_id="h-1", period_year=2027, period_month=1))
    assert seen["period"] == ("h-1", 2026, 12)


# ── 실행 단계: 측정 미완료 병원(--allow-recovery-pending)은 PARTIAL·BLOCKED로 끝나도 성공이다 ──


@pytest.mark.parametrize(
    ("state", "stage", "version", "allow", "ok"),
    [
        ("SUCCEEDED", "ARTIFACT_VALIDATED", 4, False, True),
        ("PARTIAL", "BLOCKED", 4, True, True),
        # 플래그가 없으면 종전처럼 PARTIAL은 멈춘다.
        ("PARTIAL", "BLOCKED", 4, False, False),
        # 새 버전이 생기지 않았으면(옛 버전만 있음) 갱신 성공이 아니다.
        ("PARTIAL", "BLOCKED", 3, True, False),
        ("PARTIAL", "BLOCKED", None, True, False),
        ("PARTIAL", "ARTIFACT_VALIDATED", 4, True, False),
        ("FAILED", "FAILED", None, True, False),
        ("CANCELLED", None, None, True, False),
        ("TIMEOUT", None, None, True, False),
    ],
)
def test_run_counts_as_success(state, stage, version, allow, ok):
    from app.utils import monthly_template_refresh as refresh_cli

    assert refresh_cli._run_counts_as_success(
        state, stage, version, previous_version=3, allow_recovery_pending=allow
    ) is ok


def _execute_with(monkeypatch, *, run, post_status, allow):
    """run_execute를 네트워크·DB 없이 돌린다. run은 (상태, 단계, 보고서 버전), 반환은 (종료 코드, 사후 확인 호출 수)."""
    from contextlib import contextmanager

    from app.utils import monthly_template_refresh as refresh_cli

    hospital_id = uuid.uuid4()
    verdict = RefreshVerdict()
    result = refresh_cli.HospitalResult(hospital_id, "가상 의원", 3, verdict)
    post_verdict = RefreshVerdict()
    if post_status == "DIFF":
        post_verdict.add("DIFF", "DOCTOR_PDF_NUMBER", "x")
    post_calls: list = []

    @contextmanager
    def fake_client(**_kwargs):
        yield object()

    monkeypatch.setattr(refresh_cli.settings, "ADMIN_SECRET_KEY", "test-key")
    monkeypatch.setattr(refresh_cli.httpx, "Client", fake_client)
    monkeypatch.setattr(refresh_cli, "run_precheck", lambda *_a, **_k: [result])
    monkeypatch.setattr(refresh_cli, "_request_refresh", lambda *_a, **_k: "run-1")
    monkeypatch.setattr(refresh_cli, "_wait_for_run", lambda *_a, **_k: run)

    def post(*_args):
        post_calls.append(1)
        return [refresh_cli.HospitalResult(hospital_id, "가상 의원", 4, post_verdict)]

    monkeypatch.setattr(refresh_cli, "run_postcheck", post)
    code = refresh_cli.run_execute(
        2026, 9, [], reason="새 템플릿", api_base="https://api.invalid", confirm=True,
        timeout=1.0, allow_recovery_pending=allow,
    )
    return code, len(post_calls)


def test_execute_continues_to_postcheck_when_a_measurement_incomplete_report_ends_partial(monkeypatch):
    code, post_calls = _execute_with(
        monkeypatch, run=("PARTIAL", "BLOCKED", 4), post_status="PASS", allow=True
    )
    assert (code, post_calls) == (0, 1)


def test_execute_stops_on_partial_without_the_recovery_pending_flag(monkeypatch):
    code, post_calls = _execute_with(
        monkeypatch, run=("PARTIAL", "BLOCKED", 4), post_status="PASS", allow=False
    )
    assert (code, post_calls) == (1, 0)


def test_execute_stops_on_a_failed_run_even_with_the_flag(monkeypatch):
    code, post_calls = _execute_with(
        monkeypatch, run=("FAILED", "FAILED", None), post_status="PASS", allow=True
    )
    assert (code, post_calls) == (1, 0)


def test_execute_still_stops_on_a_postcheck_diff_after_a_partial_run(monkeypatch):
    code, post_calls = _execute_with(
        monkeypatch, run=("PARTIAL", "BLOCKED", 4), post_status="DIFF", allow=True
    )
    assert (code, post_calls) == (1, 1)
