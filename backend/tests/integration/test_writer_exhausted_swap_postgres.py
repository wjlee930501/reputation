"""작가 예산을 다 쓴 **본문 있는** 비-Critical 슬롯이 다음 스윕에서 주제를 바꾸는가 — 실제 PostgreSQL.

2026-10-15 실례(578acbf2, 마포성모탑정형외과 10/16): 본문이 비-Critical HARD 두 문장에 막혔고, 저장 본문
수리가 작가를 사려다 현재 주제의 작가 예산(6)에 막혔다. 워커의 예외 경로는 본문이 없는 슬롯(또는 갱신
구간이 열린 슬롯)만 시도 기록을 남겼으므로 생성 예산 소진 원인이 저장되지 않았다 — 교체 pass의 후보 조건
(원인 = GENERATION_BUDGET_EXHAUSTED)이 영영 맞지 않아 스윕마다 같은 실패를 되풀이했고, 그 사이 사고는
사람의 할 일(OPEN·HIGH)로 열렸다.

고친 뒤: 예외 경로가 본문 있는 슬롯의 소진도 기록한다(저장 본문·검수는 그대로) → 사고는 교체가 소유한
RETRYING(기한 = 교체할 첫 스윕, MEDIUM) → 다음 스윕의 교체 pass가 같은 슬롯·예정일로 주제를 바꾼다.
Critical 판정이 있는 본문은 종전대로 그 판정을 덮지 않는다.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.finding_criticality import CRITICAL_RULE, CRITICAL_RULE_KEY
from app.workers import generation_incident_control, tasks, topic_swap_fallback
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    WRITER_RESULT_LIMIT,
    GenerationBudgetLedger,
    current_topic_writers_exhausted,
    fresh_generation_attempt,
)
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.integration.test_noncritical_renewal_postgres import (
    KST,
    _attempt,
    _buy_until_exhausted,
    _pass,
    _row,
    _seed,
)
from tests.integration.test_noncritical_renewal_postgres import (
    _freeze as _freeze_workers,
)
from tests.test_generation_recovery_ladder import _open_incident

BODY = "## 안내\n" + "손목 통증은 원인이 다양해 진찰로 원인을 확인합니다. " * 60
SPECIALTY_SENTENCE = (
    "진료과목은 정형외과, 마취통증의학과, 신경외과, 재활의학과이고, 마포역 일대에서도 방문하는 분이 많은 위치입니다."
)


def _review(*, critical: bool) -> dict:
    return {
        "status": "REVISE",
        "blocking": True,
        "schema_version": "content-review-v4",
        "findings": [
            {
                "kind": "HOSPITAL_FACT",
                "severity": "HARD",
                "critical": critical,
                # 확정 정의(2026-10-15)로 판정한 v4 지적 — 표지 없는 v3 병원 사실은 재등급으로 비-Critical이다.
                CRITICAL_RULE_KEY: CRITICAL_RULE,
                "target": "CANDIDATE_TEXT",
                "quote": SPECIALTY_SENTENCE,
                "message": "'방문하는 분이 많은'은 승인 자료에 근거가 없습니다.",
            }
        ],
    }


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def swap_settings(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_NONCRITICAL_DAILY_RENEWAL_SESSIONS", 1)
    monkeypatch.setattr(settings, "CONTENT_AUTO_TOPIC_SWAP_MAX", 2)
    monkeypatch.setattr(settings, "CONTENT_FINDING_TIERS_ENABLED", True)

    async def _recover(_incident_id):
        return True

    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", _recover)


def _freeze(monkeypatch, moment: datetime) -> None:
    _freeze_workers(monkeypatch, moment)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(generation_incident_control, "datetime", _Frozen)


def _bodied_writer_exhausted_slot(pg_conn, pg_session, *, critical: bool):
    """본문·비-Critical(또는 Critical) HARD 검수가 있고 현재 주제의 작가 6회를 다 쓴 슬롯."""

    item_id, target = _seed(pg_conn, summary=json.dumps({}))
    row = _row(pg_session, item_id)
    row.title = "도화동 손목 통증, 어디서 진료받을 수 있나요?"
    row.body = BODY + SPECIALTY_SENTENCE
    row.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: fresh_generation_attempt(topic_id=str(target)),
        "ai_review": _review(critical=critical),
    }
    pg_session.commit()
    assert _buy_until_exhausted(pg_session, item_id) == WRITER_RESULT_LIMIT
    # 저장 본문의 차단이 남긴 표본 기록(교체 후보가 아니다) — 실례와 같은 모양.
    tasks._remember_generation_attempt(
        pg_session, _row(pg_session, item_id), None, "CONTENT_AI_HARD_FINDING"
    )
    attempt = _attempt(pg_session, item_id)
    assert attempt["reason"] == "CONTENT_AI_HARD_FINDING"
    assert current_topic_writers_exhausted(attempt) is True
    return item_id, target


def _run_worker_into_budget_wall(pg_session, monkeypatch, item_id) -> list:
    """저장 본문 수리가 작가를 사려다 원장에 막히는 실행 — 공급자 호출은 없다."""

    def _repair_buys_a_writer(db, item, _hospital, **_kwargs):
        GenerationBudgetLedger(db, item).reserve(
            acquisition_id=f"writer:{uuid.uuid4()}", kind="WRITER"
        )
        raise AssertionError("작가 예산이 남아 있으면 안 된다")

    opened: list = []

    async def _capture_incident(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(tasks, "_generate_single_content_item", _repair_buys_a_writer)
    monkeypatch.setattr(tasks, "open_generation_incident", _capture_incident)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: None)
    recorder = SimpleNamespace(
        record=lambda *_a, **_k: None,
        item_run=lambda *_a, **_k: SimpleNamespace(id=uuid.uuid4()),
    )
    row = _row(pg_session, item_id)
    hospital = SimpleNamespace(id=row.hospital_id, name="마포탑정형외과의원")
    state, code, _message = tasks._run_generation_item(pg_session, recorder, row, hospital)
    assert state == tasks.GenerationItemState.FAILED
    assert code == "GENERATION_BUDGET_EXHAUSTED"
    return opened


def test_a_writer_exhausted_bodied_noncritical_slot_swaps_on_the_next_sweep(
    pg_conn, pg_session, monkeypatch
):
    moment = datetime(2026, 9, 15, 23, 0, 5, tzinfo=KST)  # 예정일(9/16) 전날 23:00
    _freeze(monkeypatch, moment)
    item_id, first_target = _bodied_writer_exhausted_slot(pg_conn, pg_session, critical=False)
    # 종전 동작: 교체 pass가 이 슬롯을 보지 못한다.
    assert topic_swap_fallback.exhausted_body_sample_reason(_row(pg_session, item_id)) is None

    opened = _run_worker_into_budget_wall(pg_session, monkeypatch, item_id)

    row = _row(pg_session, item_id)
    attempt = _attempt(pg_session, item_id)
    # GAP 1: 소진 원인이 기록된다 — 저장 본문·검수는 그대로다.
    assert attempt["reason"] == "GENERATION_BUDGET_EXHAUSTED"
    assert attempt["retry_class"] in {
        GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        GenerationRetryClass.OPERATOR_REQUIRED.value,
    }
    assert current_topic_writers_exhausted(attempt) is True
    assert row.body.endswith(SPECIALTY_SENTENCE)
    assert row.essence_check_summary["ai_review"] == _review(critical=False)
    assert topic_swap_fallback.exhausted_body_sample_reason(row) == "GENERATION_BUDGET_EXHAUSTED"

    # GAP 3: 사고는 사람의 일이 아니라 교체가 소유한 RETRYING(기한 있음)이다.
    assert [call["code"] for call in opened] == ["GENERATION_BUDGET_EXHAUSTED"]
    code = "GENERATION_BUDGET_EXHAUSTED"
    assert generation_incident_control.generation_block_is_terminal(code, row) is False
    assert generation_incident_control.scheduled_recovery_owns_blocker(code, row) is True
    deadline = generation_incident_control.scheduled_recovery_deadline(row, code)
    assert deadline is not None and deadline > moment.astimezone(UTC)
    assert deadline <= (moment + timedelta(days=1)).astimezone(UTC)
    incident = asyncio.run(_open_incident(monkeypatch, row, code))
    assert incident.state == "RETRYING"
    assert incident.severity == "MEDIUM"
    assert incident.sla_due_at == deadline

    # 다음 스윕: 교체 pass가 같은 슬롯·예정일로 다른 질문을 쓴다.
    report = _pass(pg_session, moment + timedelta(hours=2))
    assert report.swapped == 1
    swapped = _row(pg_session, item_id)
    assert swapped.query_target_id != first_target
    assert swapped.body is None
    assert swapped.scheduled_date == row.scheduled_date
    assert _attempt(pg_session, item_id)["reason"] == topic_swap_fallback.TOPIC_SWAPPED_REASON


def test_a_critical_verdict_on_the_body_keeps_its_ladder(pg_conn, pg_session, monkeypatch):
    """Critical 판정이 있는 본문은 생성 예산 소진으로 덮지 않는다 — Critical 계단(교정 → 교체 → 사람)이 맡는다."""

    _freeze(monkeypatch, datetime(2026, 9, 15, 23, 0, 5, tzinfo=KST))
    item_id, _target = _bodied_writer_exhausted_slot(pg_conn, pg_session, critical=True)

    _run_worker_into_budget_wall(pg_session, monkeypatch, item_id)

    row = _row(pg_session, item_id)
    assert _attempt(pg_session, item_id)["reason"] == "CONTENT_AI_HARD_FINDING"
    assert topic_swap_fallback.exhausted_body_sample_reason(row) is None
    assert (
        generation_incident_control.topic_swap_owns_blocker("GENERATION_BUDGET_EXHAUSTED", row)
        is False
    )


@pytest.mark.parametrize(
    ("fields", "owned"),
    [
        # 표본 3일 소진으로 OPERATOR_REQUIRED가 된 비-Critical 기록도 교체가 남았으면 사람의 일이 아니다.
        ({"reason": "GENERATION_BUDGET_EXHAUSTED", "retry_class": "OPERATOR_REQUIRED"}, True),
        ({"reason": "CONTENT_AI_HARD_FINDING", "retry_class": "OPERATOR_REQUIRED"}, True),
        # Critical 교체 요청(교정 상한 소진)은 종전대로 사람에게 보인다.
        (
            {
                "reason": "CONTENT_AI_HARD_FINDING",
                "retry_class": "OPERATOR_REQUIRED",
                "auto_correction_exhausted": True,
            },
            False,
        ),
    ],
)
def test_a_swap_pending_noncritical_incident_is_retrying_not_operator_work(
    pg_conn, pg_session, monkeypatch, fields, owned
):
    moment = datetime(2026, 9, 15, 23, 0, 5, tzinfo=KST)
    _freeze(monkeypatch, moment)
    item_id, _target = _bodied_writer_exhausted_slot(pg_conn, pg_session, critical=False)
    row = _row(pg_session, item_id)
    summary = dict(row.essence_check_summary)
    summary[GENERATION_ATTEMPT_KEY] = {**summary[GENERATION_ATTEMPT_KEY], **fields}
    row.essence_check_summary = summary
    pg_session.commit()
    row = _row(pg_session, item_id)
    code = fields["reason"]

    assert generation_incident_control.topic_swap_owns_blocker(code, row) is owned
    assert generation_incident_control.generation_block_is_terminal(code, row) is (not owned)
    incident = asyncio.run(_open_incident(monkeypatch, row, code))
    if owned:
        assert (incident.state, incident.severity) == ("RETRYING", "MEDIUM")
        assert incident.sla_due_at is not None
        assert _pass(pg_session, moment + timedelta(hours=2)).swapped == 1
    else:
        assert incident.state == "OPEN"
        assert incident.sla_due_at is None
