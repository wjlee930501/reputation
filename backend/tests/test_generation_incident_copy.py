"""생성 인시던트의 조치 문구와 기한은 저장된 시도 기록이 말하는 사실과 같다(#179 후속).

- 원고 미생성(CONTENT_NOT_GENERATED): 게이트가 기록한 뒤에는 어떤 스윕도 다시 쓰지 않는다
  (OPERATOR_REQUIRED, 기한 없음). 예전 문구 '자동 복구는 01시·04시·07시·07시 45분에도 다시
  실행됩니다'는 그 뒤에 사실이 아니었고 12·18·22시 스윕도 빠져 있었다. 스윕이 아직 소유한
  슬롯(주제 교체 뒤의 새 주제)은 저장된 다음 시도 시각을 말한다.
- 공급자 장애(PROVIDER_TIMEOUT·PROVIDER_UNAVAILABLE): 환경 예산을 다 쓰면 스윕이 더 집지
  않는데도 '지금은 기다리세요'가 남았다. 소진되면 사람이 할 일을 말한다. 문구가 권하는
  “작업 다시 시도”가 실제로 원고를 쓰는지는 `test_generation_incident_copy_retry.py`가 본다.
- 재사용된 RETRYING 인시던트: 원고 미생성 관측이 앞선 원인의 인시던트를 재사용하면 그 원인의
  옛 기한이 그대로 남았다. 지금 시도 기록의 기한으로 바꾸고, 기한이 없으면 사람의 일로 드러낸다.
- 독립 검수 HARD 지적: '해당 항목을 종료하세요'는 없는 조작이다. 콘텐츠 화면의 실제 조작만 말한다.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.admin.operations_center_serializers import requires_operator_action
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.notification_copy import (
    REFERENCES_OPERATOR_DECIDES_COPY_CODE,
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE,
    blocker_copy,
    display_time,
)
from app.workers import generation_incident_control, generation_retry_policy, tasks
from app.workers.generation_incident_control import (
    CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION,
    CONTENT_NOT_GENERATED_OPERATOR_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_ACTION,
    CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION,
    ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION,
    PROVIDER_RETRY_NOW_ACTIONS,
    REFERENCES_OPERATOR_DECIDES_ACTION,
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION,
    generation_operator_action,
)
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.test_reference_operator_copy import NONEXISTENT_ACTIONS, require_admin_source

KST = ZoneInfo("Asia/Seoul")
_SLOT = date(2026, 9, 16)  # D
_REPO = Path(__file__).resolve().parents[2]
_OPERATION_DETAIL = _REPO / "admin" / "app" / "operations" / "OperationDetail.tsx"
_CONTENT_PAGE = _REPO / "admin" / "app" / "hospitals" / "[id]" / "content" / "page.tsx"


def _kst(month, day, hour=0, minute=0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KST)


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, generation_incident_control):
        monkeypatch.setattr(module, "datetime", _Frozen)


class _CommitOnlyDB:
    def commit(self) -> None:
        pass


def _slot(*, body=None):
    hospital_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        hospital=SimpleNamespace(id=hospital_id, name="문구의원"),
        scheduled_date=_SLOT,
        sequence_no=1,
        content_type=SimpleNamespace(value="FAQ"),
        query_target_id=None,
        essence_check_summary=None,
        body=body,
        title=None,
        topic_swap_history=[],
    )


def _remember(monkeypatch, item, moment, reason, *, count_attempt=True) -> dict:
    _freeze(monkeypatch, moment)
    return tasks._remember_generation_attempt(
        _CommitOnlyDB(), item, SimpleNamespace(id="p1"), reason, count_attempt=count_attempt
    )


def _exhaust_provider(monkeypatch, item, code="PROVIDER_TIMEOUT") -> dict:
    """D-1 23:00 야간 배치와 D 01·04·07시 복구 — 환경 예산 4회를 모두 쓴다."""

    for moment in (_kst(9, 15, 23), _kst(9, 16, 1), _kst(9, 16, 4), _kst(9, 16, 7)):
        attempt = _remember(monkeypatch, item, moment, code)
    return attempt


class _Session:
    """`scalar`는 차례로 (같은 키의 이전 인시던트, 원인 인시던트)를 돌려준다."""

    def __init__(self, item, scalars=()) -> None:
        self.item = item
        self._scalars = list(scalars)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def scalar(self, _statement):
        return self._scalars.pop(0) if self._scalars else None

    async def get(self, _model, _item_id):
        return self.item

    async def commit(self):
        pass


def _incident(code: str, *, state: str, sla_due_at=None) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        severity=IncidentSeverity.HIGH,
        state=state,
        customer_impact="",
        next_action="앞선 원인의 조치",
        admin_path="/operations",
        hospital_id=uuid.uuid4(),
        version=3,
        safe_error_code=code,
        safe_error_message="",
        episode_seq=1,
        sla_due_at=sla_due_at,
    )


async def _open(monkeypatch, item, code, *, scalars=()):
    """인시던트 저장소만 바꾸고 상태·기한·문구 결정은 실제 경로로 돌린다."""

    captured: dict[str, object] = {}
    opened = _incident(code, state=IncidentState.OPEN.value)

    async def capture_request(_db, request, **_kwargs):
        captured["request"] = request
        opened.next_action = request.next_action
        return opened

    async def retrying(_db, _incident_id, **_kwargs):
        if opened.state != IncidentState.OPEN.value:
            return None  # 실제 전이처럼 OPEN에서만 RETRYING이 된다
        opened.state = IncidentState.RETRYING.value
        return opened

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: _Session(item, scalars),
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "mark_retrying", retrying)
    await generation_incident_control.open_generation_incident(
        item_id=item.id,
        hospital_id=item.hospital_id,
        hospital_name="문구의원",
        run_id=uuid.uuid4(),
        code=code,
        message="",
        notify=False,
    )
    return captured.get("request"), opened


# ── 3a 원고 미생성 ─────────────────────────────────────────────────────────────


def test_the_not_generated_action_no_longer_promises_the_overnight_sweeps():
    action = generation_operator_action("CONTENT_NOT_GENERATED")

    assert action == CONTENT_NOT_GENERATED_OPERATOR_ACTION
    assert action == (
        "예약된 자동 복구는 이 글의 원고 생성을 다시 시도하지 않습니다(운영 기준이 새로 승인되는 등 "
        "생성 조건이 바뀔 때만 다시 시도합니다). 운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요."
    )
    assert "만듭니다" not in action  # 결과(원고)를 약속하지 않는다 — 시도만 말한다
    assert "01시·04시·07시·07시 45분" not in action
    assert "기다리" not in action


@pytest.mark.asyncio
async def test_a_gate_recorded_not_generated_slot_says_no_sweep_will_write_it(monkeypatch):
    item = _slot()
    _remember(monkeypatch, item, _kst(9, 16, 7, 45), "CONTENT_NOT_GENERATED", count_attempt=False)
    stored = tasks._stored_generation_attempt(item)
    assert stored["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert "next_retry_at" not in stored  # 어떤 스윕도 집지 않는다

    request, incident = await _open(monkeypatch, item, "CONTENT_NOT_GENERATED")

    assert request.next_action == CONTENT_NOT_GENERATED_OPERATOR_ACTION
    assert incident.state == IncidentState.OPEN.value
    assert incident.sla_due_at is None


@pytest.mark.asyncio
async def test_a_slot_a_sweep_still_owns_names_the_stored_next_attempt(monkeypatch):
    """주제 교체 뒤의 빈 슬롯 — 게이트가 기록을 보존하고, 스윕이 저장된 시각에 새 주제를 쓴다."""

    item = _slot()
    due = _kst(9, 16, 12).astimezone(UTC)
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "TOPIC_SWAPPED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "attempt_period": "2026-09-16",
            "provider_attempt_count": 0,
            "exhausted_days": 0,
            "next_retry_at": due.isoformat(),
        }
    }
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    request, _incident_row = await _open(monkeypatch, item, "CONTENT_NOT_GENERATED")

    assert request.next_action == (
        f"자동 복구가 {display_time(due)}에 이 글의 원고 생성을 다시 시도합니다. 그 전에는 “작업 다시 "
        "시도”를 눌러도 원고를 만들지 않으니, 그 뒤 운영 센터에서 결과를 확인하세요."
    )
    assert "09/16 12:00 KST" in request.next_action


# ── 3b 환경 예산 소진 ─────────────────────────────────────────────────────────


def test_the_exhausted_environment_action_tells_the_operator_what_to_do():
    action = ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION

    assert action == (
        "자동 재시도 횟수를 모두 사용해 예약된 자동 복구가 이 글의 원고를 더 만들지 않습니다. "
        "외부 서비스가 정상인지 확인한 뒤 운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요."
    )
    # #182 뒤에는 환경 실패 기록도 “작업 다시 시도”가 바로 푼다 — 게이트를 기다리라고 하지 않는다.
    assert "기다리" not in action and "07시 45분" not in action


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"])
async def test_an_exhausted_provider_failure_stops_saying_wait(monkeypatch, code):
    item = _slot()
    attempt = _exhaust_provider(monkeypatch, item, code)
    assert attempt["provider_attempt_count"] == generation_retry_policy.ENVIRONMENT_ATTEMPT_BUDGET
    assert attempt["next_retry_at"] is None
    assert generation_retry_policy.recovery_is_abandoned(attempt)

    request, incident = await _open(monkeypatch, item, code)

    assert request.next_action == ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION
    assert incident.state == IncidentState.OPEN.value
    assert requires_operator_action(incident.state, incident.sla_due_at, _kst(9, 16, 7, 1))


@pytest.mark.asyncio
async def test_a_provider_failure_with_budget_left_names_the_button_like_the_digest(monkeypatch):
    """요약은 '서비스가 복구됐으면 “작업 다시 시도”'라고 한다. 빈 슬롯의 환경 실패 기록은 누르면
    억제가 풀려 바로 다시 시도한다(#182) — 인시던트도 '기다리세요'가 아니라 같은 말을 한다."""

    item = _slot()
    for moment in (_kst(9, 15, 23), _kst(9, 16, 1)):
        attempt = _remember(monkeypatch, item, moment, "PROVIDER_TIMEOUT")
    assert isinstance(attempt["next_retry_at"], str)

    request, _incident_row = await _open(monkeypatch, item, "PROVIDER_TIMEOUT")

    assert request.next_action == (
        "일시적인 응답 지연입니다. 다음 예약 배치가 자동으로 다시 시도합니다. 서비스가 복구됐으면 운영 "
        "센터에서 해당 항목의 “작업 다시 시도”를 눌러 지금 바로 다시 시도할 수 있습니다."
    )
    assert "기다리세요" not in request.next_action
    assert "서비스가 복구됐으면" in blocker_copy("PROVIDER_TIMEOUT").action


@pytest.mark.asyncio
async def test_a_provider_failure_on_a_written_body_with_budget_left_still_says_wait(monkeypatch):
    """본문이 있는 글은 “작업 다시 시도”가 환경 실패 기록을 풀지 않는다 — 기다리라는 말이 참이다."""

    item = _slot(body="이미 쓴 본문")
    for moment in (_kst(9, 15, 23), _kst(9, 16, 1)):
        _remember(monkeypatch, item, moment, "PROVIDER_TIMEOUT")

    request, _incident_row = await _open(monkeypatch, item, "PROVIDER_TIMEOUT")

    assert request.next_action == generation_operator_action("PROVIDER_TIMEOUT")
    assert "지금은 기다리세요" in request.next_action


@pytest.mark.asyncio
async def test_an_exhausted_provider_failure_on_a_written_body_keeps_its_copy(monkeypatch):
    """본문이 있는 글은 저장 본문 수리 경로가 소유한다 — '원고를 만들지 않습니다'가 아니다."""

    item = _slot(body="이미 쓴 본문")
    _exhaust_provider(monkeypatch, item)

    request, _incident_row = await _open(monkeypatch, item, "PROVIDER_TIMEOUT")

    assert request.next_action == generation_operator_action("PROVIDER_TIMEOUT")


# ── 3c 재사용된 RETRYING 인시던트의 기한 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reused_retrying_cause_drops_its_stale_deadline_once_no_sweep_owns_it(
    monkeypatch,
):
    """앞선 표본 실패(RETRYING, 기한 12시)를 07:45 원고 미생성 관측이 재사용한다.

    게이트가 시도 기록을 CONTENT_NOT_GENERATED(OPERATOR_REQUIRED)로 남겼으므로 12시 스윕은
    이 슬롯을 집지 않는다. 옛 기한이 남으면 12시까지 '재시도 중'으로 숨는다.
    """

    item = _slot()
    stale = _kst(9, 16, 12).astimezone(UTC)
    cause = _incident("CONTENT_AI_HARD_FINDING", state=IncidentState.RETRYING.value, sla_due_at=stale)
    observed = _kst(9, 16, 7, 45)
    _remember(monkeypatch, item, observed, "CONTENT_NOT_GENERATED", count_attempt=False)

    request, _unused = await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert request is None  # 새 인시던트가 아니라 앞선 원인을 재사용했다
    assert cause.state == IncidentState.RETRYING.value
    assert cause.sla_due_at == observed.astimezone(UTC)
    assert requires_operator_action(cause.state, cause.sla_due_at, _kst(9, 16, 7, 46))
    # 사람이 할 일은 게이트 기록이 정한다 — 앞선 원인의 조치가 아니라 원고 미생성 조치다.
    assert cause.next_action == CONTENT_NOT_GENERATED_OPERATOR_ACTION


@pytest.mark.asyncio
async def test_a_reused_retrying_cause_takes_the_newer_attempts_deadline(monkeypatch):
    item = _slot()
    newer = _kst(9, 16, 18).astimezone(UTC)
    cause = _incident(
        "CONTENT_AI_HARD_FINDING",
        state=IncidentState.RETRYING.value,
        sla_due_at=_kst(9, 16, 1).astimezone(UTC),
    )
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "TOPIC_SWAPPED",
            "retry_class": GenerationRetryClass.SAMPLE_RECOVERABLE.value,
            "attempt_period": "2026-09-16",
            "provider_attempt_count": 0,
            "exhausted_days": 0,
            "next_retry_at": newer.isoformat(),
        }
    }
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert cause.state == IncidentState.RETRYING.value
    assert cause.sla_due_at == newer
    assert not requires_operator_action(cause.state, cause.sla_due_at, _kst(9, 16, 7, 46))
    assert cause.next_action == "앞선 원인의 조치"  # 스윕이 소유하는 동안 조치는 그대로다


# ── 4 독립 검수 HARD 지적 ─────────────────────────────────────────────────────


def test_the_hard_finding_action_names_real_controls_instead_of_closing_the_item():
    action = generation_operator_action("CONTENT_AI_HARD_FINDING")

    assert action == (
        "지적된 사실을 병원 정보 탭의 승인 자료에 채우세요. 승인 자료가 바뀌면 다음 자동 복구가 "
        "그 자료로 본문을 다시 씁니다. 지적이 사실과 다르면 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 "
        "눌러 지적된 내용을 고치거나, 그 내용을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 "
        "추가”로 넣어 저장하세요. 저장한 글은 독립 검수를 다시 받습니다."
    )
    for phrase in NONEXISTENT_ACTIONS:
        assert phrase not in action


# ── 8 표기 ─────────────────────────────────────────────────────────────────


# 이 PR(1·2차)이 쓰거나 고친 운영자 문구 전부. 앱 전체 표기 통일은 별도 PR이다.
_TOUCHED_ACTIONS = {
    "not_generated": CONTENT_NOT_GENERATED_OPERATOR_ACTION,
    "not_generated_scheduled": CONTENT_NOT_GENERATED_SCHEDULED_ACTION,
    "not_generated_releasable": CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION,
    "environment_exhausted": ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION,
    "provider_timeout_retry_now": PROVIDER_RETRY_NOW_ACTIONS["PROVIDER_TIMEOUT"],
    "provider_unavailable_retry_now": PROVIDER_RETRY_NOW_ACTIONS["PROVIDER_UNAVAILABLE"],
    "hard_finding": generation_operator_action("CONTENT_AI_HARD_FINDING"),
    "hard_finding_unwritten": CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION,
    "operator_decides": REFERENCES_OPERATOR_DECIDES_ACTION,
    "operator_decides_unwritten": REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION,
    "digest_provider": blocker_copy("PROVIDER_TIMEOUT").action,
    "digest_generation_failed": blocker_copy("GENERATION_FAILED").action,
    "digest_operator_decides": blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE).action,
    "digest_operator_decides_unwritten": blocker_copy(
        REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE
    ).action,
}


@pytest.mark.parametrize("name", sorted(_TOUCHED_ACTIONS))
def test_touched_actions_spell_the_operations_center_one_way(name):
    # #187 2차: 이 PR이 쓰거나 고친 문구는 '운영 센터'(띄어쓰기)로 쓴다.
    assert "운영센터" not in _TOUCHED_ACTIONS[name]


def test_touched_actions_that_name_the_operations_center_use_the_spaced_spelling():
    naming = {name for name, action in _TOUCHED_ACTIONS.items() if "운영 센터" in action}
    assert naming == {
        "not_generated",
        "not_generated_scheduled",
        "not_generated_releasable",
        "environment_exhausted",
        "provider_timeout_retry_now",
        "provider_unavailable_retry_now",
        "operator_decides_unwritten",
        "digest_provider",
        "digest_generation_failed",
    }


# ── 인용한 조작이 Admin에 실제로 있다 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "source"),
    [
        (CONTENT_NOT_GENERATED_OPERATOR_ACTION, _OPERATION_DETAIL),
        (CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION, _OPERATION_DETAIL),
        (ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION, _OPERATION_DETAIL),
        (blocker_copy("PROVIDER_TIMEOUT").action, _OPERATION_DETAIL),
        (blocker_copy("GENERATION_FAILED").action, _OPERATION_DETAIL),
        (PROVIDER_RETRY_NOW_ACTIONS["PROVIDER_TIMEOUT"], _OPERATION_DETAIL),
        (generation_operator_action("CONTENT_AI_HARD_FINDING"), _CONTENT_PAGE),
        (CONTENT_AI_HARD_FINDING_UNWRITTEN_ACTION, (_CONTENT_PAGE, _OPERATION_DETAIL)),
        (REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION, (_CONTENT_PAGE, _OPERATION_DETAIL)),
    ],
    ids=[
        "not_generated",
        "not_generated_releasable",
        "environment_exhausted",
        "provider_digest",
        "generation_failed_digest",
        "provider_retry_now",
        "hard_finding",
        "hard_finding_unwritten",
        "operator_decides_unwritten",
    ],
)
def test_every_quoted_control_exists_in_admin(action, source):
    sources = source if isinstance(source, tuple) else (source,)
    require_admin_source(sources, environ=os.environ)
    text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    quoted = re.findall(r"“([^”]+)”", action)
    assert quoted
    for label in quoted:
        assert label in text, f"Admin에 없는 “{label}”을(를) 말한다"


# ── 원고 없는 글의 사람 결정·HARD 지적(#187 2차 C) ─────────────────────────────
# 두 문구는 리뷰가 정한 그대로다(한 글자도 바꾸지 않는다). 새 인시던트와 재사용된 원인 양쪽에서
# 본문 유무로 고르고, 본문이 있는 글은 종전 문구를 그대로 쓴다.

C1_UNWRITTEN_OPERATOR_DECIDES = "아직 원고가 없는 글입니다. 운영 센터의 “작업 다시 시도”를 눌러도 이 글은 쓰이지 않습니다. 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 제목·본문을 새로 써서 저장해 주세요. 진료비·병원 선택 글로 쓰려면 주장을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 추가”로 함께 넣어야 하며, 병원 누리집과 검증된 문서 목록의 문서는 저장이 거절됩니다. 질환·검사 안내 글로 쓰면 참고 자료 없이 저장해도 자동 복구가 참고 자료를 찾습니다. 그대로 두면 생성 조건이 바뀌기 전에는 자동 복구가 이 글을 쓰지 않고, 참고 자료 없이는 발행되지 않습니다."
C2_HARD_FINDING_UNWRITTEN = "아직 원고가 없는 글입니다. “작업 다시 시도”나 승인 자료 추가만으로는 다시 쓰이지 않고, 운영 기준이 새로 승인되는 등 생성 조건이 바뀔 때 자동 복구가 다시 시도합니다. 지금 준비하려면 “콘텐츠 수정”으로 제목·본문을 직접 쓰고 근거 문서를 “참고 자료 추가”로 넣어 저장하세요. 저장한 글은 독립 검수를 다시 받습니다."
_WRITTEN_OPERATOR_DECIDES_ACTION = (
    "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러 둘 중 하나를 하세요. 글의 주장을 직접 "
    "뒷받침하는 공공·학술 기관 문서가 있으면 “참고 자료 추가”로 넣고 저장합니다. 없으면 "
    "제목·본문을 질환·검사 안내 글로 고쳐 저장합니다 — 다음 발행 확인이 검증된 문서로 참고 "
    "자료를 채울 수 있습니다. 병원 누리집과 검증된 문서 목록의 질환 문서는 이 글의 참고 자료가 "
    "될 수 없고, 참고 자료 없이는 발행되지 않습니다. 질환·검사 안내 글로 고쳐 참고 자료 없이 "
    "저장하면 자동 복구가 참고 자료를 찾으며 본문을 다시 쓸 수 있습니다. 진료비·병원 선택 글로 "
    "두면 자동 복구는 운영 기준이 새로 승인되는 등 생성 조건이 바뀌기 전에는 이 글을 다시 쓰지 "
    "않습니다."
)
_WRITTEN_HARD_ACTION = (
    "지적된 사실을 병원 정보 탭의 승인 자료에 채우세요. 승인 자료가 바뀌면 다음 자동 복구가 "
    "그 자료로 본문을 다시 씁니다. 지적이 사실과 다르면 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 "
    "눌러 지적된 내용을 고치거나, 그 내용을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 "
    "추가”로 넣어 저장하세요. 저장한 글은 독립 검수를 다시 받습니다."
)
_COST_TITLE = "치질 수술 비용 — 보험 적용과 본인부담"


def _operator_decides_record() -> dict:
    return {
        "reason": "MISSING_REFERENCES",
        "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
        "operator_decides": True,
        "attempt_period": "2026-09-16",
        "next_retry_at": None,
    }


def _hard_record(retry_class: GenerationRetryClass) -> dict:
    return {
        "reason": "CONTENT_AI_HARD_FINDING",
        "retry_class": retry_class.value,
        "attempt_period": "2026-09-16",
        "provider_attempt_count": 2,
        "exhausted_days": 0,
        "next_retry_at": None,
    }


def _written_cost_post():
    item = _slot(body="이미 쓴 진료비 본문")
    item.title = _COST_TITLE
    item.content_type = SimpleNamespace(value="TREATMENT")
    item.content_brief = None
    item.faq_question = None
    return item


@pytest.mark.asyncio
async def test_an_unwritten_operator_decides_slot_gets_the_new_draft_action(monkeypatch):
    item = _slot()
    item.essence_check_summary = {"generation_attempt": _operator_decides_record()}
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    request, incident = await _open(monkeypatch, item, "MISSING_REFERENCES")

    assert request.next_action == C1_UNWRITTEN_OPERATOR_DECIDES
    assert incident.state == IncidentState.OPEN.value


@pytest.mark.asyncio
async def test_a_written_operator_decides_post_keeps_its_edit_action(monkeypatch):
    item = _written_cost_post()
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    request, _incident_row = await _open(monkeypatch, item, "MISSING_REFERENCES")

    assert request.next_action == _WRITTEN_OPERATOR_DECIDES_ACTION


@pytest.mark.asyncio
async def test_an_unwritten_hard_finding_gets_the_write_it_yourself_action(monkeypatch):
    item = _slot()
    item.essence_check_summary = {
        "generation_attempt": _hard_record(GenerationRetryClass.INPUT_CHANGE_REQUIRED)
    }
    _freeze(monkeypatch, _kst(9, 16, 7, 1))

    request, _incident_row = await _open(monkeypatch, item, "CONTENT_AI_HARD_FINDING")

    assert request.next_action == C2_HARD_FINDING_UNWRITTEN


@pytest.mark.asyncio
async def test_a_written_hard_finding_keeps_its_action(monkeypatch):
    item = _slot(body="이미 쓴 본문")
    _freeze(monkeypatch, _kst(9, 16, 7, 1))

    request, _incident_row = await _open(monkeypatch, item, "CONTENT_AI_HARD_FINDING")

    assert request.next_action == _WRITTEN_HARD_ACTION


@pytest.mark.asyncio
async def test_a_reused_unwritten_operator_decides_cause_gets_the_new_draft_action(monkeypatch):
    """원고 미생성 관측이 사람 결정 보류(종착)를 재사용한다 — 조치는 원고 없는 글의 문구다."""

    item = _slot()
    item.essence_check_summary = {"generation_attempt": _operator_decides_record()}
    cause = _incident("MISSING_REFERENCES", state=IncidentState.OPEN.value)
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    request, _unused = await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert request is None  # 앞선 원인을 재사용했다
    assert cause.next_action == C1_UNWRITTEN_OPERATOR_DECIDES


@pytest.mark.asyncio
async def test_a_reused_terminal_unwritten_hard_cause_gets_the_write_it_yourself_action(
    monkeypatch,
):
    item = _slot()
    item.essence_check_summary = {
        "generation_attempt": _hard_record(GenerationRetryClass.INPUT_CHANGE_REQUIRED)
    }
    cause = _incident("CONTENT_AI_HARD_FINDING", state=IncidentState.OPEN.value)
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert cause.next_action == C2_HARD_FINDING_UNWRITTEN


@pytest.mark.asyncio
async def test_a_reused_abandoned_unwritten_hard_cause_gets_the_write_it_yourself_action(
    monkeypatch,
):
    """표본 기록이지만 어떤 스윕도 집지 않는(다음 시도 시각 None) HARD — 재사용 갱신 경로다."""

    item = _slot()
    item.essence_check_summary = {
        "generation_attempt": _hard_record(GenerationRetryClass.SAMPLE_RECOVERABLE)
    }
    cause = _incident("CONTENT_AI_HARD_FINDING", state=IncidentState.OPEN.value)
    observed = _kst(9, 16, 7, 45)
    _freeze(monkeypatch, observed)
    assert not generation_incident_control.generation_block_is_terminal(
        "CONTENT_AI_HARD_FINDING", item
    )
    assert not generation_incident_control.scheduled_recovery_owns_blocker(
        "CONTENT_AI_HARD_FINDING", item
    )

    await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert cause.next_action == C2_HARD_FINDING_UNWRITTEN


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["operator_decides", "hard"])
async def test_a_reused_cause_on_a_written_body_keeps_its_action(monkeypatch, record):
    item = _written_cost_post() if record == "operator_decides" else _slot(body="이미 쓴 본문")
    item.essence_check_summary = {
        "generation_attempt": (
            _operator_decides_record()
            if record == "operator_decides"
            else _hard_record(GenerationRetryClass.SAMPLE_RECOVERABLE)
        )
    }
    code = "MISSING_REFERENCES" if record == "operator_decides" else "CONTENT_AI_HARD_FINDING"
    cause = _incident(code, state=IncidentState.OPEN.value)
    _freeze(monkeypatch, _kst(9, 16, 7, 45))

    await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert cause.next_action == "앞선 원인의 조치"


# ── 재사용된 OPEN 원인의 기한(#187 2차 E, 리뷰 뮤턴트 y18) ─────────────────────


@pytest.mark.asyncio
async def test_a_reused_open_cause_keeps_its_deadline(monkeypatch):
    """기한 갱신은 RETRYING만이다. OPEN(사람의 일)의 기한을 관측 시각이나 기록 기한으로 덮지 않는다."""

    item = _slot()
    kept = _kst(9, 16, 12).astimezone(UTC)
    cause = _incident("CONTENT_AI_HARD_FINDING", state=IncidentState.OPEN.value, sla_due_at=kept)
    observed = _kst(9, 16, 7, 45)
    _remember(monkeypatch, item, observed, "CONTENT_NOT_GENERATED", count_attempt=False)

    await _open(monkeypatch, item, "CONTENT_NOT_GENERATED", scalars=(None, cause))

    assert cause.state == IncidentState.OPEN.value
    assert cause.sla_due_at == kept


# ── 기한이 지난 다음 시도 시각(#187 2차 B) ────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "announced"),
    [
        (_kst(9, 16, 6, 59), _kst(9, 16, 7)),  # 기한 전 — 저장된 시각
        (_kst(9, 16, 7), _kst(9, 16, 12)),  # 기한 그 시각 — 이미 약속이 아니다(다음 스윕)
        (_kst(9, 16, 7, 45), _kst(9, 16, 12)),
        (_kst(9, 16, 22, 30), _kst(9, 16, 23)),
    ],
    ids=["before", "at", "0745", "2230"],
)
def test_the_announced_recovery_time_never_is_a_past_time(now, announced):
    item = _slot()
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "PROVIDER_TIMEOUT",
            "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
            "next_retry_at": _kst(9, 16, 7).astimezone(UTC).isoformat(),
        }
    }

    shown = generation_incident_control.announced_recovery_time(item, now.astimezone(UTC))

    assert shown == announced
    assert shown > now
