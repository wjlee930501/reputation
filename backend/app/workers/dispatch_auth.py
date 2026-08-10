"""Validate authenticated Celery dispatch envelopes before task execution."""

from __future__ import annotations

import hmac
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from celery import Task

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


class DispatchAuthorizationError(PermissionError):
    """The broker message was not created by an authorized server process."""


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
    if issued_at > current + CLOCK_SKEW_SECONDS or expires_at - issued_at != DISPATCH_TTL_SECONDS:
        raise DispatchAuthorizationError("invalid authenticated dispatch lifetime")
    expected = {
        PURPOSE_HEADER: expected_purpose(task_name),
        TARGET_HEADER: expected_target(task_name, args),
        TASK_ID_HEADER: task_id,
        RETRIES_HEADER: str(retries),
        ISSUED_HEADER: str(issued_at),
        EXPIRES_HEADER: str(expires_at),
        RELEASE_HEADER: release_revision(),
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
        if settings.APP_ENV.lower() != "production":
            return
        from app.core.database import SyncSessionLocal
        from app.workers.generation_run_control import (
            operation_run_dispatch_authorized,
            operation_run_required,
        )

        headers = self.request.headers
        has_operation_run = isinstance(headers, Mapping) and bool(
            headers.get("operation_run_id")
        )
        if not has_operation_run and not operation_run_required(self.name):
            return
        with SyncSessionLocal() as db:
            if not operation_run_dispatch_authorized(db, self, self.name, args):
                raise DispatchAuthorizationError(
                    "task is not authorized by the claimed operation run"
                )


def _integer_header(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise DispatchAuthorizationError("invalid authenticated dispatch timestamp") from exc
