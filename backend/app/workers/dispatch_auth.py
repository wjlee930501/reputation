"""Validate authenticated Celery dispatch envelopes before task execution."""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from celery import Task
from celery.exceptions import Ignore

from app.core.config import settings
from app.workers.dispatch_envelope import (
    ARGS_DIGEST_HEADER,
    CLOCK_SKEW_SECONDS,
    DISPATCH_TTL_SECONDS,
    EXPIRES_HEADER,
    GLOBAL_TARGET,
    ISSUED_HEADER,
    OPERATION_RUN_HEADER,
    PURPOSE_HEADER,
    RELEASE_HEADER,
    RETRIES_HEADER,
    SIGNATURE_HEADER,
    TARGET_HEADER,
    TASK_ID_HEADER,
    args_digest,
    expected_purpose,
    expected_target,
    is_protected_task,
    release_revision,
    signature,
    signed_header_names,
)
from app.workers.dispatch_envelope import (
    build_dispatch_headers as build_dispatch_headers,
)
from app.workers.dispatch_envelope import (
    stamp_dispatch_headers as stamp_dispatch_headers,
)
from app.workers.dispatch_envelope import (
    stamp_published_message as stamp_published_message,
)

logger = logging.getLogger(__name__)


class DispatchAuthorizationError(PermissionError):
    """The broker message was not created by an authorized server process."""


# Cloud Run updates worker before beat so the two revisions intentionally overlap. Messages
# signed by the previous release stay trusted for the whole envelope lifetime: a message that
# waited in a backlog or was restored by the broker during the rollout is still legitimate.
# 15분이던 때는 적체·재배달된 이전 릴리스 메시지가 서명이 맞아도 거절됐다. 서명·task id·
# 인자·목적·대상 검사는 그대로이고, 교차 릴리스 재생은 봉투 만료가 막는다.
RELEASE_HANDOFF_GRACE_SECONDS = DISPATCH_TTL_SECONDS


class _Request(Protocol):
    headers: Mapping[str, str] | None
    id: str
    retries: int


class DispatchTask(Protocol):
    request: _Request


def validate_task_dispatch(
    *,
    task_name: str,
    task_id: str,
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    retries: int,
    headers: Mapping[str, Any] | None,
    now: int | None = None,
) -> None:
    if settings.APP_ENV.lower() != "production" or not is_protected_task(task_name):
        return
    if not isinstance(headers, Mapping):
        raise DispatchAuthorizationError("missing authenticated dispatch envelope")
    observed = {key: headers.get(key) for key in signed_header_names()}
    if not all(isinstance(value, str) and value for value in observed.values()):
        raise DispatchAuthorizationError("incomplete authenticated dispatch envelope")
    current = int(time.time() if now is None else now)
    issued_at = _integer_header(observed[ISSUED_HEADER])
    expires_at = _integer_header(observed[EXPIRES_HEADER])
    if current > expires_at:
        raise DispatchAuthorizationError("expired authenticated dispatch envelope")
    # 수명은 서명된 두 값의 차이라 위조할 수 없다. '정확히 같음'이면 TTL을 바꾸는 배포마다
    # 이전 릴리스가 서명한 메시지가 전부 거절되므로 상한만 건다.
    lifetime = expires_at - issued_at
    if issued_at > current + CLOCK_SKEW_SECONDS or not 0 < lifetime <= DISPATCH_TTL_SECONDS:
        raise DispatchAuthorizationError("invalid authenticated dispatch lifetime")
    observed_release = str(observed[RELEASE_HEADER])
    current_release = release_revision()
    if (
        observed_release != current_release
        and current - issued_at > RELEASE_HANDOFF_GRACE_SECONDS
    ):
        raise DispatchAuthorizationError("authenticated dispatch release handoff expired")
    expected = {
        PURPOSE_HEADER: expected_purpose(task_name),
        TARGET_HEADER: expected_target(task_name, args),
        TASK_ID_HEADER: task_id,
        RETRIES_HEADER: str(retries),
        ISSUED_HEADER: str(issued_at),
        EXPIRES_HEADER: str(expires_at),
        # Validate the publisher's signed release value rather than rewriting it to the
        # consumer's value. A signature made for another release cannot be forged or edited,
        # and the bounded handoff check above prevents indefinite cross-release replay.
        RELEASE_HEADER: observed_release,
        ARGS_DIGEST_HEADER: args_digest(args, kwargs),
        OPERATION_RUN_HEADER: str(headers.get("operation_run_id") or "-"),
    }
    if any(headers.get(key) != value for key, value in expected.items()):
        raise DispatchAuthorizationError("authenticated dispatch context changed")
    observed_signature = headers.get(SIGNATURE_HEADER)
    if not isinstance(observed_signature, str) or not hmac.compare_digest(
        observed_signature, signature(task_name, expected)
    ):
        raise DispatchAuthorizationError("invalid authenticated dispatch signature")


def require_dispatch(
    task: DispatchTask,
    purpose: str,
    target_id: str | None = None,
    *,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
    now: int | None = None,
) -> None:
    """Preserve task-local purpose checks in addition to the global task base."""
    if settings.APP_ENV.lower() != "production":
        return
    headers = task.request.headers
    if not isinstance(headers, Mapping):
        raise DispatchAuthorizationError("missing authenticated dispatch envelope")
    if headers.get(PURPOSE_HEADER) != purpose or headers.get(TARGET_HEADER) != (
        target_id or GLOBAL_TARGET
    ):
        raise DispatchAuthorizationError("dispatch purpose or target changed")
    task_name = str(getattr(task, "name", ""))
    if task_name and args is not None:
        validate_task_dispatch(
            task_name=task_name,
            task_id=str(task.request.id),
            args=args,
            kwargs=kwargs or {},
            retries=int(task.request.retries),
            headers=headers,
            now=now,
        )


class AuthenticatedTask(Task):
    """Celery task base that fails closed before any worker body executes."""

    abstract = True

    def before_start(self, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        validate_task_dispatch(
            task_name=self.name,
            task_id=task_id,
            args=args,
            kwargs=kwargs,
            retries=int(self.request.retries or 0),
            headers=self.request.headers,
        )
        from app.workers import operation_run_signals

        # 실행 claim은 봉투 검증을 통과한 사본만 한다. task_prerun에서 claim하던 때는 배포 뒤
        # 만료된 채 되돌아온 사본이 먼저 실행을 RUNNING으로 가져가고, 검증 실패의
        # task_failure가 그 실행을 FAILED로 끝냈다 — 자동 복구는 FAILED를 다시 보내지 않는다.
        operation_run_signals.track_operation_prerun(task_id=task_id, task=self)
        if settings.APP_ENV.lower() != "production":
            return
        from app.core.database import SyncSessionLocal
        from app.workers.generation_run_control import (
            DispatchVerdict,
            operation_run_dispatch_verdict,
            operation_run_required,
        )

        headers = self.request.headers
        has_operation_run = isinstance(headers, Mapping) and bool(
            headers.get("operation_run_id")
        )
        if not has_operation_run and not operation_run_required(self.name):
            return
        with SyncSessionLocal() as db:
            verdict, reason = operation_run_dispatch_verdict(db, self, self.name, args)
        if verdict is DispatchVerdict.AUTHORIZED:
            return
        if verdict is DispatchVerdict.STALE:
            # 실행은 이미 다른(더 새) 사본이 가져갔거나 끝났다. 큐 적체·재배달의 정상적인
            # 결과이므로 실패가 아니다. Ignore는 task_failure를 내지 않아 사고가 열리지 않는다.
            # 진짜 소유권 버그를 볼 수 있게 사유를 남긴다.
            logger.info(
                "dispatch_skipped reason=%s task_name=%s task_id=%s operation_run_id=%s",
                reason,
                self.name,
                task_id,
                headers.get("operation_run_id") if isinstance(headers, Mapping) else None,
            )
            raise Ignore()
        raise DispatchAuthorizationError(
            f"task is not authorized by the claimed operation run ({reason})"
        )


def _integer_header(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise DispatchAuthorizationError("invalid authenticated dispatch timestamp") from exc
