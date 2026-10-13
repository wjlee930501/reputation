"""레거시 예산 1회 교체(2026-10-13 대표 승인 D22)와 "모르는 지출을 0으로 바꾸지 않기".

운영(2026-10-13): 10/9 이전에 만든 빈 슬롯 23개가 `LEGACY_SPEND_UNKNOWN`으로 멈췄다. 실패 기록
(`_remember_generation_attempt`)이 계수 0을 써서 예산이 KNOWN_COUNTERS(0)로 읽혔고, 그래서
`replace_unknown_legacy_budget`는 교체를 거절했으며(LEGACY_BUDGET_IS_KNOWN), `OPERATOR_REQUIRED`
억제 때문에 스윕도 Admin 버튼도 작가를 부르지 않았다. 이 파일은 교체 함수·CLI·실패 기록·운영자
재생성을 고정한다.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from test_operator_retry_gate_record import _SAME_DAY, _press_retry
from test_topic_swap_fallback import (
    _approved_philosophy,
    _freeze,
    _kst,
    _patch_generation,
    _swapped_slot,
    _WorkerDB,
)

from app.models.operations import OperationRunState
from app.utils import legacy_budget_reset as cli
from app.workers import tasks
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    attempt_spend_stays_unknown,
    fresh_generation_attempt,
    legacy_budget_is_replaceable,
    read_generation_budget,
    replace_legacy_budget_and_release,
    replace_unknown_legacy_budget,
)
from app.workers.generation_retry_policy import GenerationRetryClass

_CONTEXT = "gate_catalog=2026-10-02.1;philosophy=p;content_type=HEALTH;query_target=q"


def _prod_record(reason: str) -> dict:
    """운영 23행의 저장 모양 — 실패 기록이 남긴 계수 0, 예산 표시 없음."""

    return {
        "reason": reason,
        "context": _CONTEXT,
        "observed_at": "2026-10-11T22:45:01.573847+00:00",
        "retry_class": "OPERATOR_REQUIRED",
        "attempt_count": 0,
        "approved_facts": "5f3de0ef90c10936",
        "attempt_period": "2026-10-12",
        "exhausted_days": 0,
        "first_observed_at": "2026-10-11T22:45:01.573847+00:00",
        "guard_deferral_count": 0,
        "provider_attempt_count": 0,
    }


# ── 교체 대상 판정 ──────────────────────────────────────────────────────────


def test_production_shape_reads_known_and_the_old_function_refuses_it() -> None:
    record = _prod_record("LEGACY_SPEND_UNKNOWN")
    assert read_generation_budget(record).legacy_state == "KNOWN_COUNTERS"
    with pytest.raises(GenerationBudgetExceeded, match="LEGACY_BUDGET_IS_KNOWN"):
        replace_unknown_legacy_budget(record, actor="a", reason="r", idempotency_key="k")


@pytest.mark.parametrize(
    ("attempt", "incident_open", "expected"),
    [
        pytest.param({}, False, True, id="pristine_unknown"),
        pytest.param({"reason": "GENERATION_FAILED"}, False, True, id="unknown_record"),
        pytest.param(_prod_record("LEGACY_SPEND_UNKNOWN"), False, True, id="legacy_zero_record"),
        pytest.param(_prod_record("CONTENT_NOT_GENERATED"), True, True, id="gate_overwrote"),
        pytest.param(_prod_record("CONTENT_NOT_GENERATED"), False, False, id="no_legacy_evidence"),
        pytest.param(
            {
                **_prod_record("LEGACY_SPEND_UNKNOWN"),
                "provider_attempt_count": 2,
                "attempt_count": 2,
            },
            True,
            False,
            id="known_nonzero_spend",
        ),
        pytest.param(fresh_generation_attempt(), True, False, id="fresh_v2_budget"),
    ],
)
def test_replaceable_cases(attempt, incident_open, expected) -> None:
    assert legacy_budget_is_replaceable(attempt, legacy_incident_open=incident_open) is expected


@pytest.mark.parametrize(
    ("reason", "incident_open"),
    [("LEGACY_SPEND_UNKNOWN", False), ("CONTENT_NOT_GENERATED", True)],
)
def test_replacement_mints_one_audited_budget_and_releases_the_suppression(
    reason, incident_open
) -> None:
    previous = _prod_record(reason)
    summary = {GENERATION_ATTEMPT_KEY: previous, "blocking": False}
    at = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)

    replaced, record = replace_legacy_budget_and_release(
        summary,
        actor="system:legacy-budget-reset-20261013",
        reason="D22",
        idempotency_key="key-1",
        legacy_incident_open=incident_open,
        replaced_at=at,
    )

    assert replaced["blocking"] is False
    attempt = replaced[GENERATION_ATTEMPT_KEY]
    assert {"reason", "retry_class", "next_retry_at", "observed_at"}.isdisjoint(attempt)
    assert attempt["context"] == _CONTEXT
    snapshot = read_generation_budget(attempt)
    assert snapshot.legacy_state == "REPLACED"
    assert snapshot.writer_results == 0
    assert record == attempt["budget"]["reset_record"]
    assert record["event"] == "LEGACY_BUDGET_REPLACED"
    assert record["actor"] == "system:legacy-budget-reset-20261013"
    assert record["reason"] == "D22"
    assert record["idempotency_key"] == "key-1"
    assert record["replaced_at"] == at.isoformat()
    assert record["previous_snapshot"] == previous
    # 입력은 바꾸지 않는다.
    assert summary[GENERATION_ATTEMPT_KEY] == previous

    assert legacy_budget_is_replaceable(attempt, legacy_incident_open=True) is False
    with pytest.raises(GenerationBudgetExceeded, match="LEGACY_BUDGET_ALREADY_REPLACED"):
        replace_legacy_budget_and_release(
            replaced, actor="a", reason="r", idempotency_key="k2", legacy_incident_open=True
        )


def test_replacement_refuses_known_spend() -> None:
    summary = {GENERATION_ATTEMPT_KEY: _prod_record("CONTENT_NOT_GENERATED")}
    with pytest.raises(GenerationBudgetExceeded, match="LEGACY_BUDGET_IS_KNOWN"):
        replace_legacy_budget_and_release(summary, actor="a", reason="r", idempotency_key="k")


# ── 실패 기록이 모르는 지출을 0으로 바꾸지 않는다 ────────────────────────────


@pytest.mark.parametrize(
    ("previous", "reason", "expected"),
    [
        pytest.param({}, "LEGACY_SPEND_UNKNOWN", True, id="legacy_on_pristine"),
        pytest.param(
            {"reason": "LEGACY_SPEND_UNKNOWN"},
            "CONTENT_NOT_GENERATED",
            True,
            id="gate_over_unknown",
        ),
        pytest.param({}, "COST_BLOCKED", False, id="pristine_other_reason_unchanged"),
        pytest.param(
            _prod_record("LEGACY_SPEND_UNKNOWN"),
            "LEGACY_SPEND_UNKNOWN",
            False,
            id="already_known_counters",
        ),
        pytest.param(fresh_generation_attempt(), "LEGACY_SPEND_UNKNOWN", False, id="v2_budget"),
    ],
)
def test_attempt_spend_stays_unknown(previous, reason, expected) -> None:
    assert attempt_spend_stays_unknown(previous, reason) is expected


def _empty_legacy_slot(monkeypatch):
    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    item.topic_swap_history = []
    item.essence_check_summary = None
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda _db, _hid: philosophy)
    return philosophy, item


def test_legacy_failure_record_keeps_the_spend_unknown(monkeypatch) -> None:
    philosophy, item = _empty_legacy_slot(monkeypatch)
    _freeze(monkeypatch, _kst(2026, 10, 12, 1, 0))
    tasks._remember_generation_attempt(
        _WorkerDB(),
        item,
        philosophy,
        "LEGACY_SPEND_UNKNOWN",
        count_attempt=False,
        extra={"retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value, "next_retry_at": None},
    )
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record["reason"] == "LEGACY_SPEND_UNKNOWN"
    assert "provider_attempt_count" not in record and "attempt_count" not in record
    with pytest.raises(GenerationBudgetUnknown):
        read_generation_budget(record)

    # 07:45 게이트가 그 기록을 CONTENT_NOT_GENERATED로 덮어도 여전히 모른다.
    _freeze(monkeypatch, _kst(2026, 10, 12, 7, 45))
    tasks._record_gate_blocker_decision(_WorkerDB(), item, philosophy, "CONTENT_NOT_GENERATED")
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    with pytest.raises(GenerationBudgetUnknown):
        read_generation_budget(record)


def test_other_reasons_on_a_pristine_slot_keep_writing_counters(monkeypatch) -> None:
    philosophy, item = _empty_legacy_slot(monkeypatch)
    _freeze(monkeypatch, _kst(2026, 10, 12, 1, 0))
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "COST_BLOCKED")
    record = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert record["provider_attempt_count"] == 0


# ── 스윕 로더와 운영자 재생성 ───────────────────────────────────────────────


def _stuck_slot(monkeypatch, reason: str):
    philosophy = _approved_philosophy()
    item = _swapped_slot(philosophy)
    item.topic_swap_history = []
    writer_calls = _patch_generation(monkeypatch, philosophy, item, fail=False)
    context = tasks._generation_attempt_context(item, philosophy)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {**_prod_record(reason), "context": context}
    }
    return item, writer_calls


@pytest.mark.parametrize("reason", ["LEGACY_SPEND_UNKNOWN", "CONTENT_NOT_GENERATED"])
def test_the_sweep_loader_takes_the_slot_only_after_replacement(monkeypatch, reason) -> None:
    item, _writer_calls = _stuck_slot(monkeypatch, reason)
    _freeze(monkeypatch, _SAME_DAY)
    assert tasks._generation_retry_is_eligible(_WorkerDB())(item) is False

    item.essence_check_summary, _record = replace_legacy_budget_and_release(
        item.essence_check_summary,
        actor="system:test",
        reason="D22",
        idempotency_key="k",
        legacy_incident_open=True,
    )
    assert tasks._generation_retry_is_eligible(_WorkerDB())(item) is True


def test_operator_regenerate_writes_a_replaced_legacy_slot(monkeypatch) -> None:
    item, writer_calls = _stuck_slot(monkeypatch, "LEGACY_SPEND_UNKNOWN")
    item.essence_check_summary, _record = replace_legacy_budget_and_release(
        item.essence_check_summary, actor="owner", reason="D22", idempotency_key="k"
    )

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]


def test_operator_regenerate_releases_a_slot_replaced_by_the_previous_endpoint(monkeypatch) -> None:
    """이전 판 교체(`replace_unknown_legacy_budget`)는 원인·억제를 남겼다 — 워커가 푼다."""

    item, writer_calls = _stuck_slot(monkeypatch, "LEGACY_SPEND_UNKNOWN")
    stored = dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])
    for key in ("attempt_count", "provider_attempt_count"):
        stored.pop(key)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: replace_unknown_legacy_budget(
            stored, actor="owner", reason="r", idempotency_key="k"
        )
    }
    assert item.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == "LEGACY_SPEND_UNKNOWN"

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]


def test_operator_regenerate_keeps_an_unreplaced_legacy_slot_suppressed(monkeypatch) -> None:
    item, writer_calls = _stuck_slot(monkeypatch, "LEGACY_SPEND_UNKNOWN")

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, "LEGACY_SPEND_UNKNOWN")]


# ── CLI ───────────────────────────────────────────────────────────────────


class _CliDB:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0
        self.rollbacks = 0

    def execute(self, _stmt):
        return SimpleNamespace(all=lambda: list(self.rows))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _row(attempt: dict, *, incident_open: bool):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        scheduled_date=date(2026, 10, 14),
        status=SimpleNamespace(value="DRAFT"),
        created_at=datetime(2026, 9, 24, 15, 0, tzinfo=UTC),
        essence_check_summary={GENERATION_ATTEMPT_KEY: attempt},
    )
    return item, incident_open


def test_cli_predicate_sql_keeps_every_guard() -> None:
    sql = str(
        cli.candidate_query().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    for fragment in (
        "content_items.status IN ('DRAFT', 'READY')",
        "content_items.first_published_at IS NULL",
        "content_items.created_at < '2026-10-09 16:00:00+00:00'",
        "content_items.title IS NULL",
        "content_items.body IS NULL",
        "'LEGACY_SPEND_UNKNOWN'",
        "incidents.state IN ('OPEN', 'RETRYING', 'ACKNOWLEDGED')",
    ):
        assert fragment in sql, fragment


def test_cli_dry_run_lists_only_replaceable_rows_and_writes_nothing() -> None:
    legacy = _row(_prod_record("LEGACY_SPEND_UNKNOWN"), incident_open=True)
    gate = _row(_prod_record("CONTENT_NOT_GENERATED"), incident_open=True)
    replaced_summary, _ = replace_legacy_budget_and_release(
        {GENERATION_ATTEMPT_KEY: _prod_record("LEGACY_SPEND_UNKNOWN")},
        actor="a",
        reason="r",
        idempotency_key="k",
    )
    already = _row(replaced_summary[GENERATION_ATTEMPT_KEY], incident_open=True)
    db = _CliDB([legacy, gate, already])
    out = io.StringIO()

    assert cli.run(db, apply=False, reason=None, out=out) == 0

    lines = out.getvalue().splitlines()
    ids = [json.loads(line.split(" ", 1)[1])["content_id"] for line in lines[:-1]]
    assert ids == [str(legacy[0].id), str(gate[0].id)]
    assert json.loads(lines[-1]) == {"count": 2, "apply": False}
    assert db.commits == 0


def test_cli_apply_uses_the_shared_function_per_row_and_is_idempotent(monkeypatch) -> None:
    rows = [
        _row(_prod_record("LEGACY_SPEND_UNKNOWN"), incident_open=False),
        _row(_prod_record("CONTENT_NOT_GENERATED"), incident_open=True),
    ]
    by_id = {item.id: (item, incident) for item, incident in rows}
    calls = []

    def apply_one(db, content_id, *, actor, reason, idempotency_key):
        item, incident = by_id[content_id]
        calls.append((content_id, actor, reason, idempotency_key))
        item.essence_check_summary, _ = replace_legacy_budget_and_release(
            item.essence_check_summary,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
            legacy_incident_open=incident,
        )
        return SimpleNamespace(content_id=str(content_id), outcome="REPLACED", detail=None)

    monkeypatch.setattr(cli, "apply_legacy_budget_replacement_sync", apply_one)
    db = _CliDB(rows)
    out = io.StringIO()

    cli.run(db, apply=True, reason="D22", out=out)

    assert [call[0] for call in calls] == [item.id for item, _ in rows]
    assert {call[1] for call in calls} == {cli.ACTOR}
    assert calls[0][3] == f"legacy-budget-reset-20261013:{rows[0][0].id}"
    assert db.commits == 2
    assert json.loads(out.getvalue().splitlines()[-1]) == {"replaced": 2, "attempted": 2}

    second = io.StringIO()
    cli.run(_CliDB(rows), apply=True, reason="D22", out=second)
    assert json.loads(second.getvalue().splitlines()[0]) == {"count": 0, "apply": True}
    assert len(calls) == 2


def test_cli_apply_requires_a_reason() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--apply"])
    assert cli.parse_args([]).apply is False


# ── Admin "레거시 예산 교체 후 다시 시도" ──────────────────────────────────────


def _admin_db(content, *, incident_open: bool):
    from test_admin_operations import FakeDB

    class _DB(FakeDB):
        async def scalar(self, stmt):
            if "incidents" in str(stmt):
                return incident_open
            return await super().scalar(stmt)

    from test_admin_operations import _hospital

    hospital = _hospital()
    content.hospital_id = hospital.id
    return hospital, _DB(hospital=hospital, content=content)


@pytest.mark.parametrize(
    ("reason", "incident_open"),
    [("LEGACY_SPEND_UNKNOWN", False), ("CONTENT_NOT_GENERATED", True)],
)
async def test_admin_button_replaces_the_production_shape_and_queues(
    monkeypatch, reason, incident_open
) -> None:
    from test_admin_operations import FakeTask, _content

    from app.api.admin import operations as operations_api
    from app.models.audit import AdminAuditLog
    from app.schemas.operations import LegacyBudgetReplacementRequest

    content = _content(
        uuid.uuid4(), essence_check_summary={GENERATION_ATTEMPT_KEY: _prod_record(reason)}
    )
    hospital, db = _admin_db(content, incident_open=incident_open)
    task = FakeTask()
    monkeypatch.setattr(operations_api.regenerate_content_item, "apply_async", task.apply_async)
    monkeypatch.setattr(operations_api, "verified_request_actor", lambda: "owner@example.com")

    response = await operations_api.regenerate_content_operation(
        hospital.id,
        content.id,
        db=db,
        idempotency_key="legacy-1",
        body=LegacyBudgetReplacementRequest(reason="이전 비용 기록 확인 불가"),
    )

    attempt = content.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert response["detail"] == "Content regeneration queued"
    assert "reason" not in attempt and "retry_class" not in attempt
    assert attempt["budget"]["legacy_state"] == "REPLACED"
    assert attempt["budget"]["reset_record"]["previous_snapshot"] == _prod_record(reason)
    assert [row.action for row in db.added if isinstance(row, AdminAuditLog)][:1] == [
        "LEGACY_BUDGET_REPLACED"
    ]
    assert len(task.calls) == 1


async def test_admin_regenerate_without_legacy_evidence_keeps_the_old_path(monkeypatch) -> None:
    from test_admin_operations import FakeTask, _content

    from app.api.admin import operations as operations_api

    stored = _prod_record("CONTENT_NOT_GENERATED")
    content = _content(uuid.uuid4(), essence_check_summary={GENERATION_ATTEMPT_KEY: stored})
    hospital, db = _admin_db(content, incident_open=False)
    task = FakeTask()
    monkeypatch.setattr(operations_api.regenerate_content_item, "apply_async", task.apply_async)

    await operations_api.regenerate_content_operation(
        hospital.id, content.id, db=db, idempotency_key=None, body=None
    )

    assert content.essence_check_summary[GENERATION_ATTEMPT_KEY] == stored
    assert len(task.calls) == 1
