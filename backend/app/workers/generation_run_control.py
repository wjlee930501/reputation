"""Durable control-plane records for content and image generation workers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import openai
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.operations import (
    JSONValue,
    OperationRun,
    OperationRunState,
)
from app.services.operation_run_payloads import (
    DispatchPayload,
    UnsafeDispatchPayload,
    build_request_payload,
    parse_stored_dispatch,
)
from app.workers.generation_retry_policy import (
    GenerationRetryClass,
    next_recovery_sweep,
    retry_class_for,
)

logger = logging.getLogger(__name__)

_SAFE_FAILURE_MESSAGE = "생성 작업이 완료되지 않았습니다. 운영 센터에서 원인을 확인해 주세요."
GENERATION_REFERENCE_REJECTION_MESSAGE = (
    "공신력 있는 참고 자료를 확보하지 못했습니다 — 실제 문서 확인을 통과한 출처가 없어 "
    "원고를 저장하지 않았습니다. 다음 자동 재시도가 검증된 문서를 다시 찾습니다."
)
_DEFAULT_REJECTION_MESSAGE = (
    "가격·지역·검색 구조 자동 검수 게이트가 재작성 후에도 통과되지 않았습니다. "
    "운영 센터에서 차단 원인과 승인된 입력 자료를 확인해 주세요."
)
_REJECTION_MESSAGES = {
    "price": (
        "검증되지 않은 원화 가격·보험 범위 표현이 재작성 후에도 남았습니다. "
        "운영 센터에서 승인된 가격 근거와 차단 문구를 확인해 주세요."
    ),
    "geo": (
        "지역·의료 근거 검수에 필요한 공신력 있는 참고 자료를 확보하지 못했습니다. "
        "운영 센터에서 승인된 자료와 콘텐츠 주제를 확인해 주세요."
    ),
    # 참고자료가 실제 문서 확인(빈 페이지·없는 문서·주제 불일치 제거)과 검증 목록 치유 뒤에도
    # 하나도 남지 않았다. 병원명·지역 누락(GEO) 거절과 원인이 달라 문구를 나눈다.
    "references": GENERATION_REFERENCE_REJECTION_MESSAGE,
    "seo": (
        "검색 문서 구조 검수가 재작성 후에도 통과되지 않았습니다. "
        "운영 센터에서 제목과 문서 구조 차단 원인을 확인해 주세요."
    ),
    "forbidden": (
        "의료광고 금지 표현이 재작성 후에도 남았습니다. "
        "운영 센터에서 차단된 공개 필드를 확인해 주세요."
    ),
    "faq": (
        "FAQ 공개 필수 항목이 재작성 후에도 완성되지 않았습니다. "
        "운영 센터에서 질문과 답변 요약을 확인해 주세요."
    ),
    "truncated": (
        "생성 출력이 잘려 완전한 원고를 받지 못했습니다. "
        "다음 예약 배치가 분량을 줄여 다시 생성합니다."
    ),
    "must_use": (
        "승인된 필수 문구가 재작성 후에도 원문 그대로 들어가지 않았습니다. "
        "운영 센터에서 병원 정보 탭의 필수 문구를 확인해 주세요."
    ),
}
GENERATION_REJECTION_SAFE_MESSAGES = frozenset(
    {_DEFAULT_REJECTION_MESSAGE, *_REJECTION_MESSAGES.values()}
)


class GenerationItemState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DISCARDED = "DISCARDED"


class GenerationTaskRequest(Protocol):
    id: str | None
    headers: Mapping[str, str] | None
    operation_run_claim_version: int | None


class GenerationTask(Protocol):
    request: GenerationTaskRequest


# 야간 배치가 슬롯 하나씩 팬아웃하는 작업의 실행 종류. 배치 실행(부모)과 구분되고,
# 유실 시 자율 복구가 저장된 payload로 같은 인자·큐로만 다시 배포한다.
GENERATE_CONTENT_ITEM_OPERATION = "GENERATE_CONTENT_ITEM"
GENERATE_CONTENT_ITEM_TASK = "app.workers.tasks.generate_claimed_content_item"
GENERATE_CONTENT_ITEM_PURPOSE = "generate-claimed-content-item"

_OPERATION_TASK_POLICIES = {
    "app.workers.tasks.trigger_v0_report": ("TRIGGER_V0_REPORT", "hospital", "reports"),
    "app.workers.tasks.build_aeo_site": ("REBUILD_SITE", "hospital", "default"),
    "app.workers.tasks.run_sov_for_hospital": ("RUN_SOV", "hospital", "sov"),
    "app.workers.tasks.regenerate_content_item": (
        "REGENERATE_CONTENT",
        "content_item",
        "content",
    ),
    "app.workers.tasks.generate_claimed_content_item": (
        GENERATE_CONTENT_ITEM_OPERATION,
        "content_item",
        "content",
    ),
    "app.workers.tasks.generate_content_image": (
        "REGENERATE_CONTENT_IMAGE",
        "content_item",
        "content",
    ),
    "app.workers.tasks.recertify_published_content_image": (
        "RECERTIFY_PUBLISHED_IMAGE",
        "content_item",
        "content",
    ),
    "app.workers.tasks.generate_monthly_report_for_hospital": (
        "GENERATE_MONTHLY_REPORT",
        "hospital",
        "reports",
    ),
    "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement": (
        "RECOVER_LEAD_MEASUREMENT",
        "lead_diagnosis",
        "leadgen",
    ),
    "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report": (
        "RECOVER_LEAD_REPORT",
        "lead_diagnosis",
        "leadgen",
    ),
}
_OPERATION_RUN_REQUIRED_TASKS = frozenset(
    {
        GENERATE_CONTENT_ITEM_TASK,
        "app.workers.tasks.generate_content_image",
        "app.workers.tasks.recertify_published_content_image",
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
    }
)


@dataclass(frozen=True, slots=True)
class ExplicitRunContext:
    run_id: uuid.UUID
    worker_id: str
    version: int


def classify_generation_failure(error: BaseException) -> tuple[str, str]:
    """Map runtime failures to allowlisted operator-safe facts."""
    match error:
        case TimeoutError() | openai.APITimeoutError():
            code = "PROVIDER_TIMEOUT"
            message = _SAFE_FAILURE_MESSAGE
        case ConnectionError():
            code = "PROVIDER_UNAVAILABLE"
            message = _SAFE_FAILURE_MESSAGE
        case ValueError():
            code = "GENERATION_REJECTED"
            message = _safe_rejection_message(error)
        case _:
            code = "GENERATION_FAILED"
            message = _SAFE_FAILURE_MESSAGE
    return code, message


def safe_generation_rejection_message(message: str | None) -> str:
    """Accept only classifier-owned rejection copy at persistence boundaries."""

    if message in GENERATION_REJECTION_SAFE_MESSAGES:
        return message
    return _DEFAULT_REJECTION_MESSAGE


def _safe_rejection_message(error: ValueError) -> str:
    """Classify known hard gates without persisting provider or candidate text."""

    detail = str(error).casefold()
    if "truncated" in detail:
        # 잘린 응답은 가격·지역·검색 구조 게이트 실패가 아니다. 운영자에게 틀린 원인을
        # 보여주면 승인 자료를 고치라는 엉뚱한 조치로 이어진다.
        return _REJECTION_MESSAGES["truncated"]
    if "unverified fixed price or coverage" in detail:
        return _REJECTION_MESSAGES["price"]
    if "references is empty" in detail:
        return _REJECTION_MESSAGES["references"]
    if "geo hard-fail" in detail or "citable reference" in detail:
        return _REJECTION_MESSAGES["geo"]
    if "seo hard-fail" in detail:
        return _REJECTION_MESSAGES["seo"]
    if "forbidden medical expressions" in detail:
        return _REJECTION_MESSAGES["forbidden"]
    if "faq output requires" in detail or "faq question must" in detail:
        return _REJECTION_MESSAGES["faq"]
    if "must_use messages missing verbatim" in detail:
        return _REJECTION_MESSAGES["must_use"]
    return _DEFAULT_REJECTION_MESSAGE


def explicit_run_context(task: GenerationTask) -> ExplicitRunContext | None:
    headers = task.request.headers
    run_id = headers.get("operation_run_id") if isinstance(headers, Mapping) else None
    worker_id = task.request.id
    version = getattr(task.request, "operation_run_claim_version", None)
    if (
        not isinstance(run_id, str)
        or not isinstance(worker_id, str)
        or not isinstance(version, int)
    ):
        return None
    try:
        return ExplicitRunContext(uuid.UUID(run_id), worker_id, version)
    except ValueError:
        return None


def explicit_run_matches(
    db: Session,
    task: GenerationTask,
    item_id: uuid.UUID | str,
    hospital_id: uuid.UUID | str,
    *,
    operation_type: str = "REGENERATE_CONTENT",
) -> bool:
    """Prove that the claimed Admin run authorizes this exact tenant target."""
    context = explicit_run_context(task)
    if context is None:
        return False
    run = db.get(OperationRun, context.run_id)
    if run is None:
        return False
    payload = run.request_payload if isinstance(run.request_payload, Mapping) else {}
    state = getattr(run.state, "value", run.state)
    return (
        run.operation_type == operation_type
        and state == OperationRunState.RUNNING.value
        and run.task_id == context.worker_id
        and run.lease_owner == context.worker_id
        and run.version == context.version
        and str(run.hospital_id) == str(hospital_id)
        and payload.get("source_type") == "content_item"
        and payload.get("source_id") == str(item_id)
    )


class DispatchVerdict(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    # 실행은 있지만 이 배달의 몫이 아니다(더 새 사본·이미 끝남·다른 사본이 실행 중).
    # 적체·재배달의 정상 결과라 실패로 세지 않는다.
    STALE = "STALE"
    # 실행이 없거나 대상·큐·인자가 다르다. 서명이 맞아도 막고 사고로 올린다.
    INVALID = "INVALID"


_TERMINAL_RUN_STATES = frozenset(
    state.value
    for state in (
        OperationRunState.SUCCEEDED,
        OperationRunState.PARTIAL,
        OperationRunState.FAILED,
        OperationRunState.CANCELLED,
    )
)


def operation_run_dispatch_verdict(
    db: Session,
    task: GenerationTask,
    task_name: str,
    task_args: Sequence[JSONValue],
) -> tuple[DispatchVerdict, str]:
    """Bind an Admin dispatch to its claimed run, tenant, target, queue, and arguments.

    대상·큐·인자 불일치는 언제나 INVALID다. 그것이 맞는 배달 중 소유권만 어긋난 것(실행이
    더 새 task id로 다시 배포됐거나 이미 끝났거나 같은 id의 다른 사본이 실행 중)은 STALE이다.
    """
    policy = _OPERATION_TASK_POLICIES.get(task_name)
    if policy is None:
        return DispatchVerdict.INVALID, "no_operation_policy"
    headers = task.request.headers
    raw_run_id = headers.get("operation_run_id") if isinstance(headers, Mapping) else None
    worker_id = task.request.id
    if not isinstance(raw_run_id, str) or not isinstance(worker_id, str):
        return DispatchVerdict.INVALID, "missing_operation_run"
    try:
        run = db.get(OperationRun, uuid.UUID(raw_run_id))
    except ValueError:
        return DispatchVerdict.INVALID, "missing_operation_run"
    if run is None:
        return DispatchVerdict.INVALID, "missing_operation_run"
    operation_type, target_type, queue = policy
    try:
        dispatch = parse_stored_dispatch(run.request_payload.get("_dispatch"))
    except (AttributeError, UnsafeDispatchPayload):
        return DispatchVerdict.INVALID, "unsafe_stored_dispatch"
    if not (
        run.operation_type == operation_type
        and dispatch.target_type == target_type
        and dispatch.target_id
        == str(run.hospital_id if target_type == "hospital" else task_args[0])
        and dispatch.queue == queue
        and dispatch.task_args == tuple(task_args)
    ):
        return DispatchVerdict.INVALID, "dispatch_mismatch"
    state = getattr(run.state, "value", run.state)
    claimed_version = getattr(task.request, "operation_run_claim_version", None)
    if run.task_id != worker_id:
        return DispatchVerdict.STALE, "superseded"
    if state in _TERMINAL_RUN_STATES:
        return DispatchVerdict.STALE, "already_terminal"
    if state != OperationRunState.RUNNING.value:
        # 내 task id의 대기 중 실행인데 claim하지 못했다 — 중복이 아니라 claim 저장 실패다.
        return DispatchVerdict.INVALID, "unclaimed"
    if run.lease_owner != worker_id:
        return DispatchVerdict.STALE, "running_elsewhere"
    if not isinstance(claimed_version, int):
        return DispatchVerdict.STALE, "duplicate_delivery"
    if run.version != claimed_version:
        return DispatchVerdict.STALE, "version_changed"
    return DispatchVerdict.AUTHORIZED, "authorized"


def operation_run_dispatch_authorized(
    db: Session,
    task: GenerationTask,
    task_name: str,
    task_args: Sequence[JSONValue],
) -> bool:
    verdict, _reason = operation_run_dispatch_verdict(db, task, task_name, task_args)
    return verdict is DispatchVerdict.AUTHORIZED


def mark_operation_run_queued(db: Session, run_id: uuid.UUID, observed_at: datetime) -> bool:
    """CAS REQUESTED->QUEUED right after a successful publish, and commit.

    REQUESTED로 남겨 두면 자율 복구가 2분 뒤 유실로 보고 사본을 하나 더 보낸다. 워커가 이미
    claim했으면 아무것도 바꾸지 않는다. 이 표시는 장부일 뿐이라 실패해도 배포를 되돌리지
    않는다 — 남은 REQUESTED는 자율 복구가 새 task id로 다시 보내고 이전 사본은 건너뛴다.
    """
    try:
        queued = db.execute(
            update(OperationRun)
            .where(
                OperationRun.id == run_id,
                OperationRun.state == OperationRunState.REQUESTED,
            )
            .values(
                state=OperationRunState.QUEUED,
                queued_at=observed_at,
                version=OperationRun.version + 1,
            )
            .returning(OperationRun.id)
        ).scalar_one_or_none()
        db.commit()
    except Exception as error:  # noqa: BLE001 - 장부 실패가 이미 끝난 publish를 실패로 만들지 않는다.
        db.rollback()
        logger.warning(
            "operation run queued mark failed run_id=%s error=%s", run_id, type(error).__name__
        )
        return False
    return queued is not None


def defer_operation_run(db: Session, task: GenerationTask, not_before: datetime) -> bool:
    """Hand the claimed run back as QUEUED until ``not_before``, and commit.

    긴 countdown 재시도 대신 쓴다. 지금 사본의 claim(task id·lease·판)이 그대로일 때만 바꾸며,
    판을 올리므로 뒤이은 성공 신호가 이 실행을 SUCCEEDED로 끝내지 않는다. 자율 복구가
    ``not_before`` 뒤에 새 task id·새 봉투로 다시 보낸다.
    """
    context = explicit_run_context(task)
    if context is None:
        return False
    deferred = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == context.run_id,
            OperationRun.task_id == context.worker_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == context.worker_id,
            OperationRun.version == context.version,
        )
        .values(
            state=OperationRunState.QUEUED,
            queued_at=datetime.now(UTC),
            not_before_at=not_before,
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    ).scalar_one_or_none()
    db.commit()
    return deferred is not None


def operation_run_required(task_name: str) -> bool:
    return task_name in _OPERATION_RUN_REQUIRED_TASKS


def finish_explicit_run(
    db: Session,
    task: GenerationTask,
    item_id: uuid.UUID,
    state: OperationRunState,
    *,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
) -> uuid.UUID | None:
    """Terminalize an Admin-dispatched run; the generic Celery signal then no-ops."""
    context = explicit_run_context(task)
    if context is None:
        return None
    success = int(state == OperationRunState.SUCCEEDED)
    failure = int(state in (OperationRunState.FAILED, OperationRunState.PARTIAL))
    item_result: dict[str, JSONValue] = {
        "state": state.value,
        "attempt_id": f"{context.run_id}:{item_id}:{context.version}",
    }
    if safe_error_code is not None:
        retry_class = retry_class_for(safe_error_code)
        item_result.update(
            {
                "safe_error_code": safe_error_code,
                "safe_error_message": safe_error_message,
                "retry_class": retry_class.value,
            }
        )
        if retry_class in (
            GenerationRetryClass.ENVIRONMENT_RECOVERABLE,
            GenerationRetryClass.SAMPLE_RECOVERABLE,
        ):
            item_result["next_retry_at"] = next_recovery_sweep().isoformat()
    result = db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == context.run_id,
            OperationRun.state == OperationRunState.RUNNING,
            OperationRun.lease_owner == context.worker_id,
            OperationRun.version == context.version,
        )
        .values(
            state=state,
            completed_at=datetime.now(UTC),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            total_count=1,
            success_count=success,
            failure_count=failure,
            skipped_count=int(state == OperationRunState.CANCELLED),
            result_summary={"items": {str(item_id): item_result}},
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
        .returning(OperationRun.id)
    ).scalar_one_or_none()
    db.commit()
    return result


def create_dispatched_item_run(
    db: Session,
    *,
    parent_run_id: uuid.UUID | None,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    task_id: str,
    task_args: tuple[JSONValue, ...],
    state: OperationRunState = OperationRunState.REQUESTED,
    idempotency_key: str | None = None,
) -> OperationRun:
    """Commit the durable intent of one fanned-out generation before publishing it.

    실행 기록을 먼저 커밋해야 worker가 메시지를 먼저 집어도 claim할 행이 있고, 배포가
    유실돼도 자율 복구가 **저장된 허용 목록 payload**로 같은 인자·큐에 다시 배포할 수 있다.
    """

    now = datetime.now(UTC)
    run = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type=GENERATE_CONTENT_ITEM_OPERATION,
        state=state,
        idempotency_key=idempotency_key or f"generation-item:{parent_run_id}:{item_id}",
        parent_run_id=parent_run_id,
        task_id=task_id,
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(item_id), "content", task_args)
        ),
        requested_at=now,
        started_at=now if state == OperationRunState.RUNNING else None,
        attempt_count=1 if state == OperationRunState.RUNNING else 0,
        total_count=1,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        version=1,
    )
    db.add(run)
    db.commit()
    return run


def finish_item_run(
    db: Session,
    run: OperationRun,
    item_id: uuid.UUID,
    state: OperationRunState,
    *,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
) -> None:
    """Terminalize a per-item run this worker created itself (no claimed lease)."""

    db.execute(
        update(OperationRun)
        .where(
            OperationRun.id == run.id,
            OperationRun.state.notin_(
                (
                    OperationRunState.SUCCEEDED,
                    OperationRunState.FAILED,
                    OperationRunState.PARTIAL,
                    OperationRunState.CANCELLED,
                )
            ),
        )
        .values(
            state=state,
            completed_at=datetime.now(UTC),
            heartbeat_at=None,
            lease_owner=None,
            lease_expires_at=None,
            total_count=1,
            success_count=int(state == OperationRunState.SUCCEEDED),
            failure_count=int(
                state in (OperationRunState.FAILED, OperationRunState.PARTIAL)
            ),
            skipped_count=int(state == OperationRunState.CANCELLED),
            safe_error_code=safe_error_code,
            safe_error_message=safe_error_message,
            version=OperationRun.version + 1,
        )
    )
    db.commit()


def create_item_run(
    db: Session,
    *,
    parent_run_id: uuid.UUID,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    operation_type: str,
    state: OperationRunState,
    result: JSONValue,
    safe_error_code: str | None = None,
    safe_error_message: str | None = None,
    attempt_kind: str = "final",
) -> OperationRun:
    success = int(state == OperationRunState.SUCCEEDED)
    failure = int(state in (OperationRunState.FAILED, OperationRunState.PARTIAL))
    child = OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type=operation_type,
        state=state,
        idempotency_key=f"{parent_run_id}:{item_id}:{operation_type}:{attempt_kind}",
        parent_run_id=parent_run_id,
        request_payload=build_request_payload(
            DispatchPayload("content_item", str(item_id), "content", (str(item_id),))
        ),
        attempt_count=1,
        total_count=1,
        success_count=success,
        failure_count=failure,
        skipped_count=int(state == OperationRunState.CANCELLED),
        result_summary={"items": {str(item_id): result}},
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        version=1,
    )
    try:
        # A later notification failure can revisit this already committed outcome.
        # Preserve the first fact and keep the caller's transaction usable.
        with db.begin_nested():
            db.add(child)
            db.flush()
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != (
            "uq_operation_runs_idempotency_scope"
        ):
            raise
        existing = db.scalar(select(OperationRun).where(
            OperationRun.requested_by_id.is_(None),
            OperationRun.hospital_id == hospital_id,
            OperationRun.parent_run_id == parent_run_id,
            OperationRun.operation_type == operation_type,
            OperationRun.idempotency_key == child.idempotency_key,
        ))
        if existing is None:
            raise
        db.commit()
        return existing
    db.commit()
    return child
