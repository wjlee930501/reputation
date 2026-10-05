"""이미지 때문에만 막힌 글은 시간별 발행기가 이미지 재생성을 스스로 한 번 건다(PR-B 1).

게이트 순서상 `CONTENT_IMAGE_NOT_READY`·`CONTENT_IMAGE_NOT_VERIFIED`는 본문·참고자료·금지 표현·
독립 검수·운영 기준 검사를 모두 통과한 뒤에만 나온다. 그런데도 사람이 Admin의 “이미지 다시 만들기”를
누르기 전까지 글이 며칠씩 서 있었다(2026-10-03 사고). 발행기는 같은 일을 하는 기존 태스크
(`generate_content_image`)를 시스템 소유 실행(REGENERATE_CONTENT_IMAGE OperationRun, 요청자 없음)과
서명된 봉투로 `content` 큐에 넣는다 — 실행 기록·계수를 차단 기록과 함께 커밋한 뒤에만, 글당 KST
하루 1회·누적 3회까지(`AUTO_IMAGE_REGEN_DAILY_CAP`·`AUTO_IMAGE_REGEN_TOTAL_CAP`). 계수는
`essence_check_summary["auto_image_regeneration"]`에 남고 게이트 기록이 지우지 않는다. 실행의 멱등
키는 글·KST 날짜(`auto-image-regen:<글>:<날짜>`)라 같은 날 두 번 배포되지 않는다.

이미지 태스크는 언제나 OperationRun 아래에서 돈다(Worker 인증 불변식은 그대로다). 발행하지 않는다 —
발행은 다음 시간별 발행기가 바뀌지 않은 게이트로만 한다. 검수가 막은 글·claim이 살아 있는 행·보류된
병원·오늘 이미지 예산을 다 쓴 글·비용 가드·종착 이미지 원인은 아무것도 사지 않는다. 한도를 넘으면
종전의 예산·재사용·인시던트 흐름이 그대로 소유한다.

네트워크·DB·공급자는 쓰지 않는다 — 가짜 세션·가짜 fetcher·가짜 apply_async만 쓴다.
"""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services.content_publication import (
    PublicationAssessment,
    apply_publication_assessment,
    assess_content_publication,
)
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.services.reference_verification import ReferenceVerifier
from app.workers import dispatch_auth, tasks
from app.workers.dispatch_envelope import PURPOSE_HEADER, TARGET_HEADER
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.reference_fetch_doubles import PageFetcher
from tests.test_publisher_live_claim import (
    _claim,
    _complete_row,
    _kst,
    _publisher,
    _run_publisher,
    _set_clock,
    _single_publish,
    _textual_row_without_image,
)
from tests.test_tasks_nightly import _approved_philosophy, _publication_hospital

SLOT = date(2026, 6, 10)  # `_publication_item`의 예정일
SUMMARY_KEY = "auto_image_regeneration"
IMAGE_TASK = "app.workers.tasks.generate_content_image"
IMAGE_PURPOSE = "generate-content-image"
IMAGE_OPERATION = "REGENERATE_CONTENT_IMAGE"


def _cap(name: str) -> int:
    value = getattr(tasks, name, None)
    assert value is not None, f"tasks.{name}이(가) 없다 — 자동 이미지 재생성 한도 상수"
    return value


@pytest.fixture(autouse=True)
def dispatches(monkeypatch):
    """`generate_content_image.apply_async` 호출 기록. 실제 브로커에는 아무것도 넣지 않는다."""

    calls: list[dict] = []

    def record(*args, **kwargs):
        if args:
            kwargs.setdefault("args", args[0])
        calls.append(kwargs)
        return None

    monkeypatch.setattr(tasks.generate_content_image, "apply_async", record)
    return calls


@pytest.fixture(autouse=True)
def operation_runs() -> list:
    """이 테스트에서 커밋된 실행 기록 — 멱등 키 유일 인덱스(`uq_operation_runs_idempotency_scope`)를 흉내 낸다."""

    return []


class _Savepoint:
    def __init__(self, db, registry):
        self.db = db
        self.registry = registry
        self.mark = len(db.added)

    def commit(self):
        for row in self.db.added[self.mark :]:
            if not isinstance(row, OperationRun):
                continue
            scope = (row.requested_by_id, row.hospital_id, row.operation_type, row.idempotency_key)
            if any(
                (seen.requested_by_id, seen.hospital_id, seen.operation_type, seen.idempotency_key)
                == scope
                for seen in self.registry
            ):
                raise IntegrityError("INSERT operation_runs", {}, Exception("duplicate key"))
            self.registry.append(row)

    def rollback(self):
        del self.db.added[self.mark :]


def _hourly_publisher(monkeypatch, operation_runs, *items):
    """08:00~23:00 발행기 더블에 savepoint를 더한다 — 실제 세션처럼 실행 기록을 넣을 수 있다."""

    db, incidents, effects = _publisher(monkeypatch, *items)
    db.begin_nested = lambda: _Savepoint(db, operation_runs)
    return db, incidents, effects


def _run_hourly(monkeypatch, operation_runs, moment, *items):
    db, incidents, effects = _hourly_publisher(monkeypatch, operation_runs, *items)
    _set_clock(monkeypatch, moment)
    _run_publisher()
    return db, incidents, effects


def _dispatched_ids(calls) -> list[str]:
    return [str(list(call.get("args") or [])[0]) for call in calls]


def _image_missing(hospital=None):
    """본문·참고자료·검수가 모두 통과하고 대표 이미지만 없는 글 — 게이트는 CONTENT_IMAGE_NOT_READY다."""

    return _textual_row_without_image(hospital or _publication_hospital())


def _image_unverified(hospital=None):
    """이미지는 있지만 정책 인증이 없는 글 — 게이트는 CONTENT_IMAGE_NOT_VERIFIED다."""

    item = _complete_row(hospital or _publication_hospital())
    item.image_policy_verified_at = None
    item.essence_check_summary = {"automatic_remediation_attempts": 0}
    return item


def _stored_attempt(item, reason, *, count, period="2026-06-10"):
    summary = dict(item.essence_check_summary or {})
    summary[GENERATION_ATTEMPT_KEY] = {
        "context": tasks._generation_attempt_context(item, _approved_philosophy()),
        "reason": reason,
        "retry_class": tasks.retry_class_for(reason).value,
        "attempt_period": period,
        "observed_at": f"{period}T00:00:00+09:00",
        "provider_attempt_count": count,
        "attempt_count": count,
        "exhausted_days": 0,
    }
    item.essence_check_summary = summary


# ── 정상 경로: 이미지 증상 두 가지가 각각 한 번의 시스템 실행을 건다 ───────────────────


@pytest.mark.parametrize(
    ("shape", "gate_code"),
    [(_image_missing, "CONTENT_IMAGE_NOT_READY"), (_image_unverified, "CONTENT_IMAGE_NOT_VERIFIED")],
)
def test_an_image_only_block_dispatches_one_system_owned_image_run(
    monkeypatch, dispatches, operation_runs, shape, gate_code
):
    item = shape()
    assert assess_content_publication(item, _approved_philosophy()).code == gate_code

    _db, incidents, effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert _dispatched_ids(dispatches) == [str(item.id)]
    call = dispatches[0]
    assert call.get("queue") == "content"
    # 실행 기록은 Admin 경로와 같은 종류·같은 저장 payload다. 요청자는 없다(시스템 실행).
    assert len(operation_runs) == 1
    run = operation_runs[0]
    assert run.operation_type == IMAGE_OPERATION
    assert run.state == OperationRunState.REQUESTED
    assert run.requested_by_id is None
    assert run.hospital_id == item.hospital_id
    assert run.idempotency_key == f"auto-image-regen:{item.id}:2026-06-10"
    assert run.task_id and call.get("task_id") == run.task_id
    assert run.request_payload == build_request_payload(
        DispatchPayload("content_item", str(item.id), "content", (str(item.id),))
    )
    headers = call.get("headers") or {}
    assert headers.get(PURPOSE_HEADER) == IMAGE_PURPOSE
    assert headers.get(TARGET_HEADER) == str(item.id)
    assert headers.get("operation_run_id") == str(run.id)
    # 발행은 하지 않는다. 종전 인시던트 흐름도 그대로다.
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert [call["code"] for call in incidents] == [gate_code]
    assert effects == {"revalidate": [], "indexnow": []}


def test_the_run_and_the_trigger_are_committed_before_it_is_enqueued(
    monkeypatch, dispatches, operation_runs
):
    """계수는 배포를 큐에 넣을 때 쓴다. 큐에 넣는 시점에는 실행 기록과 계수가 이미 커밋돼 있어야 한다."""

    item = _image_missing()
    db, _incidents, _effects = _hourly_publisher(monkeypatch, operation_runs, item)
    _set_clock(monkeypatch, _kst(8))
    committed: list[tuple[dict, int]] = []
    real_commit = db.commit

    def snapshotting_commit():
        runs = sum(isinstance(row, OperationRun) for row in db.added)
        committed.append((copy.deepcopy(item.essence_check_summary), runs))
        real_commit()

    db.commit = snapshotting_commit
    seen_at_dispatch: list[tuple[dict, int] | None] = []
    record = tasks.generate_content_image.apply_async

    def dispatch_after_commit(*args, **kwargs):
        seen_at_dispatch.append(copy.deepcopy(committed[-1]) if committed else None)
        return record(*args, **kwargs)

    monkeypatch.setattr(tasks.generate_content_image, "apply_async", dispatch_after_commit)

    _run_publisher()

    assert len(dispatches) == 1
    assert seen_at_dispatch and seen_at_dispatch[0] is not None
    summary, committed_runs = seen_at_dispatch[0]
    assert committed_runs == 1, "큐에 넣기 전에 실행 기록이 커밋되지 않았다"
    spent = summary.get(SUMMARY_KEY)
    assert spent is not None, "큐에 넣기 전에 계수가 커밋되지 않았다"
    assert (spent["period"], spent["count"], spent["total"]) == ("2026-06-10", 1, 1)
    stored = item.essence_check_summary[SUMMARY_KEY]
    assert (stored["period"], stored["count"], stored["total"]) == ("2026-06-10", 1, 1)
    datetime.fromisoformat(stored["last_triggered_at"])


def test_a_stored_image_failure_with_budget_left_still_triggers(
    monkeypatch, dispatches, operation_runs
):
    """보고 코드는 저장된 원인(IMAGE_GENERATION_FAILED)이 되지만 게이트 판정은 이미지 증상이다.
    오늘 이미지 예산이 남아 있으면 재생성을 건다."""

    item = _image_missing()
    _stored_attempt(item, "IMAGE_GENERATION_FAILED", count=1)

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert [call["code"] for call in incidents] == ["IMAGE_GENERATION_FAILED"]
    assert _dispatched_ids(dispatches) == [str(item.id)]


def test_a_broker_failure_keeps_the_committed_run_for_recovery(
    monkeypatch, dispatches, operation_runs
):
    """배포가 실패해도 차단 기록은 그대로이고, 커밋된 REQUESTED 실행을 자율 복구가 다시 배포한다."""

    item = _image_missing()

    def broker_down(*_args, **_kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.generate_content_image, "apply_async", broker_down)

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert [call["code"] for call in incidents] == ["CONTENT_IMAGE_NOT_READY"]
    assert [run.state for run in operation_runs] == [OperationRunState.REQUESTED]
    assert item.essence_check_summary[SUMMARY_KEY]["total"] == 1


# ── 한도: 하루 1회, 누적 3회 ───────────────────────────────────────────────────────


def test_the_caps_are_one_a_day_and_three_in_total():
    assert _cap("AUTO_IMAGE_REGEN_DAILY_CAP") == 1
    assert _cap("AUTO_IMAGE_REGEN_TOTAL_CAP") == 3


def test_the_daily_cap_allows_one_trigger_per_kst_day(monkeypatch, dispatches, operation_runs):
    """08시에 한 번 걸었다. 이미지가 아직 없어도 같은 KST 날의 09·10·23시는 다시 사지 않는다.
    매시 게이트 기록(`apply_publication_assessment`)이 계수를 지우면 매시 다시 산다."""

    item = _image_missing()
    for hour in (8, 9, 10, 23):
        _run_hourly(monkeypatch, operation_runs, _kst(hour), item)

    assert _dispatched_ids(dispatches) == [str(item.id)]
    assert len(operation_runs) == 1
    stored = item.essence_check_summary[SUMMARY_KEY]
    assert (stored["period"], stored["count"], stored["total"]) == ("2026-06-10", 1, 1)


def test_the_same_day_key_blocks_a_second_dispatch_even_if_the_counter_was_lost(
    monkeypatch, dispatches, operation_runs
):
    """계수가 다른 쓰기(오래된 JSON을 다시 쓰는 워커 등)에 지워져도 같은 KST 날의 멱등 키가 두 번째
    배포를 막는다. 차단 기록·인시던트는 종전대로 남는다."""

    item = _image_missing()
    _run_hourly(monkeypatch, operation_runs, _kst(8), item)
    summary = dict(item.essence_check_summary)
    summary.pop(SUMMARY_KEY)
    item.essence_check_summary = summary

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(9), item)

    assert _dispatched_ids(dispatches) == [str(item.id)]
    assert len(operation_runs) == 1
    assert [call["code"] for call in incidents] == ["CONTENT_IMAGE_NOT_READY"]
    assert SUMMARY_KEY not in item.essence_check_summary  # 배포하지 않은 시도는 계수를 쓰지 않는다


def test_a_new_kst_day_allows_the_next_trigger(monkeypatch, dispatches, operation_runs):
    item = _image_missing()
    _run_hourly(monkeypatch, operation_runs, _kst(23), item)
    # 자정 직후 — 새 KST 날이다(UTC로는 아직 전날 15시다).
    _run_hourly(monkeypatch, operation_runs, _kst(8, day=SLOT + timedelta(days=1)), item)

    assert _dispatched_ids(dispatches) == [str(item.id), str(item.id)]
    assert [run.idempotency_key for run in operation_runs] == [
        f"auto-image-regen:{item.id}:2026-06-10",
        f"auto-image-regen:{item.id}:2026-06-11",
    ]
    stored = item.essence_check_summary[SUMMARY_KEY]
    assert (stored["period"], stored["count"], stored["total"]) == ("2026-06-11", 1, 2)


def test_the_total_cap_stops_at_exactly_three_and_the_block_stays_with_the_incident_flow(
    monkeypatch, dispatches, operation_runs
):
    item = _image_missing()
    incidents_per_day: list[list[str]] = []
    for offset in range(5):
        _db, incidents, _effects = _run_hourly(
            monkeypatch, operation_runs, _kst(8, day=SLOT + timedelta(days=offset)), item
        )
        incidents_per_day.append([call["code"] for call in incidents])

    # 1·2·3일째는 걸고(정확히 한도), 4·5일째(한도+1)는 걸지 않는다.
    assert _dispatched_ids(dispatches) == [str(item.id)] * 3
    assert len(operation_runs) == 3
    stored = item.essence_check_summary[SUMMARY_KEY]
    assert stored["total"] == 3
    assert stored["period"] == "2026-06-12"  # 마지막으로 건 날 — 한도 뒤에는 계수를 쓰지 않는다
    # 한도 뒤에도 글은 그대로 막혀 있고 종전 인시던트 흐름이 매번 그 차단을 소유한다.
    assert incidents_per_day == [["CONTENT_IMAGE_NOT_READY"]] * 5
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None


def test_the_counter_survives_the_hourly_gate_record():
    """게이트 기록은 허용 목록의 열쇠만 남긴다 — 이 계수도 그 목록에 있어야 한다."""

    item = _image_missing()
    counter = {"period": "2026-06-10", "count": 1, "total": 2, "last_triggered_at": "2026-06-10T08:00:00+09:00"}
    item.essence_check_summary = {"automatic_remediation_attempts": 0, SUMMARY_KEY: dict(counter)}

    assessment = assess_content_publication(item, _approved_philosophy())
    apply_publication_assessment(item, assessment)

    assert item.essence_check_summary.get(SUMMARY_KEY) == counter


# ── 걸지 않는 경우: 조건 중 하나라도 어긋나면 아무것도 사지 않는다 ──────────────────────


def test_a_review_blocked_post_without_an_image_buys_no_image_and_is_not_published(
    monkeypatch, dispatches, operation_runs
):
    """(PR-B 4b) 독립 검수가 막은 본문에는 이미지를 사지 않는다 — 게이트는 검수 코드를 먼저 낸다."""

    item = _image_missing()
    item.essence_check_summary = {
        "automatic_remediation_attempts": 0,
        "ai_review": {"status": "UNAVAILABLE", "unavailable_reason": "INVALID_RESPONSE"},
    }

    _db, incidents, effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert [call["code"] for call in incidents] == ["CONTENT_AI_REVIEW_UNAVAILABLE"]
    assert dispatches == [] and operation_runs == []
    assert SUMMARY_KEY not in item.essence_check_summary
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert effects == {"revalidate": [], "indexnow": []}


def test_an_image_code_over_a_blocking_stored_review_still_buys_nothing(
    monkeypatch, dispatches, operation_runs
):
    """방어선: 판정이 이미지 코드를 내더라도 저장된 검수가 막고 있으면 사지 않는다."""

    item = _image_missing()
    item.essence_check_summary = {
        "automatic_remediation_attempts": 0,
        "ai_review": {
            "status": "REVISE",
            "blocking": True,
            "schema_version": 2,
            "findings": [{"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY", "message": "확인 필요"}],
        },
    }
    real_assess = tasks.assess_content_publication

    def image_code_only(candidate, philosophy):
        assessment = real_assess(candidate, philosophy)
        assert assessment.code in {"CONTENT_AI_REVIEW_STALE", "CONTENT_AI_HARD_FINDING"}
        return PublicationAssessment(
            publishable=False,
            code="CONTENT_IMAGE_NOT_READY",
            message="대표 이미지가 아직 준비되지 않았습니다.",
            violations=(),
            essence_status=assessment.essence_status,
            essence_summary=assessment.essence_summary,
            philosophy_id=assessment.philosophy_id,
        )

    _hourly_publisher(monkeypatch, operation_runs, item)
    monkeypatch.setattr(tasks, "assess_content_publication", image_code_only)
    _set_clock(monkeypatch, _kst(8))
    _run_publisher()

    assert dispatches == [] and operation_runs == []
    assert item.status is tasks.ContentStatus.DRAFT


def test_a_live_generation_claim_is_never_given_an_image_job(
    monkeypatch, dispatches, operation_runs
):
    """23시 마지막 발행기는 claim 행을 분리된 사본으로 판정·보고한다. 워커가 쓰는 중인 행에 이미지
    태스크를 얹지 않는다(그 태스크도 같은 lease를 잡으려다 물러날 뿐이다)."""

    item = _image_missing()
    _claim(item, _kst(22, 30))

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(23), item)

    assert [call["code"] for call in incidents] == ["CONTENT_IMAGE_NOT_READY"]
    assert dispatches == [] and operation_runs == []
    assert SUMMARY_KEY not in (item.essence_check_summary or {})


def test_a_held_hospital_gets_no_image_job(monkeypatch, dispatches, operation_runs):
    item = _image_missing()
    monkeypatch.setattr(
        tasks, "auto_publish_hold", lambda: type("Hold", (), {"holds": lambda _s, _h: True})()
    )

    _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert dispatches == [] and operation_runs == []


def test_a_hospital_off_the_public_site_gets_no_image_job(monkeypatch, dispatches, operation_runs):
    item = _image_missing()
    item.hospital.site_live = False

    _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert dispatches == [] and operation_runs == []


def test_todays_spent_image_budget_is_not_bypassed(monkeypatch, dispatches, operation_runs):
    item = _image_missing()
    _stored_attempt(
        item, "IMAGE_GENERATION_FAILED", count=tasks.SAMPLE_IMAGE_DAILY_BUDGET
    )
    assert tasks._image_attempts_exhausted_today(item) is False  # 시계를 얼리기 전(실제 오늘)

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert tasks._image_attempts_exhausted_today(item) is True
    assert dispatches == [] and operation_runs == []
    assert [call["code"] for call in incidents] == ["IMAGE_GENERATION_FAILED"]


@pytest.mark.parametrize(
    "reason",
    ["COST_BLOCKED", "IMAGE_GENERATION_RETRIES_EXHAUSTED", "CONTENT_IMAGE_POLICY_REJECTED"],
)
def test_cost_guard_and_terminal_image_causes_buy_nothing(
    monkeypatch, dispatches, operation_runs, reason
):
    item = _image_missing()
    _stored_attempt(item, reason, count=1)

    _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert dispatches == [] and operation_runs == []
    assert SUMMARY_KEY not in item.essence_check_summary


def test_a_text_block_buys_no_image(monkeypatch, dispatches, operation_runs):
    """이미지보다 앞선 차단(금지 표현)은 이미지와 무관하다 — 이미지가 없어도 사지 않는다."""

    item = _image_missing()
    item.body = "이 수술은 완치를 약속드립니다."

    _db, incidents, _effects = _run_hourly(monkeypatch, operation_runs, _kst(8), item)

    assert [call["code"] for call in incidents] == ["FORBIDDEN_EXPRESSION"]
    assert dispatches == [] and operation_runs == []


# ── 이미지 태스크는 발행하지 않는다(PR-B 4a) — 발행은 다음 발행기의 게이트가 한다 ──────────


class _ImageTaskDB:
    def __init__(self, item, hospital):
        self.item = item
        self.hospital = hospital
        self.added: list = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, model, key):
        if model is ContentItem:
            return self.item if key == self.item.id else None
        if model is Hospital:
            return self.hospital
        raise AssertionError(f"예상하지 못한 조회 대상: {model}")

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def refresh(self, _value):
        return None

    def execute(self, _stmt):  # pragma: no cover — 이 태스크 경로는 조회문을 쓰지 않는다
        raise AssertionError("unexpected statement")


def _arm_image_task(monkeypatch, item, *, image_ok=True):
    hospital = item.hospital
    db = _ImageTaskDB(item, hospital)
    philosophy = _approved_philosophy()
    token = uuid.uuid4()
    provider_calls: list[str] = []
    image_hash = "d" * 64

    async def provider(*_args, **kwargs):
        provider_calls.append(kwargs.get("topic"))
        if not image_ok:
            return None, None
        return f"gs://reputation-images/content/{image_hash}-content.png", "prompt"

    def write_back(_db, *, item_id, values, **_guards):
        assert item_id == item.id
        for key, value in values.items():
            setattr(item, key, value)
        return 1

    async def no_incident(**_kwargs):
        raise AssertionError("실행 문맥이 없는 직접 호출은 인시던트를 열지 않는다")

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "claim_generation_lease", lambda _db, _id: (item, token))
    monkeypatch.setattr(tasks, "release_generation_claim", lambda *_a, **_k: 1)
    monkeypatch.setattr(tasks, "write_back_generated_image", write_back)
    monkeypatch.setattr(tasks, "generate_image", provider)
    monkeypatch.setattr(tasks, "hospital_image_direction", lambda *_a: None)
    monkeypatch.setattr(tasks, "open_generation_incident", no_incident)
    return db, provider_calls


def test_the_image_task_never_publishes_and_the_next_publisher_publishes_through_the_gate(
    monkeypatch,
):
    item = _image_missing()
    _set_clock(monkeypatch, _kst(8, 5))
    db, provider_calls = _arm_image_task(monkeypatch, item)

    tasks.generate_content_image.run(str(item.id))

    assert provider_calls == [item.title]
    assert tasks.image_certification_current(item)
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert not [row for row in db.added if getattr(row, "action", None)]
    # 발행은 다음 시간별 발행기가 바뀌지 않은 게이트를 거쳐서만 한다.
    _single_publish(monkeypatch, item, item.hospital)
    _set_clock(monkeypatch, _kst(9))
    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher()))
    assert payload is not None and payload["kind"] == "published"
    assert item.status is tasks.ContentStatus.PUBLISHED


def test_a_failed_image_job_records_the_cause_without_forging_an_admin_audit(monkeypatch):
    """공급자가 URL 없이 끝나도 태스크는 깨지지 않고, 원인만 정본 시도 기록에 남긴다(예산 사다리).
    Admin 감사 기록을 만들어 내지 않는다."""

    item = _image_missing()
    _set_clock(monkeypatch, _kst(8, 5))
    db, provider_calls = _arm_image_task(monkeypatch, item, image_ok=False)

    tasks.generate_content_image.run(str(item.id))

    assert provider_calls == [item.title]
    assert item.status is tasks.ContentStatus.DRAFT and item.image_url is None
    attempt = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["reason"] == "IMAGE_GENERATION_FAILED"
    assert attempt["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert db.added == []


# ── 서명된 시스템 실행은 운영 Worker의 인증을 통과하고, 인증 불변식은 그대로다 ──────────────


def _production_dispatch(monkeypatch, run=None):
    monkeypatch.setattr(dispatch_auth.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        dispatch_auth.settings, "WORKER_DISPATCH_SECRET", "worker-only-secret-32-bytes-minimum"
    )
    monkeypatch.setattr(dispatch_auth.settings, "REPUTATION_RELEASE_REVISION", "release-a")
    monkeypatch.setattr(dispatch_auth.time, "time", lambda: 1_700_000_001)

    class _RunSession:
        def __enter__(self):
            return type(
                "DB",
                (),
                {"get": lambda _s, _model, key: run if run is not None and key == run.id else None},
            )()

        def __exit__(self, *_exc):
            return False

    from app.core import database

    monkeypatch.setattr(database, "SyncSessionLocal", _RunSession)


def _worker_task(headers, task_id="image-task-id", claim_version=None):
    request = type("Req", (), {"id": task_id, "retries": 0, "headers": headers})()
    if claim_version is not None:
        request.operation_run_claim_version = claim_version
    return type("Task", (), {"name": IMAGE_TASK, "request": request})()


def test_the_publisher_dispatch_passes_production_worker_authorization(
    monkeypatch, dispatches, operation_runs
):
    """발행기가 남긴 실행을 Worker가 claim(task_prerun)한 뒤의 상태로 인증한다 — Admin 실행과 같은 검사다."""

    item = _image_missing()
    _run_hourly(monkeypatch, operation_runs, _kst(8), item)
    assert len(dispatches) == 1 and len(operation_runs) == 1
    call, run = dispatches[0], operation_runs[0]
    args = list(call.get("args") or [])
    kwargs = dict(call.get("kwargs") or {})
    task_id = call["task_id"]
    # `operation_run_signals.track_operation_prerun`의 claim을 재현한다.
    run.state = OperationRunState.RUNNING
    run.lease_owner = task_id
    run.version = 3
    _production_dispatch(monkeypatch, run)
    # before_task_publish가 하는 서명을 그대로 재현한다.
    stamped = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id=task_id,
        args=args,
        kwargs=kwargs,
        retries=0,
        headers=call.get("headers") or {},
        now=1_700_000_000,
    )

    dispatch_auth.AuthenticatedTask.before_start(
        _worker_task(stamped, task_id=task_id, claim_version=3), task_id, tuple(args), kwargs
    )


def test_a_signed_envelope_without_a_run_is_still_rejected(monkeypatch):
    """불변식: 이미지 태스크는 언제나 OperationRun 아래에서 돈다 — 서명만 있는 봉투는 거절한다."""

    _production_dispatch(monkeypatch)
    content_id = str(uuid.uuid4())
    stamped = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="image-task-id",
        args=[content_id],
        kwargs={},
        retries=0,
        headers=dispatch_auth.build_dispatch_headers(IMAGE_PURPOSE, content_id),
        now=1_700_000_000,
    )

    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.AuthenticatedTask.before_start(
            _worker_task(stamped), "image-task-id", (content_id,), {}
        )


def test_an_envelope_naming_a_run_that_does_not_authorize_it_is_still_rejected(monkeypatch):
    """보존: `operation_run_id`를 단 봉투는 종전처럼 그 실행이 허가해야만 돈다."""

    _production_dispatch(monkeypatch)
    content_id = str(uuid.uuid4())
    stamped = dispatch_auth.stamp_dispatch_headers(
        task_name=IMAGE_TASK,
        task_id="image-task-id",
        args=[content_id],
        kwargs={},
        retries=0,
        headers={"operation_run_id": str(uuid.uuid4())},
        now=1_700_000_000,
    )

    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.AuthenticatedTask.before_start(
            _worker_task(stamped, claim_version=1), "image-task-id", (content_id,), {}
        )


def test_an_unsigned_system_envelope_is_still_rejected(monkeypatch):
    _production_dispatch(monkeypatch)
    content_id = str(uuid.uuid4())

    with pytest.raises(dispatch_auth.DispatchAuthorizationError):
        dispatch_auth.AuthenticatedTask.before_start(
            _worker_task(
                {PURPOSE_HEADER: IMAGE_PURPOSE, TARGET_HEADER: content_id}
            ),
            "image-task-id",
            (content_id,),
            {},
        )


# ── Admin “이미지 다시 만들기”는 바이트 그대로다 ──────────────────────────────────────


async def test_the_admin_regenerate_image_endpoint_is_unchanged(monkeypatch):
    from app.api.admin import operations as operations_api
    from app.models.audit import AdminAuditLog
    from tests.test_admin_operations import FakeDB, FakeTask, _content, _hospital

    hospital = _hospital()
    content = _content(hospital.id, title="제목", body="본문", image_url=None)
    db = FakeDB(hospital=hospital, content=content)
    task = FakeTask()
    monkeypatch.setattr(operations_api.generate_content_image, "apply_async", task.apply_async)

    response = await operations_api.regenerate_content_image_operation(
        hospital.id, content.id, db=db
    )

    assert response["detail"] == "Content image generation queued"
    assert task.calls == [{"args": [str(content.id)], "queue": "content"}]
    assert [row.action for row in db.added if isinstance(row, AdminAuditLog)] == [
        "regenerate_content_image_requested",
        "regenerate_content_image",
    ]
