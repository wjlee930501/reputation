"""운영자 재생성과 글 단위 태스크의 claim·lease·재시도 정합성 (#179·#182·#184 후속).

- 운영자 재시도가 억제를 푼 뒤 작가가 예외로 끝나도 그 실패가 시도 기록으로 남아 억제가
  다시 걸린다. 예산 사다리는 풀린 기록의 계수에서 이어 센다.
- `regenerate_content_item`은 야간 생성과 같은 글 단위 lease를 잡는다. 살아 있는 다른
  claim이 있으면 작가를 부르지 않고 억제도 풀지 않은 채 CANCELLED(GENERATION_LEASE_ACTIVE)로
  끝난다. 자기 토큰으로 쓰고, 끝나면 자기 토큰만 푼다. 실제 SQL은
  `tests/integration/test_operator_regenerate_lease_postgres.py`가 본다.
- 글 단위 태스크(`generate_claimed_content_item`)의 rollback·해제 실패는 원래 예외를 가리지
  않고, 성공 경로의 실행 종결을 건너뛰게 하지 않는다.
- 비용 가드 보류(COST_BLOCKED)도 환경 실패라 운영자 재시도가 억제를 푼다.
- 게이트의 CONTENT_NOT_GENERATED 덮어쓰기는 원인이 바뀌어 계수를 0으로 되돌린다. 운영자
  해제가 남기는 것은 그 0이다.
- 해제 조건은 저장 원인의 정확 일치와 공백을 걷어 낸 본문으로 판정한다.
- Admin 강제 해제는 claim 시각과 토큰을 함께 지운다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from test_admin_operations import FakeDB, _content, _hospital
from test_operator_retry_gate_record import (
    _FAILED_AT,
    _SAME_DAY,
    _gate,
    _gate_kept_environment_failure,
    _gate_recorded_slot,
    _ladder,
    _press_retry,
    _slot_with_writer,
)
from test_topic_swap_fallback import _freeze, _generate_once, _kst, _WorkerDB

from app.api.admin import operations as operations_api
from app.models.operations import OperationRunState
from app.workers import tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass

_PRESS_AT = _kst(2026, 9, 16, 7, 50)


def _writer_raises(monkeypatch, error: BaseException) -> list:
    calls: list = []

    async def raising(**kwargs):
        calls.append(kwargs["item"].id)
        raise error

    monkeypatch.setattr(tasks, "_generate_with_auto_review", raising)
    return calls


def _record(item) -> dict:
    return dict(item.essence_check_summary[GENERATION_ATTEMPT_KEY])


# ── 운영자 해제 뒤 작가 예외: 억제가 다시 걸린다 ─────────────────────────────────


def test_a_raising_writer_after_an_environment_release_records_the_failure(monkeypatch):
    """게이트가 지킨 PROVIDER_TIMEOUT → 운영자 해제 → 작가 시간 초과. 스윕은 바로 다시 사지 않는다."""

    item, record, _calls, _seen = _gate_kept_environment_failure(monkeypatch, swapped=False)
    writer_calls = _writer_raises(monkeypatch, TimeoutError("provider timed out"))

    with pytest.raises(TimeoutError):
        _press_retry(monkeypatch, item, _PRESS_AT, operator=True)

    assert writer_calls == [item.id]
    stored = _record(item)
    assert stored["reason"] == "PROVIDER_TIMEOUT"
    assert stored["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    # 풀린 기록의 사다리에서 이어 센다 — 1회(07:00) + 운영자 재시도 1회.
    assert stored["provider_attempt_count"] == record["provider_attempt_count"] + 1 == 2
    assert stored["first_observed_at"] == record["first_observed_at"]
    assert stored["attempt_period"] == record["attempt_period"]
    assert datetime.fromisoformat(stored["next_retry_at"]) > _PRESS_AT

    # 자동 경로는 다음 시도 시각까지 억제된다(해제된 채 남으면 예산 없이 작가를 다시 산다).
    state, code, _message = _generate_once(monkeypatch, item, _PRESS_AT + timedelta(minutes=5))

    assert (state, code) == (tasks.GenerationItemState.SKIPPED, "PROVIDER_TIMEOUT")
    assert writer_calls == [item.id]


def test_a_raising_writer_after_a_gate_record_release_is_suppressed_again(monkeypatch):
    """게이트 CONTENT_NOT_GENERATED → 운영자 해제 → 작가 거절(표본 실패). 두 번째 클릭은 억제된다."""

    item, record, _calls, _seen = _gate_recorded_slot(monkeypatch)
    writer_calls = _writer_raises(
        monkeypatch, ValueError("GEO hard-fail: references is empty for FAQ")
    )

    with pytest.raises(ValueError):
        _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    stored = _record(item)
    assert stored["reason"] == "GENERATION_REJECTED"
    assert stored["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert stored["context"] == record["context"]
    assert stored["provider_attempt_count"] == 1

    again = _press_retry(monkeypatch, item, _SAME_DAY + timedelta(minutes=1), operator=True)

    assert writer_calls == [item.id]
    assert again == [(OperationRunState.FAILED, "GENERATION_REJECTED")]
    assert _record(item) == stored


def test_a_raising_writer_releases_its_own_lease(monkeypatch):
    item, _record_before, _calls, _seen = _gate_recorded_slot(monkeypatch)
    _writer_raises(monkeypatch, TimeoutError("provider timed out"))

    with pytest.raises(TimeoutError):
        _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert (item.generation_claim_token, item.generation_claimed_at) == (None, None)


# ── 운영자 재생성의 글 단위 lease ─────────────────────────────────────────────


def _hold_live_claim(item, moment) -> uuid.UUID:
    token = uuid.uuid4()
    item.generation_claim_token = token
    item.generation_claimed_at = (moment - timedelta(minutes=5)).astimezone(UTC)
    return token


def test_a_live_claim_of_another_job_cancels_the_operator_run_without_spending(monkeypatch):
    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    other = _hold_live_claim(item, _SAME_DAY)
    claimed_at = item.generation_claimed_at

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.CANCELLED, "GENERATION_LEASE_ACTIVE")]
    # 다른 작업의 lease와 억제 기록을 그대로 둔다 — 억제는 lease를 잡은 뒤에만 푼다.
    assert (item.generation_claim_token, item.generation_claimed_at) == (other, claimed_at)
    assert _record(item) == record


def test_the_operator_run_writes_with_its_own_lease_and_releases_it(monkeypatch):
    item, _record_before, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    tokens_at_writer: list = []
    writer = tasks._generate_with_auto_review

    async def observe(**kwargs):
        tokens_at_writer.append(kwargs["item"].generation_claim_token)
        return await writer(**kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", observe)

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    # NULL 토큰으로 쓰지 않는다 — 작가 호출 전에 잡은 자기 lease다.
    assert len(tokens_at_writer) == 1 and tokens_at_writer[0] is not None
    assert (item.generation_claim_token, item.generation_claimed_at) == (None, None)


def test_an_expired_claim_is_taken_over_by_the_operator_run(monkeypatch):
    item, _record_before, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    dead = uuid.uuid4()
    item.generation_claim_token = dead
    item.generation_claimed_at = (_SAME_DAY - timedelta(hours=3)).astimezone(UTC)
    tokens_at_writer: list = []
    writer = tasks._generate_with_auto_review

    async def observe(**kwargs):
        tokens_at_writer.append(kwargs["item"].generation_claim_token)
        return await writer(**kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", observe)

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert tokens_at_writer[0] not in (None, dead)
    assert item.generation_claim_token is None


def test_a_second_operator_dispatch_during_the_first_never_calls_the_writer(monkeypatch):
    """같은 글의 두 번째 운영자 실행(다른 Idempotency-Key의 RETRY_RUN 등)이 첫 실행 도중 도착한다."""

    item, _record_before, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    dispatched: list = []

    def dispatch_again_before_writing(*_args, **_kwargs):
        # 첫 실행이 억제를 풀고 작가를 부르기 직전(원고 계획 단계)에 두 번째 실행이 돈다.
        if not dispatched:
            dispatched.append("worker-2")
            task = tasks.regenerate_content_item
            task.push_request(
                id="worker-2",
                headers={"operation_run_id": str(uuid.uuid4())},
                operation_run_claim_version=2,
            )
            try:
                task.run(str(item.id))
            finally:
                task.pop_request()
        return {}

    monkeypatch.setattr(
        tasks, "prepare_automatic_content_brief_sync", dispatch_again_before_writing
    )

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert dispatched == ["worker-2"]
    assert writer_calls == [item.id]
    # 두 번째 실행이 먼저 끝난다(lease 없음), 이어 첫 실행이 성공으로 끝난다.
    assert finished == [
        (OperationRunState.CANCELLED, "GENERATION_LEASE_ACTIVE"),
        (OperationRunState.SUCCEEDED, None),
    ]
    assert item.generation_claim_token is None


def test_a_dispatch_without_an_operator_run_also_defers_to_a_live_claim(monkeypatch):
    """스케줄 저장 직후 생성 등 실행 없는 배포도 살아 있는 야간 claim과 겹쳐 쓰지 않는다."""

    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    item.essence_check_summary = None  # 억제 기록이 없어 lease만이 막는다
    other = _hold_live_claim(item, _SAME_DAY)

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=False)

    assert writer_calls == []
    assert finished == [(OperationRunState.CANCELLED, "GENERATION_LEASE_ACTIVE")]
    assert item.generation_claim_token == other


# ── 비용 가드 보류(COST_BLOCKED)도 운영자 재시도가 푼다 ──────────────────────────


def test_an_operator_retry_after_a_cost_block_buys_nothing_at_the_cap_and_once_after(monkeypatch):
    """COST_BLOCKED는 ENVIRONMENT_RECOVERABLE이다. 한도에서는 가드가 호출 시점에 막고(작가 0회),
    한도를 올린 뒤의 운영자 재시도는 작가를 정확히 한 번 부른다."""

    philosophy, item, writer_calls, _seen = _slot_with_writer(monkeypatch, swapped=False)
    _freeze(monkeypatch, _FAILED_AT)
    tasks._remember_generation_attempt(_WorkerDB(), item, philosophy, "COST_BLOCKED")
    blocked = _record(item)
    assert blocked["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert blocked["guard_deferral_count"] == 1
    assert datetime.fromisoformat(blocked["next_retry_at"]) > _PRESS_AT
    _gate(monkeypatch, item, philosophy)
    assert _record(item) == blocked  # 게이트가 스윕 소유 기록을 지킨다(#182)

    guard_calls: list = []
    cap = {"allowed": False}

    async def guard(*_args, **_kwargs):
        guard_calls.append(cap["allowed"])
        return SimpleNamespace(allowed=cap["allowed"], reason="daily cap")

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", guard)

    at_cap = _press_retry(monkeypatch, item, _PRESS_AT, operator=True)

    assert writer_calls == []
    assert guard_calls == [False]  # 억제가 풀려 가드까지 갔다 — 막은 것은 가드다
    assert at_cap == [(OperationRunState.FAILED, "COST_BLOCKED")]
    deferred = _record(item)
    assert deferred["reason"] == "COST_BLOCKED"
    assert deferred["guard_deferral_count"] == 2  # 사다리가 이어진다

    cap["allowed"] = True
    raised = _press_retry(monkeypatch, item, _PRESS_AT + timedelta(minutes=5), operator=True)

    assert writer_calls == [item.id]
    assert guard_calls == [False, True]
    assert raised == [(OperationRunState.SUCCEEDED, None)]


# ── 게이트의 CONTENT_NOT_GENERATED 덮어쓰기는 계수를 0으로 되돌린다 ───────────────


def test_the_gate_overwrite_resets_the_ladder_and_the_operator_release_keeps_the_reset(
    monkeypatch,
):
    """예산이 끝나 어떤 스윕도 집지 않는 표본 기록(`next_retry_at=None`)을 게이트가 덮는다.

    원인이 바뀌므로 `_remember_generation_attempt`가 계수·소진 일수·최초 관측 시각을
    새로 시작한다. 운영자 해제는 그 덮인 기록의 사다리를 그대로 남긴다 — 덮이기 전의
    계수가 아니다.
    """

    philosophy, item, writer_calls, seen_at_writer = _slot_with_writer(monkeypatch, swapped=True)
    item.essence_check_summary = {
        GENERATION_ATTEMPT_KEY: {
            "reason": "GENERATION_REJECTED",
            "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
            "context": tasks._generation_attempt_context(item, philosophy),
            "attempt_period": "2026-09-15",
            "provider_attempt_count": 2,
            "attempt_count": 2,
            "guard_deferral_count": 1,
            "exhausted_days": 3,
            "first_observed_at": "2026-09-13T16:00:04+00:00",
            "next_retry_at": None,
        }
    }

    _gate(monkeypatch, item, philosophy)

    overwritten = _record(item)
    assert overwritten["reason"] == "CONTENT_NOT_GENERATED"
    assert overwritten["context"] == tasks._generation_attempt_context(item, philosophy)
    assert overwritten["provider_attempt_count"] == 0
    assert overwritten["attempt_count"] == 0
    assert overwritten["guard_deferral_count"] == 0
    assert overwritten["exhausted_days"] == 0
    assert overwritten["first_observed_at"] != "2026-09-13T16:00:04+00:00"

    finished = _press_retry(monkeypatch, item, _PRESS_AT, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert seen_at_writer == [_ladder(overwritten)]
    assert seen_at_writer[0]["exhausted_days"] == 0


# ── 해제 조건: 원인 정확 일치·공백 본문 ───────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    ["CONTENT_AI_HARD_FINDING", "CONTENT_IMAGE_POLICY_REJECTED"],
)
def test_other_content_prefixed_reasons_are_not_released(monkeypatch, reason):
    """CONTENT_로 시작한다고 게이트 증상 기록이 아니다 — 원인은 정확히 일치해야 풀린다."""

    item, record, writer_calls, _seen = _gate_recorded_slot(monkeypatch)
    stored = {**record, "reason": reason}
    item.essence_check_summary = {GENERATION_ATTEMPT_KEY: stored}

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == []
    assert finished == [(OperationRunState.FAILED, reason)]
    assert _record(item) == stored


@pytest.mark.parametrize("body", [" ", "\n\t  \n"], ids=["space", "whitespace_lines"])
def test_a_whitespace_only_body_counts_as_empty_for_the_release(monkeypatch, body):
    item, record, writer_calls, seen_at_writer = _gate_recorded_slot(monkeypatch)
    item.body = body
    # 현재 기준과 다른 기준의 본문이라 저장 본문 경로가 아니라 동일 원인 억제 판정으로 간다.
    item.content_philosophy_id = uuid.uuid4()

    finished = _press_retry(monkeypatch, item, _SAME_DAY, operator=True)

    assert writer_calls == [item.id]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert seen_at_writer == [_ladder(record)]


# ── 글 단위 태스크의 종료 정리가 원래 결과를 가리지 않는다 ─────────────────────────


class _ReleaseFailed(Exception):
    pass


class _RollbackFailed(Exception):
    pass


class _ProviderExploded(RuntimeError):
    pass


class _ClaimedItemDB:
    def __init__(self, *, rollback_error: BaseException | None = None) -> None:
        self.rollback_error = rollback_error
        self.rollbacks = 0
        self.commits = 0

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _claimed_item_task(monkeypatch, db, body, *, release_error: BaseException | None):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital=SimpleNamespace(id=uuid.uuid4(), name="종료정리의원"),
        generation_claimed_at=datetime(2026, 9, 16, 22, 0, tzinfo=UTC),
    )
    token = uuid.uuid4()
    finished: list = []
    releases: list = []

    def release(_db, item_ids, **kwargs):
        releases.append(kwargs.get("expected_claim_token"))
        if release_error is not None:
            raise release_error
        return 1

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "claim_generation_lease", lambda *_args, **_kwargs: (item, token))
    monkeypatch.setattr(
        tasks, "_resolve_claimed_item_run", lambda *_args: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setattr(tasks, "GenerationItemRecorder", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(tasks, "_run_generation_item", body)
    monkeypatch.setattr(tasks, "release_unfinished_claims", release)
    monkeypatch.setattr(
        tasks,
        "_finish_claimed_item_run",
        lambda _db, _task, _item_id, _run, state, **kwargs: finished.append(
            (state, kwargs.get("safe_error_code"))
        ),
    )
    return item, token, finished, releases


def _succeeds(*_args, **_kwargs):
    return tasks.GenerationItemState.SUCCEEDED, None, None


def _explodes(*_args, **_kwargs):
    raise _ProviderExploded("provider exploded")


def test_a_failed_release_on_the_success_path_still_finishes_the_run(monkeypatch, caplog):
    db = _ClaimedItemDB()
    item, token, finished, releases = _claimed_item_task(
        monkeypatch, db, _succeeds, release_error=_ReleaseFailed("db went away")
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        tasks.generate_claimed_content_item.run(str(item.id), None)

    assert releases == [token]
    assert finished == [(OperationRunState.SUCCEEDED, None)]
    assert "_ReleaseFailed" in caplog.text
    assert "db went away" not in caplog.text  # 예외 이름만 남긴다


def test_a_failed_release_on_the_exception_path_keeps_the_original_exception(monkeypatch, caplog):
    db = _ClaimedItemDB()
    item, token, finished, releases = _claimed_item_task(
        monkeypatch, db, _explodes, release_error=_ReleaseFailed("db went away")
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        with pytest.raises(_ProviderExploded):
            tasks.generate_claimed_content_item.run(str(item.id), None)

    assert releases == [token]
    assert finished == []  # 예외는 종전처럼 전파되고 종결은 신호 경로가 맡는다
    assert "_ReleaseFailed" in caplog.text


def test_a_failed_rollback_on_the_exception_path_keeps_the_original_exception(monkeypatch, caplog):
    db = _ClaimedItemDB(rollback_error=_RollbackFailed("connection reset"))
    item, token, _finished, releases = _claimed_item_task(
        monkeypatch, db, _explodes, release_error=None
    )

    with caplog.at_level(logging.WARNING, logger=tasks.logger.name):
        with pytest.raises(_ProviderExploded):
            tasks.generate_claimed_content_item.run(str(item.id), None)

    assert releases == [token]  # rollback이 실패해도 해제는 시도한다
    assert "_RollbackFailed" in caplog.text


# ── Admin 강제 해제는 토큰까지 지운다 ─────────────────────────────────────────


async def test_owner_force_release_clears_the_claim_token_too():
    """claim 시각만 지우고 토큰을 남기면, 죽은 줄 알았던 워커의 토큰 가드 write-back이 여전히
    맞아 방금 풀어 준 슬롯에 늦은 결과를 쓸 수 있다."""

    claimed_at = datetime.now(UTC)
    hospital = _hospital()
    content = _content(
        hospital.id, title="검수 중인 제목", body=None, generation_claimed_at=claimed_at
    )
    statements: list = []

    class ForceReleaseDB(FakeDB):
        async def flush(self):
            return None

        async def rollback(self):
            return None

        async def execute(self, statement):
            if getattr(getattr(statement, "table", None), "name", None) == "content_items":
                statements.append(statement)
                return SimpleNamespace(scalar_one_or_none=lambda: content.id)
            return await super().execute(statement)

    db = ForceReleaseDB(hospital=hospital, content=content)
    actor = SimpleNamespace(id=uuid.uuid4(), email="owner@example.com", role="OWNER")

    await operations_api.force_release_generation_claim(
        hospital.id,
        content.id,
        operations_api.GenerationClaimReleaseRequest(
            expected_claimed_at=claimed_at, reason="stale worker confirmed"
        ),
        idempotency_key="release-claim-token-001",
        db=db,
        actor=actor,
    )

    (statement,) = statements
    compiled = statement.compile(dialect=postgresql.dialect())
    set_clause = str(compiled).split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "generation_claimed_at=" in set_clause
    assert "generation_claim_token=" in set_clause
    assert compiled.params["generation_claim_token"] is None
    assert compiled.params["generation_claimed_at"] is None
