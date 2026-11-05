"""경쟁 병원 뒤늦은 등록 — 재판정 합치기·사실 대조·API·재배달 계약(DB 없음)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.admin import operations
from app.models.operations import OperationRun, OperationRunState
from app.services import competitor_rejudge as cr
from app.services.doctor_report_v4 import (
    compare_facts_excluding_competitors,
    competitor_fact_changes,
)
from app.services.monthly_period import ReportBuildReason, plan_report_version
from app.workers import autonomous_recovery, competitor_refresh, tasks

JUDGED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


# ── 이름·판정 합치기 ─────────────────────────────────────────────────────────


def test_pending_names_skip_judged_and_retry_unresolved():
    mentions = [
        {"name": "가나 의원", "is_mentioned": True, "mention_rank": 1},
        {"name": "다라의원", "is_mentioned": None, "verdict": "UNRESOLVED"},
        "garbage",
    ]
    registered = ["가나의원", "다라 의원", "마바 클리닉"]
    # 표기(띄어쓰기)가 달라도 같은 이름이고, 미확정(null)은 다시 묻는다.
    assert cr.pending_competitor_names(mentions, registered) == ["다라 의원", "마바 클리닉"]
    assert cr.pending_competitor_names(None, registered) == registered
    assert cr.pending_competitor_names(mentions, []) == []


def test_merge_keeps_existing_judgments_and_replaces_only_unresolved():
    existing = [
        {"name": "가나의원", "is_mentioned": False, "mention_rank": None},
        {"name": "다라의원", "is_mentioned": None, "verdict": "UNRESOLVED"},
    ]
    judged = [
        {"name": "가나의원", "is_mentioned": True, "mention_rank": 1},  # 측정 당시 판정은 덮지 않는다
        {"name": "다라의원", "is_mentioned": True, "mention_rank": 2},
        {"name": "마바클리닉", "is_mentioned": False, "mention_rank": None},
    ]
    merged = cr.merge_competitor_mentions(existing, judged)
    assert merged == [
        {"name": "가나의원", "is_mentioned": False, "mention_rank": None},
        {"name": "다라의원", "is_mentioned": True, "mention_rank": 2},
        {"name": "마바클리닉", "is_mentioned": False, "mention_rank": None},
    ]
    # 등록에서 빠진 이름은 지우지 않는다 — 합치기는 덧붙이기만 한다.
    assert cr.merge_competitor_mentions(existing, []) == existing


def test_align_judgments_uses_registered_spelling_and_reports_missing_names():
    parsed = [
        {"name": "가나 의원", "is_mentioned": True, "mention_rank": 3},
        {"name": "엉뚱한병원", "is_mentioned": True, "mention_rank": 1},
        {"name": "다라의원", "is_mentioned": "yes"},
    ]
    judged, missing = cr.align_judgments(["가나의원", "다라의원", "마바클리닉"], parsed, judged_at=JUDGED_AT)
    assert judged == [
        {"name": "가나의원", "is_mentioned": True, "mention_rank": 3, "verdict": "REJUDGED", "judged_at": JUDGED_AT.isoformat()}
    ]
    assert missing == ["다라의원", "마바클리닉"]
    entry = cr.unresolved_entry("다라의원", reason="ValueError:competitor_parse_failed", judged_at=JUDGED_AT)
    assert entry["is_mentioned"] is None and entry["verdict"] == "UNRESOLVED"
    # 미확정은 보고서 집계(bool만 센다)에 들어가지 않고, 다음 실행이 다시 묻는다.
    assert cr.pending_competitor_names([entry], ["다라의원"]) == ["다라의원"]


def test_registered_names_are_trimmed_and_deduplicated():
    hospital = SimpleNamespace(competitors=[" 가나의원 ", "가나의원", "", None, "다라의원"])
    assert cr.registered_competitor_names(hospital) == ["가나의원", "다라의원"]
    assert cr.registered_competitor_names(SimpleNamespace(competitors=None)) == []


# ── 저장 전략 요약의 경쟁 집계 ───────────────────────────────────────────────────


def _record(target_id, query_id, *, mentions, status="SUCCESS", verdict="MATCHED"):
    return SimpleNamespace(
        ai_query_target_id=target_id,
        query_id=query_id,
        competitor_mentions=mentions,
        measurement_status=status,
        mention_verdict=verdict,
        is_mentioned=True,
        ai_platform="chatgpt",
        measured_at=datetime(2026, 10, 15, tzinfo=UTC),
        source_urls=[],
        raw_response="가상 답변",
    )


def test_strategy_competitor_outcomes_are_recounted_per_target_only():
    target_a, target_b, query_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    attribution = {
        "query_targets": [
            {"id": str(target_a), "sov_pct": 50.0, "competitor_outcomes": []},
            {"id": str(target_b), "sov_pct": 0.0, "competitor_outcomes": [{"name": "옛이름", "mention_pct": 100.0}]},
        ],
        "exposure_gaps": [{"keep": 1}],
    }
    targets = [
        SimpleNamespace(id=target_a, variants=[]),
        SimpleNamespace(id=target_b, variants=[SimpleNamespace(query_matrix_id=query_b)]),
    ]
    records = [
        _record(target_a, None, mentions=[{"name": "가나의원", "is_mentioned": True}]),
        _record(target_a, None, mentions=[{"name": "가나의원", "is_mentioned": False}, {"name": "다라의원", "is_mentioned": None}]),
        _record(None, query_b, mentions=[{"name": "가나의원", "is_mentioned": True}]),
        _record(target_a, None, mentions=[{"name": "가나의원", "is_mentioned": True}], status="FAILED"),
    ]
    refreshed = cr.refresh_competitor_outcomes(attribution, records, targets)
    assert refreshed is not None
    rows = {row["id"]: row for row in refreshed["query_targets"]}
    assert rows[str(target_a)]["competitor_outcomes"] == [
        {"name": "가나의원", "observed_count": 2, "mention_count": 1, "mention_pct": 50.0}
    ]
    assert rows[str(target_b)]["competitor_outcomes"] == [
        {"name": "가나의원", "observed_count": 1, "mention_count": 1, "mention_pct": 100.0}
    ]
    assert rows[str(target_a)]["sov_pct"] == 50.0 and refreshed["exposure_gaps"] == [{"keep": 1}]
    assert attribution["query_targets"][1]["competitor_outcomes"][0]["name"] == "옛이름"  # 입력은 그대로
    assert cr.refresh_competitor_outcomes(None, records, targets) is None


# ── 보고서 조립은 등록 경쟁사로 거른다 ─────────────────────────────────────────


def test_v4_competition_counts_only_currently_registered_competitors():
    from test_doctor_report_v4 import _hospital, _records, build

    everyone = build(records=_records())
    assert {row.name for row in everyone.competition.rows} == {"가상동 다른의원", "가상 중앙의원"}

    scoped = build(hospital=_hospital(competitors=["가상 중앙 의원"]), records=_records())
    # 등록 표기로 이름을 맞춘다 — 기록의 "가상 중앙의원"이 등록한 "가상 중앙 의원"으로 보인다.
    assert [row.name for row in scoped.competition.rows] == ["가상 중앙 의원"]
    assert scoped.competition.basis == "등록한 경쟁 병원 1곳 기준"
    assert all("가상동 다른의원" not in row.competitors for row in scoped.competition.replaced)
    facts = scoped.facts()
    assert "competitor.가상동 다른의원" not in facts and "competitor.가상 중앙 의원" in facts
    # 다른 사실은 그대로다 — 경쟁 사실만 다르다.
    assert {k: v for k, v in everyone.facts().items() if not k.startswith("competitor.")} == {
        k: v for k, v in facts.items() if not k.startswith("competitor.")
    }
    # 등록은 있는데 기록에 그 이름의 판정이 없으면 '관측 없음' 문구다(0으로 세지 않는다).
    unseen = build(hospital=_hospital(competitors=["처음 보는 의원"]), records=_records())
    assert unseen.competition.rows == () and "확인하지 못했습니다" in unseen.competition.empty_text


# ── 숫자 보존 판정: 경쟁 사실만 바뀌어도 된다 ──────────────────────────────────


def test_fact_comparison_excludes_only_competitor_keys():
    frozen = {"template": "v4", "card.rate": "40%", "competitor.가나의원": "4건 중 1건", "competitor.가나의원.rate": "25%"}
    view = {"template": "v4", "card.rate": "40%", "competitor.가나의원": "4건 중 2건", "competitor.가나의원.rate": "50%", "competitor.다라의원": "4건 중 0건"}
    assert compare_facts_excluding_competitors(frozen, view) == []
    assert competitor_fact_changes(frozen, view) == [
        "competitor.가나의원: 4건 중 1건 → 4건 중 2건",
        "competitor.가나의원.rate: 25% → 50%",
        "competitor.다라의원: 없음 → 4건 중 0건",
    ]
    assert compare_facts_excluding_competitors(frozen, {**view, "card.rate": "41%"}) == ["card.rate: 얼린 40% ≠ 뷰 41%"]
    assert compare_facts_excluding_competitors(frozen, {**view, "card.cumulative": "9편"}) == ["얼린 사실 없음: card.cumulative=9편"]
    assert compare_facts_excluding_competitors(frozen, {"template": "v4"})[0].startswith("원장 뷰에 없음: card.rate")


def test_competitor_refresh_is_a_new_version_over_an_existing_report():
    report_id = uuid.uuid4()
    plan = plan_report_version(
        latest_report_id=report_id, latest_version=2,
        reason_code=ReportBuildReason.COMPETITOR_REFRESH, correlation_key="k",
    )
    assert (plan.version, plan.supersedes_report_id, plan.create) == (3, report_id, True)
    with pytest.raises(Exception):
        plan_report_version(
            latest_report_id=None, latest_version=None,
            reason_code=ReportBuildReason.COMPETITOR_REFRESH, correlation_key="k",
        )


# ── 재배달·태스크 인자 계약 ─────────────────────────────────────────────────


def test_lost_competitor_refresh_child_is_redispatched_with_its_flag():
    args = (str(uuid.uuid4()), 2026, 10, *competitor_refresh.REPORT_REFRESH_TASK_FLAGS)
    run = OperationRun(
        id=uuid.uuid4(), hospital_id=uuid.UUID(args[0]), operation_type="GENERATE_MONTHLY_REPORT",
        state=OperationRunState.QUEUED,
        request_payload={"_dispatch": {"target_type": "hospital", "target_id": args[0], "queue": "reports", "task_args": list(args)}},
    )
    assert autonomous_recovery._stored_monthly_report_flags(run) == (True, False, True, False, True)
    policy = autonomous_recovery._OPERATION_REDISPATCH_POLICIES["GENERATE_MONTHLY_REPORT"]
    assert (autonomous_recovery._INTEGER_ARG, autonomous_recovery._INTEGER_ARG, True, False, True, False, True) in policy.allowed_arg_suffixes
    rejudge = autonomous_recovery._OPERATION_REDISPATCH_POLICIES["REJUDGE_COMPETITORS"]
    assert rejudge.task_name == cr.REJUDGE_TASK_NAME and rejudge.queue == cr.REJUDGE_QUEUE
    assert "REJUDGE_COMPETITORS" in autonomous_recovery._LEASE_TAKEOVER_TYPES


def test_competitor_refresh_requires_template_only_mode(monkeypatch):
    """경쟁 병원 반영은 템플릿 갱신의 변형이다 — 일반 재생성 인자에 섞이면 실행을 FAILED로 닫고 숫자를 다시 세지 않는다."""
    hospital = SimpleNamespace(id=uuid.uuid4(), name="가상 의원")

    class _Db:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, _model, _key):
            return hospital

    failed: list = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", _Db)
    monkeypatch.setattr(tasks, "_fail_monthly_operation_run", lambda *args: failed.append(args))
    result = tasks.generate_monthly_report_for_hospital.run(
        str(hospital.id), 2025, 12, True, False, False, False, True
    )
    assert result == {"status": "invalid_mode"}
    assert failed and failed[0][2] == hospital.id


def test_rejudge_task_is_routed_and_signed_explicitly():
    from app.core.celery_app import celery_app
    from app.workers.dispatch_envelope import FIRST_ARG_TARGET_TASKS, expected_purpose

    assert celery_app.conf.task_routes[cr.REJUDGE_TASK_NAME] == {"queue": cr.REJUDGE_QUEUE}
    assert expected_purpose(cr.REJUDGE_TASK_NAME) == cr.REJUDGE_DISPATCH_PURPOSE
    assert cr.REJUDGE_TASK_NAME in FIRST_ARG_TARGET_TASKS
    assert competitor_refresh.rejudge_month_competitors.name == cr.REJUDGE_TASK_NAME


# ── API ─────────────────────────────────────────────────────────────────────


def _plan(hospital_id, *, report=True, pending=3, records=10):
    return cr.RejudgePlan(
        hospital_id=hospital_id, year=2025, month=12, registered_names=("가나의원",),
        manifest_id=uuid.uuid4(), report_id=uuid.uuid4() if report else None,
        report_version=2 if report else None, records_total=records,
        targets=tuple(cr.RejudgeTarget(uuid.uuid4(), ("가나의원",)) for _ in range(pending)),
    )


def _db(plan):
    db = AsyncMock()
    db.run_sync = AsyncMock(return_value=plan)
    return db


@pytest.mark.asyncio
async def test_competitor_refresh_requires_an_idempotency_key():
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(uuid.uuid4(), year=2025, month=12, payload=None, db=AsyncMock(), idempotency_key=None)
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_competitor_refresh_refuses_without_registered_competitors(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), competitors=[])
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=hospital))
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=AsyncMock(), idempotency_key="k")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "NO_REGISTERED_COMPETITORS"


@pytest.mark.asyncio
async def test_competitor_refresh_refuses_without_a_report_or_while_in_flight(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), competitors=["가나의원"])
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=hospital))
    monkeypatch.setattr(operations, "_competitor_refresh_runs", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=_db(_plan(hospital.id, report=False)), idempotency_key="k")
    assert caught.value.detail["code"] == "NO_REPORT"

    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=_db(_plan(hospital.id, pending=0, records=0)), idempotency_key="k")
    assert caught.value.detail["code"] == "NO_FROZEN_RECORDS"

    order: list[str] = []

    async def prepare(*_args, **_kwargs):
        order.append("lock+audit")
        return True

    async def runs(*_args, **_kwargs):
        order.append("active-check")
        return [OperationRun(id=uuid.uuid4(), hospital_id=hospital.id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.RUNNING, idempotency_key="other", request_payload={})]

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_competitor_refresh_runs", runs)
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=_db(_plan(hospital.id)), idempotency_key="k")
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "COMPETITOR_REFRESH_IN_FLIGHT"
    # 진행 중 판정은 병원 잠금(사유 감사 준비) 뒤에 한다 — 키가 다른 두 요청이 체인을 겹쳐 시작하지 않게.
    assert order == ["lock+audit", "active-check"]

    # 부모는 끝났지만 자식 보고서 실행이 아직 열려 있으면 체인은 진행 중이다.
    done_parent = OperationRun(id=uuid.uuid4(), hospital_id=hospital.id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.SUCCEEDED, idempotency_key="other", request_payload={})
    monkeypatch.setattr(operations, "_competitor_refresh_runs", AsyncMock(return_value=[done_parent]))
    db = _db(_plan(hospital.id))
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: OperationRunState.QUEUED))
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=db, idempotency_key="k")
    assert caught.value.detail["code"] == "COMPETITOR_REFRESH_IN_FLIGHT"


@pytest.mark.asyncio
async def test_competitor_refresh_dispatches_the_rejudge_chain_with_audit_and_receipt(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), competitors=["가나의원"])
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=hospital))
    monkeypatch.setattr(operations, "_competitor_refresh_runs", AsyncMock(return_value=[]))
    seen: dict = {}

    async def prepare(*_args, **kwargs):
        seen["audit"] = kwargs
        return True

    async def enqueue(*_args, **kwargs):
        seen["enqueue"] = kwargs
        run = OperationRun(id=uuid.uuid4(), hospital_id=hospital.id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.QUEUED, request_payload={})
        return SimpleNamespace(run=run, replayed=False)

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", enqueue)
    plan = _plan(hospital.id)
    response = await operations.request_competitor_refresh(
        hospital.id, year=2025, month=12, payload=operations.CompetitorRefreshRequest(reason="경쟁사 추가"),
        db=_db(plan), idempotency_key="competitor-refresh:k",
    )
    assert seen["audit"]["mode"] == "COMPETITOR_REFRESH" and seen["audit"]["reason"] == "경쟁사 추가"
    assert seen["enqueue"]["args"] == [str(hospital.id), 2025, 12]
    assert seen["enqueue"]["queue"] == "sov"
    assert seen["enqueue"]["task"] is competitor_refresh.rejudge_month_competitors
    assert seen["enqueue"]["request_payload_extra"]["pending_records"] == 3
    assert response["pending_records"] == 3 and response["registered_competitors"] == ["가나의원"]
    assert response["redeliver_required"] is True and response["idempotent_replay"] is False
    assert "다시 전달" in response["detail"]


@pytest.mark.asyncio
async def test_competitor_refresh_replay_with_a_different_month_is_rejected(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), competitors=["가나의원"])
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=hospital))
    monkeypatch.setattr(operations, "_competitor_refresh_runs", AsyncMock(return_value=[]))

    async def prepare(*_args, **_kwargs):
        return False

    async def enqueue(*_args, **_kwargs):
        run = OperationRun(
            id=uuid.uuid4(), hospital_id=hospital.id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.QUEUED,
            request_payload={"_dispatch": {"target_type": "hospital", "target_id": str(hospital.id), "queue": "sov", "task_args": [str(hospital.id), 2025, 11]}},
        )
        return SimpleNamespace(run=run, replayed=True)

    monkeypatch.setattr(operations, "_prepare_monthly_rebuild_audit", prepare)
    monkeypatch.setattr(operations, "_enqueue_with_truthful_audit", enqueue)
    with pytest.raises(HTTPException) as caught:
        await operations.request_competitor_refresh(hospital.id, year=2025, month=12, payload=None, db=_db(_plan(hospital.id)), idempotency_key="k")
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_competitor_refresh_status_reports_pending_and_latest_chain(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), competitors=["가나의원"])
    monkeypatch.setattr(operations, "_get_hospital_or_404", AsyncMock(return_value=hospital))
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.SUCCEEDED,
        request_payload={}, result_summary={"stage": "REPORT_REFRESH_QUEUED", "period_year": 2025, "period_month": 12,
                                            "rejudge": {"records_total": 10, "pending_records": 3, "judged_records": 2, "unresolved_records": 1, "judged_names": 2, "unresolved_names": 1, "cost_blocked": False}},
    )
    child = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type="GENERATE_MONTHLY_REPORT", state=OperationRunState.SUCCEEDED, parent_run_id=parent.id,
        request_payload={}, result_summary={"stage": "ARTIFACT_VALIDATED", "report_version": 3, "report_id": str(uuid.uuid4())},
    )
    monkeypatch.setattr(operations, "_competitor_refresh_runs", AsyncMock(return_value=[parent]))
    db = _db(_plan(hospital.id, pending=0))
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: child))
    status = await operations.competitor_refresh_status(hospital.id, year=2025, month=12, db=db)
    assert status["pending_records"] == 0 and status["registered_competitors"] == ["가나의원"]
    latest = status["latest_run"]
    assert latest["judged_records"] == 2 and latest["unresolved_records"] == 1
    assert latest["report_run"]["report_version"] == 3 and latest["report_run"]["stage"] == "ARTIFACT_VALIDATED"
    assert operations._stored_run_period(parent) == (2025, 12)


# ── 판정 재시도·등록 필터·실행 정책 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_incomplete_judgment_answers_are_retried_for_the_missing_names_only():
    asked: list[list[str]] = []

    async def judge(names, _raw):
        asked.append(list(names))
        if len(asked) == 1:
            return [{"name": "가나의원", "is_mentioned": True, "mention_rank": 1}]  # 다라의원 누락
        if len(asked) == 2:
            raise ValueError("competitor_parse_failed")
        return [{"name": "다라의원", "is_mentioned": False, "mention_rank": None}]

    judged, missing, error = await cr._judge_with_retries(judge, ["가나의원", "다라의원"], "원문", judged_at=JUDGED_AT)
    assert asked == [["가나의원", "다라의원"], ["다라의원"], ["다라의원"]]
    assert [item["name"] for item in judged] == ["가나의원", "다라의원"] and missing == [] and error is None

    async def empty(_names, _raw):
        return []

    judged, missing, error = await cr._judge_with_retries(empty, ["가나의원"], "원문", judged_at=JUDGED_AT)
    assert judged == [] and missing == ["가나의원"] and error == "competitor_name_missing_in_judgment"


def test_strategy_competitor_outcomes_respect_current_registration():
    target = uuid.uuid4()
    strategy = {"query_targets": [{"id": str(target), "competitor_outcomes": []}]}
    records = [_record(target, None, mentions=[{"name": "가나 의원", "is_mentioned": True}, {"name": "옛경쟁의원", "is_mentioned": True}])]
    refreshed = cr.refresh_competitor_outcomes(strategy, records, [SimpleNamespace(id=target, variants=[])], registered_names=["가나의원"])
    assert [row["name"] for row in refreshed["query_targets"][0]["competitor_outcomes"]] == ["가나의원"]
    everyone = cr.refresh_competitor_outcomes(strategy, records, [SimpleNamespace(id=target, variants=[])])
    assert len(everyone["query_targets"][0]["competitor_outcomes"]) == 2


def test_missing_case_does_not_claim_non_mention_from_unresolved_judgments():
    from test_doctor_report_v4 import _hospital, build
    from test_doctor_report_v4 import _record as v4_record

    unresolved = [{"name": "가나의원", "is_mentioned": None, "verdict": "UNRESOLVED"}]
    records = [
        v4_record("가상동 위내시경 가능한 병원", mentioned=False, competitors=unresolved),
    ]
    view = build(hospital=_hospital(competitors=["가나의원"]), records=records)
    assert view.cases.missing is not None
    assert "경쟁 병원도 언급되지 않았습니다" not in view.cases.missing.insight
    confirmed = build(
        hospital=_hospital(competitors=["가나의원"]),
        records=[v4_record("가상동 위내시경 가능한 병원", mentioned=False, competitors=[{"name": "가나의원", "is_mentioned": False}])],
    )
    assert "경쟁 병원도 언급되지 않았습니다" in confirmed.cases.missing.insight


def test_rejudge_task_has_an_operation_policy_and_requires_a_run():
    from app.services.operation_terminal_outcomes import terminal_outcome_identity
    from app.workers.generation_run_control import _OPERATION_TASK_POLICIES, operation_run_required

    assert _OPERATION_TASK_POLICIES[cr.REJUDGE_TASK_NAME] == ("REJUDGE_COMPETITORS", "hospital", "sov")
    assert operation_run_required(cr.REJUDGE_TASK_NAME)
    hospital_id = uuid.uuid4()
    run = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital_id, operation_type="REJUDGE_COMPETITORS", state=OperationRunState.FAILED,
        safe_error_code=None,
        request_payload={"_dispatch": {"target_type": "hospital", "target_id": str(hospital_id), "queue": "sov", "task_args": [str(hospital_id), 2026, 10]}},
        result_summary={},
    )
    identity = terminal_outcome_identity(run)
    assert identity is not None and identity.period == "2026-10"


# ── 2차 검토 반영: 표기 통합·혼합 판정·전략 요약 투영 ─────────────────────────────


def test_competitor_aliases_collapse_into_the_registered_spelling():
    from test_doctor_report_v4 import _hospital, build
    from test_doctor_report_v4 import _record as v4_record

    from app.services.competitor_scope import scope_competitor_mentions, scope_competitor_records

    mentions = [
        {"name": "가나 의원", "is_mentioned": None, "verdict": "UNRESOLVED"},
        {"name": "가나의원", "is_mentioned": True, "mention_rank": 1},
        {"name": "옛경쟁의원", "is_mentioned": True},
    ]
    assert scope_competitor_mentions(mentions, ["가나 의원"]) == [{"name": "가나 의원", "is_mentioned": True, "mention_rank": 1}]
    assert scope_competitor_records([SimpleNamespace(competitor_mentions=mentions)], []) [0].competitor_mentions is mentions

    records = [
        v4_record("가상동 위내시경 가능한 병원", mentioned=False, competitors=[{"name": "가나 의원", "is_mentioned": True, "mention_rank": 1}]),
        v4_record("가상동 심장초음파 잘하는 병원", mentioned=True, competitors=[{"name": "가나의원", "is_mentioned": False}]),
    ]
    view = build(hospital=_hospital(competitors=["가나의원"]), records=records)
    assert [(row.name, row.count_label, row.rate) for row in view.competition.rows] == [("가나의원", "2건 중 1건", 50.0)]
    assert view.competition.basis == "등록한 경쟁 병원 1곳 기준"


def test_mixed_false_and_unresolved_judgments_do_not_claim_non_mention():
    from test_doctor_report_v4 import _hospital, build
    from test_doctor_report_v4 import _record as v4_record

    mixed = [{"name": "가나의원", "is_mentioned": False}, {"name": "다라의원", "is_mentioned": None, "verdict": "UNRESOLVED"}]
    view = build(hospital=_hospital(competitors=["가나의원", "다라의원"]), records=[v4_record("가상동 위내시경 가능한 병원", mentioned=False, competitors=mixed)])
    assert "경쟁 병원도 언급되지 않았습니다" not in view.cases.missing.insight
    both = [{"name": "가나의원", "is_mentioned": False}, {"name": "다라의원", "is_mentioned": False}]
    view = build(hospital=_hospital(competitors=["가나의원", "다라의원"]), records=[v4_record("가상동 위내시경 가능한 병원", mentioned=False, competitors=both)])
    assert "경쟁 병원도 언급되지 않았습니다" in view.cases.missing.insight


def test_ordinary_strategy_summary_uses_the_same_registration_projection():
    from datetime import datetime, timezone

    from app.services.report_engine import build_strategy_summary

    target = SimpleNamespace(id=uuid.uuid4(), name="가상동 위내시경", priority="NORMAL", platforms=["chatgpt"], status="ACTIVE", variants=[])
    records = [
        _record(target.id, None, mentions=[{"name": "가나 의원", "is_mentioned": True}, {"name": "옛경쟁의원", "is_mentioned": True}]),
        _record(target.id, None, mentions=[{"name": "가나의원", "is_mentioned": False}]),
    ]
    summary = build_strategy_summary(
        hospital=SimpleNamespace(id=uuid.uuid4(), competitors=["가나의원"]),
        query_targets=[target], sov_records=records, exposure_gaps=[], exposure_actions=[],
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc), period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        next_month="2026-11",
    )
    outcomes = summary["query_targets"][0]["competitor_outcomes"]
    assert outcomes == [{"name": "가나의원", "observed_count": 2, "mention_count": 1, "mention_pct": 50.0}]
    legacy = build_strategy_summary(
        hospital=SimpleNamespace(id=uuid.uuid4(), competitors=[]),
        query_targets=[target], sov_records=records, exposure_gaps=[], exposure_actions=[],
        period_start=datetime(2026, 10, 1, tzinfo=timezone.utc), period_end=datetime(2026, 11, 1, tzinfo=timezone.utc),
        next_month="2026-11",
    )
    assert len(legacy["query_targets"][0]["competitor_outcomes"]) == 3
