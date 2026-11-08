"""경쟁 병원 뒤늦은 등록 → 경쟁사 재판정 → 경쟁 현황만 다시 만든 새 버전(PostgreSQL).

첫 버전은 등록 경쟁사 없이 만들어진 v4 보고서다. 그 뒤 경쟁사를 등록하고 재판정을 돌리면 저장된 답변의
`competitor_mentions`에 새 이름의 판정만 더해지고, `COMPETITOR_REFRESH` 템플릿 갱신은 경쟁 사실만 바뀐 새 버전을
만든다 — 측정·귀속·인용·발행 숫자는 그대로여야 한다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import arrow
import pytest
from sqlalchemy import select
from test_monthly_template_refresh_postgres import (  # noqa: F401 — pg_session fixture
    ANCHOR,
    ORIGINAL_CUTOFF,
    _item,
    _pdf_bytes_text,
    pg_session,
)

from app.models.content import ContentSchedule
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import OperationRun, OperationRunState
from app.models.report import MonthlyReport
from app.models.sov import AIQueryTarget, QueryMatrix, SovRecord
from app.services import competitor_rejudge as cr
from app.services import cost_guard, sov_engine
from app.services.doctor_report_v4 import is_competitor_fact
from app.services.monthly_period import ReportBuildReason
from app.services.monthly_report_snapshot import report_render_inputs, restore_doctor_view
from app.services.monthly_template_refresh import TemplateRefreshRefused
from app.workers import competitor_refresh, tasks

HOSPITAL = "재판정 가상 의원"
RIVAL = "경쟁 가상 의원"
SECOND = "둘째 가상 클리닉"


@pytest.fixture
def quiet_renders(monkeypatch):
    """원장 PDF는 실제로 렌더·검증하고(판 이름이 진짜가 되게) 저장소 업로드·사고 기록·AE PDF만 생략한다."""
    from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
    from app.services.doctor_report_artifact import build_doctor_pdf_expectation
    from app.services.report_artifact_validation import PublishedDoctorPdf

    texts: dict = {}

    def real_doctor(_hospital, report_id, period_start, view, public_url):
        rendered = render_validated_doctor_pdf(
            view=view, period_label=arrow.get(period_start).format("YYYY-MM"), public_url=public_url,
            expectation=build_doctor_pdf_expectation(view, public_url),
        )
        texts[report_id] = _pdf_bytes_text(rendered.pdf_bytes)
        return PublishedDoctorPdf(
            path=f"gs://qa-private/monthly/{report_id}.pdf", sha256=rendered.sha256,
            byte_size=rendered.byte_size, metadata=rendered.metadata,
        )

    async def quiet(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tasks, "_public_site_url", lambda *_a: "https://fictional.example.invalid/")
    monkeypatch.setattr(tasks, "generate_doctor_pdf_report", real_doctor)
    monkeypatch.setattr(tasks, "generate_pdf_report", lambda **_k: "gs://qa-private/ae-competitor.pdf")
    monkeypatch.setattr(tasks, "record_monthly_artifact_failure", quiet)
    monkeypatch.setattr(tasks, "recover_monthly_artifact_failures", quiet)
    return texts


@pytest.fixture
def budget(monkeypatch):
    """비용 가드 예약·정산을 가로채 호출 인자를 모은다(Redis 없이)."""
    calls: dict = {"reserve": [], "settle": []}

    async def reserve(category, *, count, reservation_id=None, hospital_id=None, **_kwargs):
        calls["reserve"].append((category, count, reservation_id, hospital_id))
        if calls.get("block"):
            return cost_guard.CostGuardDecision(False, "blocked-for-test")
        receipt = cost_guard.ReservationReceipt(
            id=str(reservation_id), category=category, daily_period="d", monthly_period="m", reserved_units=count
        )
        return cost_guard.CostGuardDecision(True, None, receipt)

    async def settle(receipt, *, consumed_units, **_kwargs):
        calls["settle"].append((receipt, consumed_units))
        return receipt

    async def record(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cost_guard, "reserve", reserve)
    monkeypatch.setattr(cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(cost_guard, "record_provider_call", record)
    return calls


def _first_version(session):
    """등록 경쟁사 없이 만든 v4 첫 버전 — 실제 매니페스트·셀·답변 기록 위에서."""
    from app.services.monthly_manifest import (
        ManifestCellSpec,
        freeze_monthly_manifest,
        link_attempt,
    )

    hospital = Hospital(name=HOSPITAL, slug=f"rejudge-{uuid.uuid4().hex}", region=["가상동"], competitors=[])
    session.add(hospital)
    session.flush()
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[0], active_from=date(2026, 7, 1), is_active=True,
    )
    session.add(schedule)
    session.flush()
    session.add_all([
        _item(hospital, schedule, 1, scheduled=date(2026, 7, 6), published_at=datetime(2026, 7, 6, 1, tzinfo=timezone.utc)),
        _item(hospital, schedule, 2, scheduled=date(2026, 7, 13), published_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc)),
    ])
    target = AIQueryTarget(
        hospital_id=hospital.id, name="가상동 위내시경 잘하는 병원", target_intent="추천 탐색",
        platforms=["chatgpt", "gemini"], treatment="위내시경",
    )
    query = QueryMatrix(hospital_id=hospital.id, query_text="가상동 위내시경 잘하는 병원", query_intent="LOCAL")
    session.add_all((target, query))
    session.flush()
    manifest = freeze_monthly_manifest(
        session, hospital.id, 2026, 7,
        [
            ManifestCellSpec(
                query_key=f"query:{query.id}", query_text=query.query_text, platform=platform,
                query_matrix_id=query.id, query_target_id=target.id, query_variant_id=None, query_intent="LOCAL",
            )
            for platform in ("chatgpt", "gemini")
        ],
        gemini_configured=True,
    )
    records = [
        SovRecord(
            hospital_id=hospital.id, query_id=query.id, ai_query_target_id=target.id, ai_platform="chatgpt",
            is_mentioned=True, measurement_status="SUCCESS",
            raw_response=f"가상동에서는 {HOSPITAL}과 {RIVAL}의 위내시경 상담을 받아볼 수 있습니다.",
        ),
        SovRecord(
            hospital_id=hospital.id, query_id=query.id, ai_query_target_id=target.id, ai_platform="gemini",
            is_mentioned=False, measurement_status="SUCCESS",
            raw_response="가상동에는 여러 내과가 있어 가까운 곳에서 상담해 보세요.",
        ),
    ]
    session.add_all(records)
    session.flush()
    cells = {cell.platform: cell for cell in manifest.cells}
    session.add_all(link_attempt(cells[r.ai_platform], r) for r in records)
    session.commit()
    outcome = tasks._build_monthly_report_for_hospital(
        session, hospital, ANCHOR, observed_at=ORIGINAL_CUTOFF,
        build_reason=ReportBuildReason.SCHEDULED_CLOSE, correlation_key=f"test:first:{hospital.id}",
    )
    assert outcome == "created"
    first = session.execute(select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)).scalar_one()
    frozen = report_render_inputs(first.content_summary)
    assert frozen is not None and not any(is_competitor_fact(k) for k in frozen.doctor_metric_facts)
    return hospital, records, first


def _judge_by_text(names, raw):
    """측정 판정 대신 쓰는 결정적 판정 — 원문에 이름이 있으면 언급."""
    return [{"name": name, "is_mentioned": name in raw, "mention_rank": 1 if name in raw else None} for name in names]


async def _async_judge(names, raw):
    return _judge_by_text(names, raw)


def _register(session, hospital, names):
    hospital.competitors = list(names)
    session.commit()


def test_rejudge_merges_only_missing_names_and_is_idempotent(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL, SECOND])
    # 측정 당시 판정이 있는 이름은 덮지 않는다 — 첫 기록에 SECOND의 옛 판정을 심어 둔다.
    records[0].competitor_mentions = [{"name": SECOND, "is_mentioned": False, "mention_rank": None}]
    pg_session.commit()

    plan = cr.build_rejudge_plan(pg_session, hospital, 2026, 7)
    assert plan.records_total == 2 and plan.pending_records == 2
    assert {t.record_id: t.names for t in plan.targets} == {records[0].id: (RIVAL,), records[1].id: (RIVAL, SECOND)}

    asked: list[tuple[list[str], str]] = []

    async def judge(names, raw):
        asked.append((list(names), raw))
        if SECOND in names:
            raise ValueError("competitor_parse_failed")  # 둘째 기록은 세 번 다 실패 → 미확정
        return _judge_by_text(names, raw)

    run_id = uuid.uuid4()
    outcome = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=run_id, judge=judge)
    assert (outcome.records_total, outcome.pending_records) == (2, 2)
    assert (outcome.judged_records, outcome.unresolved_records) == (1, 1)
    assert (outcome.judged_names, outcome.unresolved_names) == (1, 2)
    assert outcome.cost_blocked is False
    assert len(asked) == 1 + cr.MAX_JUDGMENT_ATTEMPTS  # 첫 기록 1회, 둘째 기록은 장애로 3회
    assert len(budget["reserve"]) == 1
    category, count, reservation_id, reserved_hospital = budget["reserve"][0]
    assert (category, count, reserved_hospital) == ("sov", 2, hospital.id)
    assert reservation_id.startswith(f"competitor-rejudge:{run_id}:2026-07:")
    assert budget["settle"][0][1] == 0  # 가짜 판정기는 공급자 HTTP를 내지 않았다 — 실제 호출만 정산

    pg_session.expire_all()
    first_mentions = {m["name"]: m for m in pg_session.get(SovRecord, records[0].id).competitor_mentions}
    assert first_mentions[SECOND] == {"name": SECOND, "is_mentioned": False, "mention_rank": None}
    assert first_mentions[RIVAL]["is_mentioned"] is True and first_mentions[RIVAL]["verdict"] == "REJUDGED"
    second_mentions = {m["name"]: m for m in pg_session.get(SovRecord, records[1].id).competitor_mentions}
    assert second_mentions[RIVAL]["is_mentioned"] is None and second_mentions[RIVAL]["verdict"] == "UNRESOLVED"
    assert second_mentions[SECOND]["failure_reason"].startswith("ValueError")

    # 다시 돌리면 미확정으로 남은 이름만 묻고 확정된 판정은 그대로다(멱등).
    asked.clear()

    async def recording_judge(names, raw):
        asked.append((list(names), raw))
        return _judge_by_text(names, raw)

    again = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=recording_judge)
    assert asked == [([RIVAL, SECOND], records[1].raw_response)]
    assert (again.pending_records, again.judged_records, again.unresolved_records) == (1, 1, 0)
    pg_session.expire_all()
    assert pg_session.get(SovRecord, records[0].id).competitor_mentions == list(first_mentions.values())
    assert all(m["is_mentioned"] is False for m in pg_session.get(SovRecord, records[1].id).competitor_mentions)
    third = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    assert third.pending_records == 0 and len(budget["reserve"]) == 2


def test_rejudge_uses_the_measurement_resolver_without_provider_calls(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    """기본 판정기는 측정과 같은 `_parse_competitors`(규칙 대조)다 — 공급자를 부르지 않는다."""
    hospital, records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])

    class _Unexpected:
        async def create(self, **_kwargs):
            raise AssertionError("경쟁 병원 재판정은 공급자를 부르지 않는다")

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Unexpected())

    outcome = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4())

    assert (outcome.judged_records, outcome.unresolved_records, outcome.provider_calls) == (2, 0, 0)
    assert budget["settle"][0][1] == 0
    pg_session.expire_all()
    mentioned = pg_session.get(SovRecord, records[0].id).competitor_mentions[0]
    assert mentioned["is_mentioned"] is True and mentioned["mention_rank"] == 1
    assert pg_session.get(SovRecord, records[1].id).competitor_mentions[0]["is_mentioned"] is False


def test_cost_block_leaves_records_untouched(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    budget["block"] = True
    outcome = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    assert outcome.cost_blocked is True and outcome.unresolved_records == 2 and budget["settle"] == []
    pg_session.expire_all()
    assert all(pg_session.get(SovRecord, r.id).competitor_mentions is None for r in records)


def test_competitor_refresh_rebuilds_only_the_competition_facts(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, records, first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL, SECOND])
    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)

    plan = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert plan.verdict.passed, plan.verdict.summary()
    codes = {finding.code for finding in plan.verdict.findings}
    assert codes == {"COMPETITOR_FACTS_RECOMPUTED"}
    pg_session.rollback()

    outcome = tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:competitor:{hospital.id}", competitor_refresh=True
    )
    assert outcome == "created"
    second = pg_session.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id, MonthlyReport.version == 2)
    ).scalar_one()
    assert second.supersedes_report_id == first.id
    assert second.sov_summary == first.sov_summary
    assert second.essence_summary == first.essence_summary
    assert (second.planned_count, second.success_count, second.cutoff_at) == (first.planned_count, first.success_count, first.cutoff_at)
    old_facts = report_render_inputs(first.content_summary).doctor_metric_facts
    new_inputs = report_render_inputs(second.content_summary)
    new_facts = new_inputs.doctor_metric_facts
    assert {k: v for k, v in old_facts.items() if not is_competitor_fact(k)} == {k: v for k, v in new_facts.items() if not is_competitor_fact(k)}
    assert new_facts[f"competitor.{RIVAL}"] == "2건 중 1건" and new_facts[f"competitor.{RIVAL}.rate"] == "50%"
    assert new_facts[f"competitor.{SECOND}"] == "2건 중 0건"
    # 저장 전략 요약(AE PDF의 경쟁 열)의 측정 키워드별 경쟁 집계도 같은 기록으로 다시 셌다 — 본문 요약과 AE 스냅샷 둘 다.
    rows = second.content_summary["strategy"]["query_targets"]
    assert rows[0]["competitor_outcomes"][0] == {"name": RIVAL, "observed_count": 2, "mention_count": 1, "mention_pct": 50.0}
    assert new_inputs.ae_report.strategy["query_targets"][0]["competitor_outcomes"] == rows[0]["competitor_outcomes"]
    assert first.content_summary["strategy"]["query_targets"][0]["competitor_outcomes"] == []
    artifact = pg_session.execute(
        select(MonthlyReportArtifact).where(MonthlyReportArtifact.report_id == second.id)
    ).scalar_one()
    assert artifact.validated is True and artifact.validation_metadata["validation_version"] == "doctor-pdf-v4"
    assert RIVAL.replace(" ", "") in "".join(quiet_renders[second.id].split())
    # 그 뒤의 일반 템플릿 갱신(문구만)도 새 스냅샷(경쟁 사실 포함) 위에서 그대로 PASS다.
    third = tasks.build_monthly_template_refresh_plan(pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc))
    assert third.verdict.passed, third.verdict.summary()
    # 등록에서 뺀 이름은 기록에 남지만 다음 경쟁 병원 반영이 보고서에서 거른다(WARN으로 기록).
    _register(pg_session, hospital, [RIVAL])
    scoped = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert scoped.verdict.passed, scoped.verdict.summary()
    scoped_facts = scoped.doctor_view.facts()
    assert f"competitor.{RIVAL}" in scoped_facts and f"competitor.{SECOND}" not in scoped_facts
    assert any(f.code == "COMPETITOR_FACTS_RECOMPUTED" and SECOND in f.detail for f in scoped.verdict.findings)
    pg_session.rollback()


def test_competitor_refresh_refuses_when_a_non_competitor_number_moved(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, _records, first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    # 누적 편수는 현재 행에서 읽는다 — 그 달 이전에 공개된 글이 뒤늦게 생기면 경쟁 현황만 바꾸는 갱신이 아니다.
    schedule = pg_session.execute(select(ContentSchedule).where(ContentSchedule.hospital_id == hospital.id)).scalar_one()
    pg_session.add(_item(hospital, schedule, 9, scheduled=date(2026, 6, 1), published_at=datetime(2026, 6, 1, 1, tzinfo=timezone.utc)))
    pg_session.commit()

    plan = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert plan.verdict.status == "DIFF"
    assert any(f.code == "COMPETITOR_REFRESH_FACTS" and "card.cumulative" in f.detail for f in plan.verdict.findings)
    pg_session.rollback()
    with pytest.raises(TemplateRefreshRefused):
        tasks._build_monthly_template_refresh(
            pg_session, hospital, ANCHOR, correlation_key=f"test:refuse:{hospital.id}", competitor_refresh=True
        )
    pg_session.rollback()
    versions = pg_session.execute(select(MonthlyReport.version).where(MonthlyReport.hospital_id == hospital.id)).scalars().all()
    assert versions == [first.version]


def test_cli_precheck_and_postcheck_pass_around_a_competitor_refresh(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    from app.utils import monthly_competitor_refresh as cli

    hospital, _records, first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    monkeypatch.setattr(
        cli, "_stored_doctor_text", lambda _db, report, _verdict: quiet_renders.get(report.id, "옛 PDF 본문")
    )
    before = cli.precheck_hospital(pg_session, hospital, ANCHOR)
    assert before.verdict.status == "PASS", before.verdict.summary()
    assert (before.registered, before.records_total, before.pending_records) == (1, 2, 2)
    pg_session.rollback()

    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:cli:{hospital.id}", competitor_refresh=True
    )
    after = cli.postcheck_hospital(pg_session, hospital, 2026, 7)
    assert after.verdict.status == "PASS", after.verdict.summary()
    assert any(f.code == "COMPETITOR_FACTS_RECOMPUTED" for f in after.verdict.findings)
    assert after.version == first.version + 1


def test_unregistered_hospital_is_blocked_in_precheck(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    from app.utils import monthly_competitor_refresh as cli

    hospital, _records, _first = _first_version(pg_session)
    result = cli.precheck_hospital(pg_session, hospital, ANCHOR)
    assert result.verdict.status == "BLOCKED"
    assert any(f.code == "NO_REGISTERED_COMPETITORS" for f in result.verdict.findings)


def test_rejudge_task_finishes_its_run_and_chains_the_report_refresh(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    hospital, _records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.QUEUED, idempotency_key=f"test:{uuid.uuid4()}", task_id=str(uuid.uuid4()),
        request_payload={}, result_summary={},
    )
    pg_session.add(parent)
    pg_session.commit()

    class _Session:
        def __enter__(self):
            return pg_session

        def __exit__(self, *_exc):
            return False

    dispatched: list[dict] = []
    monkeypatch.setattr(competitor_refresh, "SyncSessionPinnedConnection", _Session)
    monkeypatch.setattr(
        competitor_refresh, "execute_rejudge",
        lambda db, h, y, m, *, run_id, **_k: cr.execute_rejudge(db, h, y, m, run_id=run_id, judge=_async_judge),
    )
    monkeypatch.setattr(
        tasks.generate_monthly_report_for_hospital, "apply_async", lambda **kwargs: dispatched.append(kwargs)
    )
    task = competitor_refresh.rejudge_month_competitors
    task.push_request(headers={"operation_run_id": str(parent.id)}, retries=0, id="t")
    try:
        result = task.run(str(hospital.id), 2026, 7)
    finally:
        task.pop_request()

    assert result["status"] == "report_refresh_queued" and result["judged_records"] == 2
    pg_session.refresh(parent)
    assert parent.state == OperationRunState.SUCCEEDED
    assert parent.result_summary["stage"] == "REPORT_REFRESH_QUEUED"
    assert parent.result_summary["rejudge"]["judged_names"] == 2
    assert parent.result_summary["report_run_id"] == result["report_run_id"]
    assert parent.completed_at is not None and parent.lease_owner is None
    child = pg_session.get(OperationRun, uuid.UUID(result["report_run_id"]))
    assert child.parent_run_id == parent.id and child.operation_type == "GENERATE_MONTHLY_REPORT"
    assert child.state == OperationRunState.QUEUED
    assert child.request_payload["competitor_refresh"]["rejudge_run_id"] == str(parent.id)
    # 부모 종결과 자식 행은 같은 커밋이다 — 부모가 SUCCEEDED인데 자식이 없는 창이 없다.
    assert child.requested_at is not None and parent.completed_at is not None
    assert dispatched[0]["args"] == [str(hospital.id), 2026, 7, True, False, True, False, True]
    assert dispatched[0]["queue"] == "reports" and dispatched[0]["headers"]["operation_run_id"] == str(child.id)
    # 같은 부모로 다시 불러도 자식은 하나다.
    again = competitor_refresh.dispatch_competitor_report_refresh(
        pg_session, hospital, 2026, 7, parent_run_id=parent.id, rejudge_summary={}
    )
    assert again.id == child.id and len(dispatched) == 1


def test_child_refresh_ignores_its_own_parent_even_if_still_marked_running(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, _records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.RUNNING, idempotency_key=f"test:{uuid.uuid4()}", task_id="t", request_payload={},
    )
    child = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type="GENERATE_MONTHLY_REPORT", parent_run_id=parent.id,
        state=OperationRunState.RUNNING, idempotency_key=f"test:{uuid.uuid4()}", task_id="c", request_payload={},
    )
    pg_session.add_all([parent, child])
    pg_session.commit()
    blocked = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert any(f.code == "OPERATION_IN_FLIGHT" for f in blocked.verdict.findings)
    pg_session.rollback()
    own = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc),
        exclude_run_id=child.id, competitor_refresh=True,
    )
    assert own.verdict.passed, own.verdict.summary()
    pg_session.rollback()
    # 같은 병원의 **다른 달** 재판정(락이 바빠 미뤄진 실행 등)은 이 달의 갱신을 막지 않는다 — 그 달의 경쟁 현황만 바꾼다.
    parent.state = OperationRunState.SUCCEEDED
    child.state = OperationRunState.SUCCEEDED
    other_month = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.QUEUED, idempotency_key=f"test:{uuid.uuid4()}", task_id="o", request_payload={},
        result_summary={"stage": "DEFERRED_LOCK_BUSY", "period_year": 2026, "period_month": 6},
    )
    pg_session.add(other_month)
    pg_session.commit()
    unaffected = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert unaffected.verdict.passed, unaffected.verdict.summary()
    pg_session.rollback()
    # 다른 달 체인의 **자식 보고서 실행**이 큐에 있어도 마찬가지다 — 두 달의 자식이 서로를 FAILED로 만들지 않는다.
    other_child = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type="GENERATE_MONTHLY_REPORT", parent_run_id=other_month.id,
        state=OperationRunState.QUEUED, idempotency_key=f"test:{uuid.uuid4()}", task_id="oc",
        request_payload={"_dispatch": {"target_type": "hospital", "target_id": str(hospital.id), "queue": "reports",
                                       "task_args": [str(hospital.id), 2026, 6, True, False, True, False, True]}},
        result_summary={},
    )
    pg_session.add(other_child)
    pg_session.commit()
    unaffected = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert unaffected.verdict.passed, unaffected.verdict.summary()
    pg_session.rollback()
    other_month.result_summary = {"period_year": 2026, "period_month": 7}
    pg_session.commit()
    same_month = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert any(f.code == "OPERATION_IN_FLIGHT" for f in same_month.verdict.findings)
    pg_session.rollback()


def test_report_without_snapshot_evidence_has_nothing_to_rejudge(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    hospital, _records, first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    content = dict(first.content_summary)
    content.pop("report_snapshot", None)
    first.content_summary = content
    pg_session.commit()
    plan = cr.build_rejudge_plan(pg_session, hospital, 2026, 7)
    assert (plan.records_total, plan.pending_records) == (0, 0)
    outcome = cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    assert outcome.records_total == 0 and budget["reserve"] == []


def test_rejudge_delivery_steps_aside_when_another_copy_holds_the_hospital_lock(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    from celery.exceptions import Ignore

    hospital, _records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.RUNNING, idempotency_key=f"test:{uuid.uuid4()}", task_id="t", request_payload={},
    )
    pg_session.add(parent)
    pg_session.commit()

    class _Session:
        def __enter__(self):
            return pg_session

        def __exit__(self, *_exc):
            return False

    released: list = []
    monkeypatch.setattr(competitor_refresh, "SyncSessionPinnedConnection", _Session)
    monkeypatch.setattr(competitor_refresh, "try_acquire_hospital_advisory_session_lock_sync", lambda *_a, **_k: False)
    monkeypatch.setattr(competitor_refresh, "_release_session_lock", lambda *a: released.append(a))
    monkeypatch.setattr(competitor_refresh, "execute_rejudge", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("판정을 사면 안 된다")))
    task = competitor_refresh.rejudge_month_competitors
    task.push_request(headers={"operation_run_id": str(parent.id)}, retries=0, id="t")
    try:
        with pytest.raises(Ignore):
            task.run(str(hospital.id), 2026, 7)
    finally:
        task.pop_request()
    pg_session.refresh(parent)
    assert parent.state == OperationRunState.RUNNING  # 주인 사본의 실행 기록은 그대로다
    assert released == [] and budget["reserve"] == []


def test_rejudge_delivery_that_finds_the_run_already_terminal_buys_nothing(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    hospital, _records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.SUCCEEDED, idempotency_key=f"test:{uuid.uuid4()}", task_id="t", request_payload={},
        result_summary={"stage": "REPORT_REFRESH_QUEUED", "report_run_id": "child-1"},
    )
    pg_session.add(parent)
    pg_session.commit()

    class _Session:
        def __enter__(self):
            return pg_session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(competitor_refresh, "SyncSessionPinnedConnection", _Session)
    monkeypatch.setattr(competitor_refresh, "execute_rejudge", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("판정을 사면 안 된다")))
    task = competitor_refresh.rejudge_month_competitors
    task.push_request(headers={"operation_run_id": str(parent.id)}, retries=0, id="t")
    try:
        result = task.run(str(hospital.id), 2026, 7)
    finally:
        task.pop_request()
    assert result == {"status": "already_finished", "report_run_id": "child-1"}
    pg_session.refresh(parent)
    assert parent.result_summary["stage"] == "REPORT_REFRESH_QUEUED" and budget["reserve"] == []


def test_publish_failure_after_commit_leaves_a_recoverable_child_and_returns_its_id(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    hospital, _records, _first = _first_version(pg_session)
    _register(pg_session, hospital, [RIVAL])
    parent = OperationRun(
        id=uuid.uuid4(), hospital_id=hospital.id, operation_type=cr.REJUDGE_OPERATION_TYPE,
        state=OperationRunState.QUEUED, idempotency_key=f"test:{uuid.uuid4()}", task_id="t", request_payload={}, result_summary={},
    )
    pg_session.add(parent)
    pg_session.commit()

    class _Session:
        def __enter__(self):
            return pg_session

        def __exit__(self, *_exc):
            pg_session.expire_all()
            return False

    def broken_publish(**_kwargs):
        raise OSError("broker down")

    monkeypatch.setattr(competitor_refresh, "SyncSessionPinnedConnection", _Session)
    monkeypatch.setattr(competitor_refresh, "execute_rejudge", lambda db, h, y, m, *, run_id, **_k: cr.execute_rejudge(db, h, y, m, run_id=run_id, judge=_async_judge))
    monkeypatch.setattr(tasks.generate_monthly_report_for_hospital, "apply_async", broken_publish)
    task = competitor_refresh.rejudge_month_competitors
    task.push_request(headers={"operation_run_id": str(parent.id)}, retries=0, id="t")
    try:
        result = task.run(str(hospital.id), 2026, 7)
    finally:
        task.pop_request()
    assert result["status"] == "report_refresh_queued"
    child = pg_session.get(OperationRun, uuid.UUID(result["report_run_id"]))
    assert child.state == OperationRunState.REQUESTED and child.parent_run_id == parent.id  # 자율 복구가 재배달한다
    pg_session.refresh(parent)
    assert parent.state == OperationRunState.SUCCEEDED


def test_redelivered_copy_does_not_reclaim_a_live_rejudge_lease(pg_session, quiet_renders, budget, monkeypatch):  # noqa: F811 - pytest fixture
    """같은 task id의 재배달 사본은 살아 있는 재판정 lease를 다시 claim하지 않는다 — 주인 사본의 재시도 판 울타리를 지킨다."""
    from app.workers import operation_run_signals

    hospital, _records, _first = _first_version(pg_session)
    now = datetime.now(timezone.utc)

    class _Session:
        def __enter__(self):
            return pg_session

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(operation_run_signals, "SyncSessionLocal", _Session)
    runs = {}
    for operation_type in (cr.REJUDGE_OPERATION_TYPE, "GENERATE_MONTHLY_REPORT"):
        run = OperationRun(
            id=uuid.uuid4(), hospital_id=hospital.id, operation_type=operation_type, state=OperationRunState.RUNNING,
            idempotency_key=f"test:{uuid.uuid4()}", task_id=f"task-{operation_type}", lease_owner=f"task-{operation_type}",
            lease_expires_at=now + timedelta(hours=1), request_payload={}, version=2,
        )
        pg_session.add(run)
        runs[operation_type] = run
    pg_session.commit()
    rejudge = runs[cr.REJUDGE_OPERATION_TYPE]
    assert operation_run_signals._claim(rejudge.id, rejudge.task_id, now, redelivered=True) is None
    pg_session.refresh(rejudge)
    assert rejudge.version == 2 and rejudge.state == OperationRunState.RUNNING
    other = runs["GENERATE_MONTHLY_REPORT"]
    assert operation_run_signals._claim(other.id, other.task_id, now, redelivered=True) == 3
    # 만료된 lease는 종전처럼 인수한다.
    rejudge.lease_expires_at = now - timedelta(minutes=1)
    pg_session.commit()
    assert operation_run_signals._claim(rejudge.id, rejudge.task_id, now, redelivered=False) == 3


def test_competitor_refresh_and_ae_edit_carry_each_other_through_the_version_chain(pg_session, quiet_renders, budget):  # noqa: F811 - pytest fixture
    """경쟁 반영 → AE 수정 → 경쟁 반영 순서의 버전 체인: 숫자는 그대로, 수정 값은 규칙대로 이어지고, 경쟁 사실은 최신 판정이다."""
    from app.services.doctor_report_ae_edit import base_view_of, stored_ae_overrides

    hospital, _records, first = _first_version(pg_session)
    keyword = "가상동 위내시경 잘하는 병원"

    # v2: 경쟁 병원 반영(경쟁사 1곳)
    _register(pg_session, hospital, [RIVAL])
    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    assert tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:chain-c1:{hospital.id}", competitor_refresh=True
    ) == "created"
    v2 = cr.latest_monthly_report(pg_session, hospital.id, 2026, 7)
    v2_facts = report_render_inputs(v2.content_summary).doctor_metric_facts
    assert f"competitor.{RIVAL}" in v2_facts

    # v3: AE 수정 판 — 경쟁과 무관한 핵심 줄·라벨, 경쟁 병원을 말하는 P5 시사점
    fields = {
        "summary.key_lines[0]": "이번 달 위내시경 문의가 꾸준했습니다",
        f"treatment_label[{keyword}]": "위 내시경",
        "cases.missing.insight": f"{RIVAL}보다 먼저 노출되도록 콘텐츠 1편 발행",
    }
    assert tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:chain-ae:{hospital.id}",
        ae_edit={"report_id": str(v2.id), "fields": fields, "edited_by": "ae@example.invalid", "requested_at": "2026-11-05T01:00:00+00:00"},
    ) == "created"
    v3 = cr.latest_monthly_report(pg_session, hospital.id, 2026, 7)
    v3_inputs = report_render_inputs(v3.content_summary)
    v3_view = restore_doctor_view(v3_inputs.doctor_view)
    assert v3.supersedes_report_id == v2.id and v3_view.summary.key_lines[0] == fields["summary.key_lines[0]"]
    assert [row.name for row in v3_view.competition.rows] == [RIVAL]  # AE 수정이 경쟁 결과를 되돌리지 않는다
    assert [row.name for row in base_view_of(v3_inputs).competition.rows] == [RIVAL]
    assert {k: v for k, v in v3_inputs.doctor_metric_facts.items() if k.startswith("competitor.")} == {
        k: v for k, v in v2_facts.items() if k.startswith("competitor.")
    }

    # v4: 경쟁사 하나 더 등록 → 경쟁 병원 반영. 라벨·경쟁 무관 문장은 잇고, 경쟁 병원을 말하는 문장은 버린다.
    _register(pg_session, hospital, [RIVAL, SECOND])
    cr.execute_rejudge(pg_session, hospital, 2026, 7, run_id=uuid.uuid4(), judge=_async_judge)
    plan = tasks.build_monthly_template_refresh_plan(
        pg_session, hospital, ANCHOR, observed_now=datetime.now(timezone.utc), competitor_refresh=True
    )
    assert plan.verdict.passed, plan.verdict.summary()
    codes = {f.code for f in plan.verdict.findings}
    assert codes == {"COMPETITOR_FACTS_RECOMPUTED", "AE_OVERRIDES_DROPPED"}
    pg_session.rollback()
    assert tasks._build_monthly_template_refresh(
        pg_session, hospital, ANCHOR, correlation_key=f"test:chain-c2:{hospital.id}", competitor_refresh=True
    ) == "created"
    v4 = cr.latest_monthly_report(pg_session, hospital.id, 2026, 7)
    v4_inputs = report_render_inputs(v4.content_summary)
    v4_view = restore_doctor_view(v4_inputs.doctor_view)
    assert v4.supersedes_report_id == v3.id
    assert v4.sov_summary == first.sov_summary
    assert {k: v for k, v in v3_inputs.doctor_metric_facts.items() if not k.startswith("competitor.")} == {
        k: v for k, v in v4_inputs.doctor_metric_facts.items() if not k.startswith("competitor.")
    }
    assert {row.name for row in v4_view.competition.rows} == {RIVAL, SECOND}
    assert v4_view.summary.key_lines[0] == fields["summary.key_lines[0]"]
    assert all(row.treatment == "위 내시경" for row in v4_view.appendix.keyword_rows if row.keyword == keyword)
    carried = stored_ae_overrides(v4_inputs)
    assert set(carried["fields"]) == {"summary.key_lines[0]", f"treatment_label[{keyword}]"}
    assert {row.name for row in base_view_of(v4_inputs).competition.rows} == {RIVAL, SECOND}
    dropped = v4_inputs.model_dump().get("ae_overrides_dropped") if hasattr(v4_inputs, "model_dump") else None
    dropped = dropped or (v4.content_summary["report_snapshot"]["render_inputs"].get("ae_overrides_dropped"))
    assert dropped["reason"] == "COMPETITION_CHANGED" and set(dropped["fields"]) == {"cases.missing.insight"}
    assert v4_view.cases.missing is not None and f"{RIVAL}보다" not in v4_view.cases.missing.insight
