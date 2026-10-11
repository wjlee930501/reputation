"""자율 복구가 멈춘 원인 사고는 멈춘 실행이 모두 후속 성공을 가지면 닫힌다(2026-10-11, D5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.workers import autonomous_recovery, incident_backlog

NOW = datetime(2026, 10, 11, 5, 0, tzinfo=UTC)


def _incident(code: str | None):
    return SimpleNamespace(
        incident_type="OPERATION_RECOVERY_EXHAUSTED",
        safe_error_code=code,
        first_seen_at=NOW,
    )


class _Rows:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def execute(self, _statement):
        self.queries += 1
        return SimpleNamespace(all=lambda: self.rows)


def test_the_resolver_is_registered_for_the_cause_incident() -> None:
    resolver = incident_backlog.RESOLVERS["OPERATION_RECOVERY_EXHAUSTED"]
    assert resolver is incident_backlog._stopped_runs_recovered


def test_it_knows_every_code_autonomous_recovery_stops_with() -> None:
    assert incident_backlog.RECOVERY_STOP_CODES == frozenset(
        autonomous_recovery._RECOVERY_STOP_MESSAGES
    )


def test_an_unknown_code_is_never_closed_and_reads_nothing() -> None:
    db = _Rows([])
    assert incident_backlog._stopped_runs_recovered(db, _incident("TASK_FAILED"), NOW) is None
    assert incident_backlog._stopped_runs_recovered(db, _incident(None), NOW) is None
    assert db.queries == 0


def test_no_stopped_run_is_not_evidence() -> None:
    db = _Rows([])
    code = "OPERATION_REDISPATCH_EXHAUSTED"
    assert incident_backlog._stopped_runs_recovered(db, _incident(code), NOW) is None


def test_every_stopped_run_needs_its_own_follow_up_success(monkeypatch) -> None:
    recovered, pending = uuid.uuid4(), uuid.uuid4()
    rows = [SimpleNamespace(id=recovered), SimpleNamespace(id=pending)]
    code = "OPERATION_LEASE_EXPIRED"
    monkeypatch.setattr(
        incident_backlog, "_has_succeeded_follow_up", lambda _db, run: run.id == recovered
    )
    assert incident_backlog._stopped_runs_recovered(_Rows(rows), _incident(code), NOW) is None

    monkeypatch.setattr(incident_backlog, "_has_succeeded_follow_up", lambda _db, _run: True)
    assert (
        incident_backlog._stopped_runs_recovered(_Rows(rows), _incident(code), NOW)
        == "stopped_runs_recovered"
    )
