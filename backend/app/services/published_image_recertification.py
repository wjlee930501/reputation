"""공개 글 대표 이미지 재인증 workflow의 코드·키·시도 예산 정본 (H-01).

PATCH 디스패치(API)·재인증 태스크(worker)·복구 sweep·운영자 재시도가 모두 이 한 곳을
읽는다. 예산이 여기 하나뿐이라 어떤 경로 조합으로도 (글, 판)당 유료 재검수가
``ATTEMPT_BUDGET``을 넘지 않는다. API가 worker 모듈을 가져오지 않도록 services에 둔다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import String, and_, cast, false, func, literal, update
from sqlalchemy.sql.elements import ColumnElement

from app.models.content import ContentItem, ContentStatus
from app.models.operations import JSONValue, OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

RECERTIFY_OPERATION: Final = "RECERTIFY_PUBLISHED_IMAGE"

PUBLISHED_IMAGE_RECERTIFY_REJECTED: Final = "PUBLISHED_IMAGE_RECERTIFY_REJECTED"
PUBLISHED_IMAGE_MISSING: Final = "PUBLISHED_IMAGE_MISSING"
PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED: Final = "PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED"

# 자동 복구가 더 진행하지 않고 사람의 결정을 기다리는 코드. 같은 판에서 이 코드로 끝난
# 실행이 있으면 어떤 경로도 공급자를 다시 부르지 않는다.
OPERATOR_REQUIRED_CODES: Final[frozenset[str]] = frozenset(
    {
        PUBLISHED_IMAGE_RECERTIFY_REJECTED,
        PUBLISHED_IMAGE_MISSING,
        PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED,
    }
)
# 한 (글, 판)에서 공급자를 부를 수 있는 실행 수의 절대 상한.
ATTEMPT_BUDGET: Final = 3
# 종결된 실행과 다음 자동 재실행 사이의 최소 간격. 비용 가드 차단·공급자 순간 장애가
# 예산 세 번을 몇 분 만에 태우지 않게 한다.
RETRY_COOLDOWN: Final = timedelta(minutes=15)
# 태스크 하드 제한(900초) + 여유. 이보다 오래된 비종결 실행은 유실된 것으로 보고
# 새 실행을 막지 않는다.
IN_FLIGHT_TIMEOUT: Final = timedelta(minutes=30)

_NON_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        OperationRunState.REQUESTED.value,
        OperationRunState.QUEUED.value,
        OperationRunState.RUNNING.value,
    }
)

# 사람의 결정을 기다리는 판을 글 자체에 남기는 표시. sweep이 후보 SQL에서 이 행을
# 제외해 다른 글의 자리를 뺏지 않게 한다.
MARKER_FIELD: Final = "image_recertification"

SAFE_MESSAGES: Final[dict[str, str]] = {
    PUBLISHED_IMAGE_RECERTIFY_REJECTED: (
        "제목이 바뀌어 대표 이미지가 글 주제와 맞지 않습니다."
    ),
    PUBLISHED_IMAGE_MISSING: "공개 중인 글에 대표 이미지가 없어 재인증할 수 없습니다.",
    PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED: (
        "대표 이미지 자동 재인증이 정해진 횟수만큼 반복 실패했습니다."
    ),
}
# 공개 글에는 관리자 이미지 업로드 경로가 없고 `generate_content_image`는 PUBLISHED를
# 거절한다. 사람이 실제로 할 수 있는 조치만 적는다.
OPERATOR_ACTION: Final = "제목을 되돌리거나, 글을 반려(비공개)해 새 이미지로 재생성하세요."

_EPOCH: Final = datetime.min.replace(tzinfo=UTC)


def base_key(item_id: uuid.UUID | str, revision: int) -> str:
    """PATCH 디스패치가 쓰는 (글, 판) 실행 키."""

    return f"recertify:{item_id}:{revision}"


def sweep_key(item_id: uuid.UUID | str, revision: int, attempt: int) -> str:
    """sweep 재실행 키. 종결된 같은 키를 dispatch가 '이미 실행함'으로 오인하지 않게 한다."""

    return f"{base_key(item_id, revision)}:s{attempt}"


def request_payload(item_id: uuid.UUID | str, revision: int) -> dict[str, JSONValue]:
    """실행 payload. 시도 수는 키 파싱이 아니라 여기 적힌 판으로 센다."""

    target_id = str(item_id)
    return {
        **build_request_payload(
            DispatchPayload("content_item", target_id, "content", (target_id,))
        ),
        "revision": int(revision),
    }


def payload_revision(run: OperationRun) -> int | None:
    """실행이 어느 판을 대상으로 만들어졌는지. 판이 없는 실행은 세지 않는다."""

    payload = getattr(run, "request_payload", None)
    revision = payload.get("revision") if isinstance(payload, Mapping) else None
    return revision if type(revision) is int else None


def payload_source_id(run: OperationRun) -> str | None:
    payload = getattr(run, "request_payload", None)
    source_id = payload.get("source_id") if isinstance(payload, Mapping) else None
    return source_id if isinstance(source_id, str) else None


def is_terminal(run: OperationRun) -> bool:
    state = getattr(run.state, "value", run.state)
    return str(state) not in _NON_TERMINAL_STATES


def _at_revision(runs: Iterable[OperationRun], revision: int) -> list[OperationRun]:
    return [run for run in runs if payload_revision(run) == revision]


def attempts_spent(runs: Iterable[OperationRun], revision: int) -> int:
    """이 (글, 판)에서 이미 소진한 실행 수 — 예산의 유일한 정의.

    PATCH·sweep·운영자 재시도를 키 모양과 무관하게 함께 센다. 종결 상태(FAILED,
    CANCELLED, 인증을 남기지 못한 SUCCEEDED)면 한 번 쓴 것으로 본다. 인증이 살아 있는
    SUCCEEDED는 호출자가 그 전에 이미 성공으로 끝내므로 이 셈에 도달하지 않는다.
    """

    return sum(1 for run in _at_revision(runs, revision) if is_terminal(run))


def pending_operator_code(runs: Iterable[OperationRun], revision: int) -> str | None:
    """이 판이 사람의 결정을 기다리며 끝났다면 그 코드."""

    blocked = [
        run
        for run in _at_revision(runs, revision)
        if is_terminal(run) and str(run.safe_error_code or "") in OPERATOR_REQUIRED_CODES
    ]
    if not blocked:
        return None
    blocked.sort(key=lambda run: run.completed_at or _EPOCH)
    return str(blocked[-1].safe_error_code)


def latest_terminal_completion(
    runs: Iterable[OperationRun], revision: int
) -> datetime | None:
    finished = [
        run.completed_at
        for run in _at_revision(runs, revision)
        if is_terminal(run) and run.completed_at is not None
    ]
    return max(finished) if finished else None


def _activity_at(run: OperationRun) -> datetime | None:
    for value in (run.heartbeat_at, run.started_at, run.queued_at, run.requested_at):
        if value is not None:
            return value
    return None


def in_flight(runs: Iterable[OperationRun], revision: int, *, now: datetime) -> bool:
    """지금 이 판을 실제로 실행 중인 run이 있는가.

    판이 다른 비종결 실행은 막지 않는다 — 그 실행은 시작 즉시 현재 판을 보고 끝난다.
    좌초한(하드 제한을 넘긴) 실행도 막지 않는다. 자기 자신은 RUNNING이므로 호출자는
    아직 실행 이력을 읽기 전인 sweep에서만 이 함수를 쓴다.
    """

    for run in _at_revision(runs, revision):
        if is_terminal(run):
            continue
        activity = _activity_at(run)
        if activity is None or activity > now - IN_FLIGHT_TIMEOUT:
            return True
    return False


def sweep_may_dispatch(
    runs: Sequence[OperationRun], revision: int, *, now: datetime
) -> bool:
    """자동 재실행을 하나 더 만들어도 되는가.

    예산이 소진된 직후에도 정확히 한 번은 더 만든다 — 그 실행이 유료 호출 없이
    '반복 실패' incident를 열어, 태스크가 아예 시작하지 못한 실패(TASK_FAILED,
    BROKER_UNAVAILABLE 등)도 사람이 볼 수 있는 결말을 갖게 한다.
    """

    if in_flight(runs, revision, now=now):
        return False
    if pending_operator_code(runs, revision) is not None:
        return False
    if attempts_spent(runs, revision) > ATTEMPT_BUDGET:
        # 예산 소진을 기록한 마지막 실행까지 끝났다. 더 만들지 않는다.
        return False
    finished_at = latest_terminal_completion(runs, revision)
    if finished_at is not None and finished_at > now - RETRY_COOLDOWN:
        return False
    return True


def next_sweep_key(
    item_id: uuid.UUID | str, revision: int, runs: Sequence[OperationRun]
) -> str:
    """아직 쓰지 않은 재실행 키. 번호는 이 판의 실행 수에서 이어 붙인다."""

    return sweep_key(item_id, revision, len(_at_revision(runs, revision)) + 1)


def blocked_row_predicate() -> ColumnElement[bool]:
    """sweep 후보에서 제외할 행 — 현재 판이 사람의 결정을 기다리는 글.

    제목을 고치면 판이 올라가 이 표시가 저절로 맞지 않게 된다. 숫자 캐스팅 실패가
    sweep 전체를 깨뜨리지 않도록 판은 문자열로 비교한다. 표시가 없는 행에서는 비교가
    NULL이 되므로 coalesce로 거짓을 확정한다 — 부정(`~`)이 모든 행을 지우지 않게.
    """

    marker = ContentItem.essence_check_summary[MARKER_FIELD]
    return func.coalesce(
        and_(
            marker["revision"].as_string() == cast(ContentItem.content_revision, String),
            marker["blocked"].as_string() == "true",
        ),
        false(),
    )


def mark_blocked(db, *, item_id: uuid.UUID, revision: int, code: str) -> int:
    """사람의 결정을 기다리는 판을 글에 남긴다 (status·판 CAS)."""

    marker = {"revision": int(revision), "blocked": True, "code": code}
    return _write_summary(
        db,
        item_id=item_id,
        revision=revision,
        summary=_existing_summary().op("||")(_jsonb({MARKER_FIELD: marker})),
    )


def clear_marker(db, *, item_id: uuid.UUID, revision: int) -> int:
    """재인증이 성공한 판의 표시를 지운다 — sweep이 다시 볼 수 있게 한다."""

    return _write_summary(
        db,
        item_id=item_id,
        revision=revision,
        summary=_existing_summary().op("-")(cast(literal(MARKER_FIELD), String)),
    )


def _jsonb(value: dict):
    summary_type = ContentItem.__table__.c.essence_check_summary.type
    return cast(literal(value, summary_type), summary_type)


def _existing_summary():
    return func.coalesce(ContentItem.essence_check_summary, _jsonb({}))


def _write_summary(db, *, item_id: uuid.UUID, revision: int, summary) -> int:
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.content_revision == revision,
        )
        .values(essence_check_summary=summary)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
