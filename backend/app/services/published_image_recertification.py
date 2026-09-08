"""공개 글 대표 이미지 재인증 workflow의 코드·키·시도 예산 정본 (H-01).

PATCH 디스패치(API)·재인증 태스크(worker)·복구 sweep·운영자 재시도가 모두 이 한 곳을
읽는다. 예산이 여기 하나뿐이라 어떤 경로 조합으로도 (글, 이미지 subject)당 유료 재검수가
``ATTEMPT_BUDGET``을 넘지 않는다. API가 worker 모듈을 가져오지 않도록 services에 둔다.

예산·표시·incident의 키는 `content_revision`이 아니라 subject(유형 + 제목)다. 공급자가
실제로 인증하는 것이 subject이고, 판은 제목을 건드리지 않는 편집·Essence 재승인으로도
올라가 예산을 되살리고 표시를 지우고 같은 거절로 두 번째 incident를 열었다.

불변식: **유료 호출을 한 태스크 실행(execution)은 어떤 경우에도 세지 않고 넘어갈 수
없다.** 예산은 실행 행(run) 수가 아니라 그 행이 실제로 산 결제 수로 센다 —
`task_acks_late`·`task_reject_on_worker_lost` 아래에서 worker가 유료 호출 도중 죽으면
broker가 같은 task를 재배달하고 prerun이 같은 행을 다시 claim하며 `attempt_count`를
올린다. 행은 하나지만 돈은 여러 번 나갈 수 있다. 그래서 결제는 boolean이 아니라
`mark_paid_execution_started`가 유료 호출 **직전에** 별도 세션으로 올리는 durable
계수기(`paid_executions`)로 남는다 — boolean이면 execution 1에서 사고 재배달돼
execution 2에서 또 산 행이 하나로 세어져 다음 실행이 네 번째 호출을 산다.

세는 규칙은 둘뿐이다. 종결했거나 좌초한(하드 제한 + 여유를 넘도록 종결 기록이 없는)
실행은 결과를 남기지 못했어도 이미 샀을 수 있으므로 `max(attempt_count,
paid_executions, 1)`로 센다. 아직 도는 실행은 자기 것이든 남의 것이든 기록된
`paid_executions`만큼 센다(계수기 이전의 `provider_called` payload는 몇 번인지 알 수
없으므로 하나로 본다).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Integer, String, and_, case, cast, false, func, literal, select, update
from sqlalchemy.sql.elements import ColumnElement

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.operations import (
    Incident,
    IncidentState,
    JSONValue,
    OperationRun,
    OperationRunState,
)
from app.services.image_engine import image_subject_hash
from app.services.incident_safety import build_incident_key
from app.services.incident_types import IncidentFingerprint
from app.services.operation_run_payloads import DispatchPayload, build_request_payload

RECERTIFY_OPERATION: Final = "RECERTIFY_PUBLISHED_IMAGE"

PUBLISHED_IMAGE_RECERTIFY_REJECTED: Final = "PUBLISHED_IMAGE_RECERTIFY_REJECTED"
PUBLISHED_IMAGE_MISSING: Final = "PUBLISHED_IMAGE_MISSING"
PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED: Final = "PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED"

# 자동 복구가 더 진행하지 않고 사람의 결정을 기다리는 코드. 같은 subject에서 이 코드로
# 끝난 실행이 있으면 어떤 경로도 공급자를 다시 부르지 않는다.
OPERATOR_REQUIRED_CODES: Final[frozenset[str]] = frozenset(
    {
        PUBLISHED_IMAGE_RECERTIFY_REJECTED,
        PUBLISHED_IMAGE_MISSING,
        PUBLISHED_IMAGE_RECERTIFY_UNRECOVERED,
    }
)
# 한 (글, subject)에서 공급자를 부를 수 있는 실행 수의 절대 상한.
ATTEMPT_BUDGET: Final = 3
# 종결된 실행과 다음 자동 재실행 사이의 최소 간격. 비용 가드 차단·공급자 순간 장애가
# 예산 세 번을 몇 분 만에 태우지 않게 한다.
RETRY_COOLDOWN: Final = timedelta(minutes=15)
# 태스크 하드 제한(900초) + 여유. 이보다 오래된 비종결 실행은 좌초한 것으로 본다 —
# 새 실행을 막지는 않지만, 이미 돈을 썼을 수 있으므로 시도로는 센다.
IN_FLIGHT_TIMEOUT: Final = timedelta(minutes=30)

_NON_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        OperationRunState.REQUESTED.value,
        OperationRunState.QUEUED.value,
        OperationRunState.RUNNING.value,
    }
)

# 사람의 결정을 기다리는 subject를 글 자체에 남기는 표시. sweep이 후보 SQL에서 이 행을
# 제외해 다른 글의 자리를 뺏지 않게 한다.
MARKER_FIELD: Final = "image_recertification"

# 유료 호출을 시작한 실행이 스스로 올리는 durable 계수기. 종결 전에도 예산에 잡힌다.
PAID_EXECUTIONS_FIELD: Final = "paid_executions"
# 계수기 이전의 boolean 표시. 몇 번 샀는지 알 수 없으므로 하나로 센다.
LEGACY_PROVIDER_CALL_FIELD: Final = "provider_called"

# 차단이 아직 사람에게 보이는 incident 상태.
VISIBLE_INCIDENT_STATES: Final[tuple[str, ...]] = (
    IncidentState.OPEN.value,
    IncidentState.RETRYING.value,
    IncidentState.ACKNOWLEDGED.value,
)

# 차단 incident의 신원. 차단 세 코드는 지문 하나를 공유해 (글, subject)당 한 건이 된다.
INCIDENT_PIPELINE: Final = "content_generation"
INCIDENT_OBJECT_TYPE: Final = "content_item"
INCIDENT_FINGERPRINT: Final = IncidentFingerprint.RENDER_FAILED

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


def subject_hash_of(item: ContentItem) -> str:
    """공급자가 실제로 인증하는 대상 — 콘텐츠 유형과 제목."""

    return image_subject_hash(item.content_type, item.title)


def base_key(item_id: uuid.UUID | str, subject_hash: str) -> str:
    """PATCH 디스패치가 쓰는 (글, subject) 실행 키."""

    return f"recertify:{item_id}:{subject_hash[:16]}"


def sweep_key(item_id: uuid.UUID | str, subject_hash: str, attempt: int) -> str:
    """sweep 재실행 키. 종결된 같은 키를 dispatch가 '이미 실행함'으로 오인하지 않게 한다."""

    return f"{base_key(item_id, subject_hash)}:s{attempt}"


def incident_object_id(item_id: uuid.UUID | str, subject_hash: str) -> str:
    """사람이 결정하는 단위 — (글, subject). 다음 제목 편집은 다른 건이다."""

    return f"{item_id}:{subject_hash[:16]}"


def incident_dedupe_key(item_id: uuid.UUID | str, subject_hash: str) -> str:
    """이 (글, subject) 차단 incident의 중복 제거 키.

    성공한 subject가 자기 건만 닫도록, 사고를 여는 쪽과 닫는 쪽이 같은 키를 여기서 읽는다.
    차단 세 코드는 지문을 공유하므로 (글, subject)당 키는 하나다.
    """

    return build_incident_key(
        INCIDENT_PIPELINE,
        INCIDENT_OBJECT_TYPE,
        incident_object_id(item_id, subject_hash),
        INCIDENT_FINGERPRINT,
    )


def request_payload(
    item_id: uuid.UUID | str,
    *,
    subject_hash: str,
    title: str | None,
    revision: int,
) -> dict[str, JSONValue]:
    """실행 payload. 시도 수는 키 파싱이 아니라 여기 적힌 subject로 센다.

    `title`은 읽기 위한 값이고, `revision`은 write-back CAS가 쓰는 당시 판이다.
    """

    target_id = str(item_id)
    return {
        **build_request_payload(
            DispatchPayload("content_item", target_id, "content", (target_id,))
        ),
        "subject_hash": subject_hash,
        "title": title,
        "revision": int(revision),
    }


def payload_subject_hash(run: OperationRun | None) -> str | None:
    """실행이 어느 subject를 대상으로 만들어졌는지."""

    payload = getattr(run, "request_payload", None)
    value = payload.get("subject_hash") if isinstance(payload, Mapping) else None
    return value if isinstance(value, str) and value else None


def paid_executions(run: OperationRun) -> int:
    """이 실행 행이 유료 호출 직전에 스스로 기록한 결제 수."""

    payload = getattr(run, "request_payload", None)
    if not isinstance(payload, Mapping):
        return 0
    recorded = payload.get(PAID_EXECUTIONS_FIELD)
    if isinstance(recorded, int) and not isinstance(recorded, bool):
        return max(recorded, 0)
    # 계수기 이전 payload — 샀다는 사실만 있고 횟수는 없다.
    return 1 if payload.get(LEGACY_PROVIDER_CALL_FIELD) else 0


def mark_paid_execution_started(run_id: uuid.UUID) -> None:
    """유료 호출 **직전에** 결제 계수기를 별도 세션으로 하나 올린다.

    태스크의 본 세션은 글 행 잠금을 쥔 채 공급자를 부르므로 여기서 커밋할 수 없다. 이
    기록이 없으면, 호출을 산 채로 죽어 종결도 좌초도 아닌 실행이 잠금을 이어받은 다른
    실행의 예산 검사에서 0으로 세어진다. 증가는 DB에서 읽고 더하므로 같은 행이 재배달로
    여러 번 돌아도 결제마다 하나씩 는다. operation_runs 행만 건드리므로 본 세션이 쥔
    잠금과 겹치지 않고, `version`도 올리지 않아 종결의 낙관적 검사를 깨지 않는다.
    """

    payload_type = OperationRun.__table__.c.request_payload.type
    payload = OperationRun.request_payload
    recorded = func.coalesce(
        cast(payload.op("->>")(literal(PAID_EXECUTIONS_FIELD)), Integer),
        case(
            (payload.op("->>")(literal(LEGACY_PROVIDER_CALL_FIELD)) == literal("true"), 1),
            else_=0,
        ),
    )
    counter = cast(
        func.jsonb_build_object(literal(PAID_EXECUTIONS_FIELD), recorded + 1), payload_type
    )
    with SyncSessionLocal() as db:
        db.execute(
            update(OperationRun)
            .where(OperationRun.id == run_id)
            .values(request_payload=payload.op("||")(counter))
            .execution_options(synchronize_session=False)
        )
        db.commit()


def payload_source_id(run: OperationRun) -> str | None:
    payload = getattr(run, "request_payload", None)
    source_id = payload.get("source_id") if isinstance(payload, Mapping) else None
    return source_id if isinstance(source_id, str) else None


def is_terminal(run: OperationRun) -> bool:
    state = getattr(run.state, "value", run.state)
    return str(state) not in _NON_TERMINAL_STATES


def _at_subject(runs: Iterable[OperationRun], subject_hash: str) -> list[OperationRun]:
    """이 subject를 대상으로 만들어진 실행.

    subject를 적지 않은 예전 payload는 어느 subject에 썼는지 알 수 없으므로 보수적으로
    현재 subject의 예산에 포함한다.
    """

    return [
        run
        for run in runs
        if (recorded := payload_subject_hash(run)) is None or recorded == subject_hash
    ]


def _activity_at(run: OperationRun) -> datetime | None:
    for value in (run.heartbeat_at, run.started_at, run.queued_at, run.requested_at):
        if value is not None:
            return value
    return None


def _is_stranded(run: OperationRun, *, now: datetime) -> bool:
    """비종결인데 하드 제한을 한참 넘긴 실행 — worker와 함께 유실됐다."""

    activity = _activity_at(run)
    return activity is not None and activity <= now - IN_FLIGHT_TIMEOUT


def _executions_spent(run: OperationRun, *, now: datetime) -> int:
    """이 실행 행이 이미 산 유료 호출 수.

    종결·좌초한 행은 결과를 남기지 못했어도 이미 샀을 수 있으므로 claim 수
    (`attempt_count`)와 기록된 결제 수 중 큰 값을 센다(둘 다 없는 행도 최소 하나로 본다).
    아직 도는 행은 자기 것이든 남의 것이든 기록된 결제 수만 센다 — 결제 직전에 커밋되므로
    `attempt_count`를 대신 세면 아직 사지 않은 execution까지 예산에서 빼앗고, 재배달로
    두 번 산 execution은 하나로 세어진다.
    """

    attempts = int(getattr(run, "attempt_count", 0) or 0)
    paid = paid_executions(run)
    if is_terminal(run) or _is_stranded(run, now=now):
        return max(attempts, paid, 1)
    return paid


def attempts_spent(
    runs: Iterable[OperationRun], subject_hash: str, *, now: datetime
) -> int:
    """이 (글, subject)에서 이미 산 유료 호출 수 — 예산의 유일한 정의.

    PATCH·sweep·운영자 재시도를 키 모양과 무관하게 함께 센다. 종결 상태(FAILED,
    CANCELLED, 인증을 남기지 못한 SUCCEEDED)면 그 행의 execution을 모두 쓴 것으로 본다.
    인증이 살아 있는 SUCCEEDED는 호출자가 그 전에 이미 성공으로 끝내므로 이 셈에
    도달하지 않는다. 좌초한 실행은 결과를 남기지 못했을 뿐 이미 공급자를 불렀을 수 있어
    함께 센다. 진행 중인 실행은 자기 것을 포함해 기록된 결제 수만큼 세므로, 재배달된
    실행은 자기 앞의 결제를 몇 번이든 스스로 센다.
    """

    return sum(
        _executions_spent(run, now=now) for run in _at_subject(runs, subject_hash)
    )


def pending_operator_code(
    runs: Iterable[OperationRun], subject_hash: str
) -> str | None:
    """이 subject가 사람의 결정을 기다리며 끝났다면 그 코드."""

    blocked = [
        run
        for run in _at_subject(runs, subject_hash)
        if is_terminal(run) and str(run.safe_error_code or "") in OPERATOR_REQUIRED_CODES
    ]
    if not blocked:
        return None
    blocked.sort(key=lambda run: run.completed_at or _EPOCH)
    return str(blocked[-1].safe_error_code)


def latest_terminal_completion(
    runs: Iterable[OperationRun], subject_hash: str
) -> datetime | None:
    finished = [
        run.completed_at
        for run in _at_subject(runs, subject_hash)
        if is_terminal(run) and run.completed_at is not None
    ]
    return max(finished) if finished else None


def in_flight(
    runs: Iterable[OperationRun], subject_hash: str, *, now: datetime
) -> bool:
    """지금 이 subject를 실제로 실행 중인 run이 있는가.

    subject가 다른 비종결 실행은 막지 않는다 — 그 실행은 시작 즉시 현재 subject를 보고
    돈을 쓰지 않고 끝난다. 좌초한 실행도 막지 않는다(예산으로는 이미 셌다).
    """

    return any(
        not is_terminal(run) and not _is_stranded(run, now=now)
        for run in _at_subject(runs, subject_hash)
    )


def other_run_in_flight(
    runs: Iterable[OperationRun],
    subject_hash: str,
    *,
    now: datetime,
    run_id: uuid.UUID | None,
) -> bool:
    """자기 실행을 뺀 이 subject의 진행 중 실행이 있는가.

    예산이 끝난 것을 본 실행이 '반복 실패'로 닫아야 할지 판단할 때 쓴다. 아직 도는 다른
    실행이 성공할 수 있으므로, 그 앞에서 결말을 선언하면 사실이 아니다.
    """

    return any(
        run.id != run_id and not is_terminal(run) and not _is_stranded(run, now=now)
        for run in _at_subject(runs, subject_hash)
    )


def sweep_may_dispatch(
    runs: Sequence[OperationRun],
    subject_hash: str,
    *,
    now: datetime,
    block_visible: bool = True,
) -> bool:
    """자동 재실행을 하나 더 만들어도 되는가.

    예산이 소진된 직후에도 정확히 한 번은 더 만든다 — 그 실행이 유료 호출 없이
    '반복 실패' incident를 열어, 태스크가 아예 시작하지 못한 실패(TASK_FAILED,
    BROKER_UNAVAILABLE 등)도 사람이 볼 수 있는 결말을 갖게 한다.

    `block_visible`은 이 subject의 차단이 사람에게 아직 보이는가다(사고가 미해결).
    """

    if in_flight(runs, subject_hash, now=now):
        return False
    finished_at = latest_terminal_completion(runs, subject_hash)
    if finished_at is not None and finished_at > now - RETRY_COOLDOWN:
        return False
    if pending_operator_code(runs, subject_hash) is not None:
        # 사람의 결정을 기다리는 subject다. 그 사고가 아직 열려 있으면 같은 답을 다시
        # 사지 않고 멈춘다. 사고가 닫혔는데 차단은 그대로면(다른 subject의 성공이 닫았거나
        # 사람이 수동으로 닫았다) 아무도 보지 않는 보류가 된다 — 실행 하나를 더 만들어
        # 태스크의 시작 게이트가 사고와 표시를 다시 남기게 한다. 그 경로는 공급자를
        # 부르지 않으므로 예산을 쓰지 않는다.
        return not block_visible
    if attempts_spent(runs, subject_hash, now=now) > ATTEMPT_BUDGET:
        # 예산 소진을 기록해야 할 마지막 실행까지 끝났다. 그 실행이 사고를 남겼으면 더
        # 만들지 않는다. 사고 생성 자체가 실패해(보류 코드도 표시도 없이) 끝났다면
        # 아무도 보지 않는 보류이므로, 쿨다운마다 무료 실행 하나로 사고 생성을 다시 시도한다.
        return not block_visible
    return True


def next_sweep_key(
    item_id: uuid.UUID | str, subject_hash: str, runs: Sequence[OperationRun]
) -> str:
    """아직 쓰지 않은 재실행 키. 번호는 이 subject의 실행 수에서 이어 붙인다."""

    return sweep_key(item_id, subject_hash, len(_at_subject(runs, subject_hash)) + 1)


def blocked_row_predicate() -> ColumnElement[bool]:
    """sweep 후보에서 제외할 행 — 현재 subject의 차단을 사람이 실제로 보고 있는 글.

    콘텐츠 유형은 바뀌지 않으므로 제목만 비교하면 subject 비교와 같다. 제목을 고치면
    표시가 저절로 맞지 않게 된다. 표시가 없는 행에서는 비교가 NULL이 되므로 coalesce로
    거짓을 확정한다 — 부정(`~`)이 모든 행을 지우지 않게.

    표시만 보고 빼면 안 된다. 표시는 남았는데 그 incident가 어떤 경로로든 닫히면(다른
    subject의 성공, 사람의 정리) 자동 경로도 사람이 볼 사고도 없는 글이 된다. 표시가
    가리키는 incident가 미해결일 때만 빼고, 그렇지 않으면 후보로 되돌려 무료 실행 하나가
    사고와 표시를 다시 남기게 한다. incident 키가 없는 옛 표시도 같은 이유로 되돌아온다.
    """

    marker = ContentItem.essence_check_summary[MARKER_FIELD]
    return func.coalesce(
        and_(
            marker["title"].as_string() == ContentItem.title,
            marker["blocked"].as_string() == "true",
            select(Incident.id)
            .where(
                Incident.dedupe_key == marker["incident_key"].as_string(),
                Incident.state.in_(VISIBLE_INCIDENT_STATES),
            )
            .exists(),
        ),
        false(),
    )


def mark_blocked(
    db, *, item_id: uuid.UUID, subject_hash: str, title: str | None, code: str
) -> int:
    """사람의 결정을 기다리는 subject를 글에 남긴다 (status·제목 CAS).

    표시는 자기 incident의 키를 함께 들고 다닌다 — 후보 SQL이 그 사고가 아직 보이는지
    확인해야 표시만 남은 무음 보류가 생기지 않는다.
    """

    marker = {
        "subject_hash": subject_hash,
        "title": title,
        "blocked": True,
        "code": code,
        "incident_key": incident_dedupe_key(item_id, subject_hash),
    }
    return _write_summary(
        db,
        item_id=item_id,
        title=title,
        summary=_existing_summary().op("||")(_jsonb({MARKER_FIELD: marker})),
    )


def clear_marker(db, *, item_id: uuid.UUID, title: str | None) -> int:
    """재인증이 성공한 subject의 표시를 지운다 — sweep이 다시 볼 수 있게 한다."""

    return _write_summary(
        db,
        item_id=item_id,
        title=title,
        summary=_existing_summary().op("-")(cast(literal(MARKER_FIELD), String)),
    )


def _jsonb(value: dict):
    summary_type = ContentItem.__table__.c.essence_check_summary.type
    return cast(literal(value, summary_type), summary_type)


def _existing_summary():
    return func.coalesce(ContentItem.essence_check_summary, _jsonb({}))


def _write_summary(db, *, item_id: uuid.UUID, title: str | None, summary) -> int:
    # NULL 제목은 `= NULL`로 비교하면 항상 거짓이라, 바뀐 것이 없는데도 0행이 나온다.
    title_clause = (
        ContentItem.title.is_(None) if title is None else ContentItem.title == title
    )
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status == ContentStatus.PUBLISHED,
            title_clause,
        )
        .values(essence_check_summary=summary)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
