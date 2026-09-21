"""자동 복구가 약속한 재시도를 실제로 수행하는지 — 기한·선택·claim의 한 사다리.

기한(`next_retry_at`)과 인시던트 기한(`sla_due_at`)이 같은 값을 읽고, 그 시각에
복구 스윕이 정말로 그 슬롯을 다시 집는지를 한 타임라인으로 고정한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.admin.operations_center_serializers import requires_operator_action
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.workers import (
    generation_incident_control,
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
)
from app.workers.generation_retry_policy import GenerationRetryClass

KST = ZoneInfo("Asia/Seoul")
_SLOT = date(2026, 9, 16)  # D


def _kst(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def _freeze(monkeypatch, moment: datetime) -> None:
    """워커·정책·로더가 같은 '지금'을 보게 한다."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


class _CommitOnlyDB:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _PageDB(_CommitOnlyDB):
    """로더의 keyset 페이지를 한 번에 하나씩 돌려준다."""

    def __init__(self, *pages) -> None:
        super().__init__()
        self._pages = [list(page) for page in pages]
        self.execute_calls = 0

    def execute(self, _stmt):
        page = self._pages[self.execute_calls] if self.execute_calls < len(self._pages) else []
        self.execute_calls += 1
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: page))


def _slot_item(*, scheduled_date: date = _SLOT):
    hospital_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        hospital=SimpleNamespace(id=hospital_id, name="사다리의원"),
        scheduled_date=scheduled_date,
        sequence_no=1,
        carried_over_from=None,
        content_type=SimpleNamespace(value="FAQ"),
        query_target_id=None,
        essence_check_summary=None,
        generation_claimed_at=None,
        generation_claim_token=None,
    )


def _remember(monkeypatch, db, item, moment: datetime, reason="GENERATION_REJECTED") -> dict:
    _freeze(monkeypatch, moment)
    return tasks._remember_generation_attempt(
        db, item, SimpleNamespace(id="p1"), reason
    )


def _stored_deadline(attempt: dict) -> datetime | None:
    raw = attempt.get("next_retry_at")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else None


def test_a_body_sample_failure_stays_automatic_until_the_third_exhausted_day(monkeypatch):
    """D-2 23:00 → D-1 23:00 → D 01:00·04:00 실패 뒤에도 사람의 일이 아니다."""

    db = _CommitOnlyDB()
    item = _slot_item()

    first = _remember(monkeypatch, db, item, _kst(2026, 9, 14, 23, 0))
    # 복구 스윕의 창은 앞으로도 야간 배치와 같은 lookahead를 가진다. D-1 01:00 스윕이
    # 이미 D 예정 슬롯을 집으므로 기한은 그 다음 23:00이 아니라 그 시각이다.
    assert _stored_deadline(first) == _kst(2026, 9, 15, 1, 0).astimezone(UTC)

    second = _remember(monkeypatch, db, item, _kst(2026, 9, 15, 23, 0))
    assert second["exhausted_days"] == 0  # 밤마다 하루 예산의 한 번씩만 쓴다
    assert _stored_deadline(second) == _kst(2026, 9, 16, 1, 0).astimezone(UTC)

    third = _remember(monkeypatch, db, item, _kst(2026, 9, 16, 1, 0))
    assert third["provider_attempt_count"] == 1  # KST 자정에 하루 예산이 초기화됐다
    assert _stored_deadline(third) == _kst(2026, 9, 16, 4, 0).astimezone(UTC)

    fourth = _remember(monkeypatch, db, item, _kst(2026, 9, 16, 4, 0))
    assert fourth["exhausted_days"] == 1  # 당일 예산(2회) 소진
    assert fourth["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    deadline = _stored_deadline(fourth)
    assert deadline == _kst(2026, 9, 17, 1, 0).astimezone(UTC)

    # 07:00 스윕 직후·07:45 마감·08:00 발행·23:00 배치 뒤·자정을 넘겨서도 자동 복구가
    # 소유한 상태다. 한 시점만 검사하면 이 누수를 놓친다.
    for moment in (
        _kst(2026, 9, 16, 7, 0, 1),
        _kst(2026, 9, 16, 7, 45),
        _kst(2026, 9, 16, 8, 0),
        _kst(2026, 9, 16, 23, 0, 1),
        _kst(2026, 9, 17, 0, 30),
    ):
        assert requires_operator_action("RETRYING", deadline, moment) is False

    # 기한이 지난 뒤에야 같은 규칙이 사람의 일로 읽는다.
    assert requires_operator_action("RETRYING", deadline, deadline + timedelta(minutes=1)) is True


def test_the_promised_sweep_actually_selects_and_claims_the_slot(monkeypatch):
    """기한으로 약속한 D+1 01:00 스윕이 그 슬롯을 실제로 선택·claim한다."""

    db = _CommitOnlyDB()
    item = _slot_item()
    for moment in (
        _kst(2026, 9, 14, 23, 0),
        _kst(2026, 9, 15, 23, 0),
        _kst(2026, 9, 16, 1, 0),
        _kst(2026, 9, 16, 4, 0),
    ):
        attempt = _remember(monkeypatch, db, item, moment)
    assert _stored_deadline(attempt) == _kst(2026, 9, 17, 1, 0).astimezone(UTC)

    # 예산이 남아 있던 04:00 직후에는 같은 슬롯을 다시 사지 않는다.
    _freeze(monkeypatch, _kst(2026, 9, 16, 4, 30))
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id="p1"))
    assert tasks._generation_retry_is_eligible(db)(item) is False

    _freeze(monkeypatch, _kst(2026, 9, 17, 1, 0))
    page_db = _PageDB([item])
    claimed, truncated, _scan_complete = tasks._load_nightly_generation_batch(
        page_db,
        _SLOT - timedelta(days=7),
        _SLOT,
        is_eligible=tasks._generation_retry_is_eligible(page_db),
    )

    assert [row.id for row in claimed] == [item.id]
    assert truncated == 0
    assert item.generation_claim_token is not None


def test_the_third_exhausted_day_becomes_operator_work_without_a_deadline(monkeypatch):
    db = _CommitOnlyDB()
    item = _slot_item()
    item.essence_check_summary = {
        "generation_attempt": {
            "context": tasks._generation_attempt_context(item, SimpleNamespace(id="p1")),
            "reason": "GENERATION_REJECTED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "provider_attempt_count": 1,
            "exhausted_days": 2,
            "attempt_period": "2026-09-17",
        }
    }

    attempt = _remember(monkeypatch, db, item, _kst(2026, 9, 17, 1, 0))

    assert attempt["exhausted_days"] == 3
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert "next_retry_at" not in attempt
    assert requires_operator_action("OPEN", None, _kst(2026, 9, 17, 1, 1)) is True


def test_a_decision_that_spent_no_budget_does_not_move_the_same_reasons_counters(monkeypatch):
    db = _CommitOnlyDB()
    item = _slot_item()
    spent = _remember(monkeypatch, db, item, _kst(2026, 9, 15, 23, 0))

    decided = _remember_without_counting(
        monkeypatch, db, item, _kst(2026, 9, 16, 7, 45), reason="GENERATION_REJECTED"
    )

    assert decided["provider_attempt_count"] == spent["provider_attempt_count"]
    assert decided["exhausted_days"] == spent["exhausted_days"]
    assert decided["guard_deferral_count"] == spent["guard_deferral_count"]


def test_a_decision_for_a_different_reason_starts_its_counters_at_zero(monkeypatch):
    """다른 원인의 계수를 물려받으면 새 원인이 처음부터 소진된 것처럼 보인다."""

    db = _CommitOnlyDB()
    item = _slot_item()
    _remember(monkeypatch, db, item, _kst(2026, 9, 15, 23, 0))

    decided = _remember_without_counting(monkeypatch, db, item, _kst(2026, 9, 16, 7, 45))

    assert decided["reason"] == "CONTENT_IMAGE_NOT_READY"
    assert decided["provider_attempt_count"] == 0
    assert decided["exhausted_days"] == 0
    assert decided["guard_deferral_count"] == 0


def _remember_without_counting(
    monkeypatch, db, item, moment: datetime, reason: str = "CONTENT_IMAGE_NOT_READY"
) -> dict:
    _freeze(monkeypatch, moment)
    return tasks._remember_generation_attempt(
        db,
        item,
        SimpleNamespace(id="p1"),
        reason,
        count_attempt=False,
    )


# ── 인시던트 기한 결정표 ──


class _FakeIncidentSession:
    def __init__(self, item) -> None:
        self.item = item
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def scalar(self, _statement):
        return None

    async def get(self, _model, _item_id):
        return self.item

    async def commit(self):
        self.commits += 1


async def _open_incident(monkeypatch, item, code: str):
    opened = Incident(
        id=uuid.uuid4(),
        severity=IncidentSeverity.HIGH,
        state=IncidentState.OPEN.value,
        customer_impact="",
        next_action="",
        admin_path="/operations",
        hospital_id=uuid.uuid4(),
        version=1,
        safe_error_code=code,
        safe_error_message="",
        episode_seq=1,
        sla_due_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def capture_request(_db, _request, **_kwargs):
        return opened

    async def retrying(_db, _incident_id, **_kwargs):
        opened.state = IncidentState.RETRYING.value
        return opened

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: _FakeIncidentSession(item),
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "mark_retrying", retrying)
    await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital_name="사다리의원",
        run_id=uuid.uuid4(),
        code=code,
        message="",
        notify=False,
    )
    return opened


@pytest.mark.asyncio
async def test_a_matching_attempt_lends_its_own_deadline_to_the_incident(monkeypatch):
    deadline = _kst(2026, 9, 17, 1, 0).astimezone(UTC)
    item = _slot_item()
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "CONTENT_IMAGE_NOT_READY",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "provider_attempt_count": 4,
            "exhausted_days": 1,
            "attempt_period": "2026-09-16",
            "next_retry_at": deadline.isoformat(),
        }
    }

    incident = await _open_incident(monkeypatch, item, "CONTENT_IMAGE_NOT_READY")

    assert incident.state == "RETRYING"
    assert incident.sla_due_at == deadline


@pytest.mark.asyncio
async def test_the_gate_records_a_canonical_attempt_before_opening(monkeypatch):
    """게이트 호출부가 정본 시도 기록을 먼저 남기고, 인시던트는 그 기한을 복사한다."""

    db = _CommitOnlyDB()
    item = _slot_item(scheduled_date=_SLOT)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))

    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "CONTENT_IMAGE_NOT_READY"
    )

    stored = item.essence_check_summary["generation_attempt"]
    # 정본이 갖춰야 하는 것: 지문·기간·계수·기한.
    assert stored["context"] == tasks._generation_attempt_context(item, SimpleNamespace(id="p1"))
    assert stored["attempt_period"] == "2026-09-16"
    assert stored["provider_attempt_count"] == 0  # 예산을 쓴 적이 없다
    assert stored["exhausted_days"] == 0
    assert stored["guard_deferral_count"] == 0
    deadline = datetime.fromisoformat(stored["next_retry_at"])
    assert deadline == _kst(2026, 9, 16, 12, 0).astimezone(UTC)

    incident = await _open_incident(monkeypatch, item, "CONTENT_IMAGE_NOT_READY")

    assert incident.state == "RETRYING"
    # 불변식: 워커가 읽는 다음 시도 시각과 인시던트 기한은 같은 값이다.
    assert incident.sla_due_at == deadline

    # 로더는 그 기한 전까지 이 슬롯을 다시 사지 않는다.
    monkeypatch.setattr(
        tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id="p1")
    )
    _freeze(monkeypatch, _kst(2026, 9, 16, 11, 59))
    assert tasks._generation_retry_is_eligible(db)(item) is False
    _freeze(monkeypatch, deadline.astimezone(KST))
    assert tasks._generation_retry_is_eligible(db)(item) is True


def test_the_morning_gate_does_not_reset_the_image_budget_it_only_observed(monkeypatch):
    """게이트가 본 "이미지 없음"이 워커가 결제한 원인 기록을 대신 쓰지 않는다.

    행복드림 슬롯이 그렇게 갇혔다. 밤에 이미지 생성이 실패해 예산을 한 번 쓰면, 07:45
    게이트가 증상(`CONTENT_IMAGE_NOT_READY`)으로 기록을 덮어써 계수를 0으로 돌렸다.
    그러면 하루 4회 예산이 매일 아침 초기화돼 `IMAGE_GENERATION_RETRIES_EXHAUSTED`에
    닿지 못하고, 저장된 원인이 `_IMAGE_FAILURE_REASONS` 밖으로 나가 예산 소진 뒤 같은
    병원의 인증 이미지를 빌리는 계약도 실행되지 않는다. 본문이 멀쩡한 슬롯이 매일 이미지를
    다시 사고 매일 아침 기록을 잃는다.
    """

    db = _CommitOnlyDB()
    item = _slot_item(scheduled_date=_SLOT)
    philosophy = SimpleNamespace(id="p1")

    # 밤 스윕이 이미지 생성 실패를 기록한다 — 하루 4회 예산 중 두 번.
    for moment in (_kst(2026, 9, 16, 1, 0), _kst(2026, 9, 16, 4, 0)):
        _remember(monkeypatch, db, item, moment, reason="IMAGE_GENERATION_FAILED")
    spent = tasks._stored_generation_attempt(item)
    assert spent["provider_attempt_count"] == 2

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    assessment = SimpleNamespace(
        code="CONTENT_IMAGE_NOT_READY", message="대표 이미지가 아직 준비되지 않았습니다."
    )
    # 게이트는 증상이 아니라 저장된 원인을 보고한다.
    code, _message = tasks._publication_block_details(item, assessment)
    assert code == "IMAGE_GENERATION_FAILED"

    tasks._record_gate_blocker_decision(db, item, philosophy, code)
    after = tasks._stored_generation_attempt(item)

    assert after["reason"] == "IMAGE_GENERATION_FAILED"
    assert after["provider_attempt_count"] == 2  # 아침 관측이 예산을 되돌리지 않는다
    assert after["next_retry_at"] == spent["next_retry_at"]

    # 남은 두 번을 더 쓰면 종착으로 올라가고, 그때 재사용 자격이 열린다.
    for moment in (_kst(2026, 9, 16, 7, 0), _kst(2026, 9, 16, 12, 0)):
        _remember(monkeypatch, db, item, moment, reason="IMAGE_GENERATION_FAILED")
    exhausted = tasks._stored_generation_attempt(item)

    assert exhausted["reason"] == "IMAGE_GENERATION_RETRIES_EXHAUSTED"
    assert exhausted["exhausted_days"] == 1
    assert tasks._image_reuse_is_due(item) is True

    # 그 뒤의 아침 관측도 종착 기록을 지우지 않는다.
    _freeze(monkeypatch, _kst(2026, 9, 17, 7, 45))
    tasks._record_gate_blocker_decision(db, item, philosophy, "CONTENT_IMAGE_NOT_READY")

    assert tasks._stored_generation_attempt(item) == exhausted
    assert tasks._image_reuse_is_due(item) is True


def test_a_cost_guard_deferral_survives_the_morning_image_observation(monkeypatch):
    """비용 가드 보류도 게이트의 증상 기록에 덮이지 않는다 — 가드가 알림을 소유한다."""

    db = _CommitOnlyDB()
    item = _slot_item(scheduled_date=_SLOT)
    blocked = _remember(monkeypatch, db, item, _kst(2026, 9, 16, 1, 0), reason="COST_BLOCKED")

    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "CONTENT_IMAGE_NOT_READY"
    )

    stored = tasks._stored_generation_attempt(item)
    assert stored["reason"] == "COST_BLOCKED"
    assert stored["guard_deferral_count"] == blocked["guard_deferral_count"]


@pytest.mark.asyncio
async def test_repeated_gate_observations_keep_the_same_decision(monkeypatch):
    """07:45과 08:00이 같은 원인을 다시 봐도 기록과 기한은 그대로다."""

    db = _CommitOnlyDB()
    item = _slot_item(scheduled_date=_SLOT)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "CONTENT_IMAGE_NOT_READY"
    )
    first = dict(item.essence_check_summary["generation_attempt"])

    _freeze(monkeypatch, _kst(2026, 9, 16, 8, 0))
    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "CONTENT_IMAGE_NOT_READY"
    )

    assert item.essence_check_summary["generation_attempt"] == first
    incident = await _open_incident(monkeypatch, item, "CONTENT_IMAGE_NOT_READY")
    assert incident.sla_due_at == datetime.fromisoformat(first["next_retry_at"])


@pytest.mark.asyncio
async def test_an_unrecorded_blocker_still_gets_a_derived_deadline(monkeypatch):
    """게이트가 기록을 남기지 못한 경로에서도 다른 원인의 기한을 빌리지 않는다."""

    item = _slot_item(scheduled_date=_SLOT)

    incident = await _open_incident(monkeypatch, item, "CONTENT_IMAGE_NOT_READY")

    assert incident.state == "RETRYING"
    assert incident.sla_due_at is not None
    # 방어선일 뿐이므로 저장하지 않는다 — 시도 기록의 소유자는 워커·게이트다.
    assert item.essence_check_summary is None


@pytest.mark.asyncio
async def test_a_terminal_block_opens_without_a_deadline(monkeypatch):
    item = _slot_item()
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "GENERATION_REJECTED",
            "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
            "exhausted_days": 3,
            "next_retry_at": _kst(2026, 9, 16, 1, 0).astimezone(UTC).isoformat(),
        }
    }

    incident = await _open_incident(monkeypatch, item, "GENERATION_REJECTED")

    assert incident.state == "OPEN"
    assert incident.sla_due_at is None
    assert "next_retry_at" not in item.essence_check_summary["generation_attempt"]


@pytest.mark.parametrize("code", ["FORBIDDEN_EXPRESSION", "CONTENT_AI_REVIEW_STALE"])
@pytest.mark.asyncio
async def test_an_input_change_blocker_is_operator_work_even_with_repair_budget(
    monkeypatch, code
):
    """입력 변경이 필요한 원인은 본문 수리 예산이 남아 있어도 RETRYING이 아니다."""

    item = _slot_item()
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": code,
            "retry_class": GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
            "next_retry_at": _kst(2026, 9, 16, 1, 0).astimezone(UTC).isoformat(),
        }
    }

    incident = await _open_incident(monkeypatch, item, code)

    assert incident.state == "OPEN"
    assert incident.sla_due_at is None
    assert "next_retry_at" not in item.essence_check_summary["generation_attempt"]

    # 다음 게이트가 같은 원인을 다시 봐도 되살아나지 않는다.
    repeated = await _open_incident(monkeypatch, item, code)
    assert (repeated.state, repeated.sla_due_at) == ("OPEN", None)


@pytest.mark.asyncio
async def test_a_repair_blocker_is_system_work_while_a_session_remains(monkeypatch):
    """수리 세션이 남아 있으면 저장된 OPERATOR_REQUIRED보다 예산이 앞선다."""

    db = _CommitOnlyDB()
    item = _slot_item()
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    stored = item.essence_check_summary["generation_attempt"]
    assert stored["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    deadline = datetime.fromisoformat(stored["next_retry_at"])
    assert deadline == _kst(2026, 9, 16, 12, 0).astimezone(UTC)

    incident = await _open_incident(monkeypatch, item, "MISSING_REFERENCES")

    assert incident.state == "RETRYING"
    assert incident.sla_due_at == deadline


@pytest.mark.asyncio
async def test_a_repair_blocker_becomes_operator_work_when_its_budget_is_gone(monkeypatch):
    db = _CommitOnlyDB()
    item = _slot_item()
    item.essence_check_summary = {
        "automatic_body_repair": {
            "period": "2026-09-16",
            "count": 2,
            "exhausted_days": 3,
        }
    }
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        db, item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    assert item.essence_check_summary["generation_attempt"]["next_retry_at"] is None

    incident = await _open_incident(monkeypatch, item, "MISSING_REFERENCES")

    assert (incident.state, incident.sla_due_at) == ("OPEN", None)


@pytest.mark.parametrize(
    "repair_state, expected_state",
    [
        (None, "RETRYING"),
        ({"period": "2026-09-16", "count": 2, "exhausted_days": 3}, "OPEN"),
    ],
)
@pytest.mark.asyncio
async def test_the_worker_record_and_the_gate_record_agree(
    monkeypatch, repair_state, expected_state
):
    """워커가 남긴 기록과 게이트가 남긴 기록이 같은 결론을 준다."""

    db = _CommitOnlyDB()
    states = []
    for count_attempt in (True, False):
        item = _slot_item()
        if repair_state is not None:
            item.essence_check_summary = {"automatic_body_repair": dict(repair_state)}
        _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
        if count_attempt:  # 워커 경로: 실제 실패를 기록한다
            tasks._remember_generation_attempt(
                db, item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
            )
        else:  # 게이트 경로: 예산을 쓰지 않은 결정만 남긴다
            tasks._record_gate_blocker_decision(
                db, item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
            )
        incident = await _open_incident(monkeypatch, item, "MISSING_REFERENCES")
        states.append((incident.state, incident.sla_due_at))

    assert states[0] == states[1]
    assert states[0][0] == expected_state


@pytest.mark.asyncio
async def test_missing_approved_essence_keeps_its_null_deadline(monkeypatch):
    item = _slot_item()

    incident = await _open_incident(monkeypatch, item, "MISSING_APPROVED_ESSENCE")

    assert incident.state == "RETRYING"
    assert incident.sla_due_at is None
    assert item.essence_check_summary is None  # Essence 처리가 소유한다 — 기록하지 않는다


@pytest.mark.asyncio
async def test_a_lease_report_does_not_borrow_another_causes_deadline(monkeypatch):
    item = _slot_item()

    incident = await _open_incident(monkeypatch, item, "GENERATION_LEASE_ACTIVE")

    assert incident.state == "OPEN"
    assert incident.sla_due_at == datetime(2026, 1, 1, tzinfo=UTC)  # 현행 정책 유지
    assert item.essence_check_summary is None


# ── 배치 합계: 로더가 거른 행은 SKIPPED로 기록되지 않는다 ──


class _CountingRecorder:
    def __init__(self) -> None:
        self.states: list = []

    def record(self, _item_id, state, **_kwargs) -> None:
        self.states.append(state)

    def item_run(self, *_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())


def test_prefiltered_rows_never_reach_the_batch_totals(monkeypatch):
    db = _PageDB([])
    recorder = _CountingRecorder()
    monkeypatch.setattr(tasks, "load_stuck_claims", lambda *_args: [])
    monkeypatch.setattr(
        tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id="p1")
    )

    dispatched = tasks._dispatch_generation_batch(
        db,
        recorder,
        _SLOT - timedelta(days=7),
        _SLOT,
        now_kst=None,
        notify=False,
    )

    assert dispatched == 0
    assert recorder.states == []  # 재시도 불가 행은 claim도 SKIPPED 기록도 만들지 않는다


def test_a_relocated_backlog_slot_is_simply_due_at_the_next_sweep(monkeypatch):
    """백로그 복구가 날짜를 옮기면 지나간 22:30+1h 기한은 그대로 '도래'가 된다."""

    db = _CommitOnlyDB()
    stranded = _slot_item(scheduled_date=date(2026, 9, 1))
    attempt = _remember(monkeypatch, db, stranded, _kst(2026, 9, 16, 4, 0))
    # 창 밖 슬롯은 22:30 백로그 복구가 소유한다.
    assert _stored_deadline(attempt) == _kst(2026, 9, 16, 23, 30).astimezone(UTC)

    monkeypatch.setattr(
        tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id="p1")
    )
    _freeze(monkeypatch, _kst(2026, 9, 16, 22, 0))
    assert tasks._generation_retry_is_eligible(db)(stranded) is False

    # 22:30 복구가 슬롯을 내일로 옮긴다. 저장된 기한은 이미 지난 시각이 되어 23:00
    # 야간 배치가 정상 경로로 이 슬롯을 집는다.
    stranded.scheduled_date = date(2026, 9, 17)
    _freeze(monkeypatch, _kst(2026, 9, 17, 0, 0))
    assert tasks._generation_retry_is_eligible(db)(stranded) is True

    page_db = _PageDB([stranded])
    claimed, truncated, scan_complete = tasks._load_nightly_generation_batch(
        page_db,
        date(2026, 9, 17),
        date(2026, 9, 18),
        is_eligible=tasks._generation_retry_is_eligible(page_db),
    )

    assert [row.id for row in claimed] == [stranded.id]
    assert (truncated, scan_complete) == (0, True)


def test_a_body_row_is_claimed_even_when_its_stored_attempt_is_unchanged(monkeypatch):
    """본문이 있는 행은 수리·이미지 재사용 경로로 가야 하므로 로더가 거르지 않는다."""

    db = _CommitOnlyDB()
    repairable = _slot_item()
    repairable.body = "저장된 본문"
    empty = _slot_item()
    empty.body = None
    _freeze(monkeypatch, _kst(2026, 9, 16, 4, 0))
    for item in (repairable, empty):
        _remember(monkeypatch, db, item, _kst(2026, 9, 16, 4, 0), reason="CONTENT_IMAGE_NOT_READY")

    monkeypatch.setattr(
        tasks, "_generation_philosophy_sync", lambda *_args: SimpleNamespace(id="p1")
    )
    _freeze(monkeypatch, _kst(2026, 9, 16, 4, 30))
    is_eligible = tasks._generation_retry_is_eligible(db)

    assert is_eligible(repairable) is True
    assert is_eligible(empty) is False
