"""Closed durable payload contract for OperationRun dispatch and retry."""

import re
from dataclasses import dataclass
from typing import assert_never

from app.models.operations import JSONValue

_SAFE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")
_SAFE_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)


@dataclass(frozen=True, slots=True)
class DispatchPayload:
    target_type: str
    target_id: str
    queue: str
    task_args: tuple[JSONValue, ...]


@dataclass(frozen=True, slots=True)
class UnsafeDispatchPayload(Exception):
    field: str

    def __str__(self) -> str:
        return f"dispatch payload field {self.field} is not allowlisted"


def build_request_payload(dispatch: DispatchPayload) -> dict[str, JSONValue]:
    safe = _safe_dispatch(dispatch)
    return {
        "source_type": safe.target_type,
        "source_id": safe.target_id,
        "_dispatch": {
            "target_type": safe.target_type,
            "target_id": safe.target_id,
            "queue": safe.queue,
            "task_args": list(safe.task_args),
        },
    }


def parse_stored_dispatch(value: JSONValue) -> DispatchPayload:
    match value:
        case dict() as stored:
            return _safe_dispatch(
                DispatchPayload(
                    target_type=_stored_text(stored.get("target_type"), "target_type"),
                    target_id=_stored_text(stored.get("target_id"), "target_id"),
                    queue=_stored_text(stored.get("queue"), "queue"),
                    task_args=_stored_args(stored.get("task_args")),
                )
            )
        case None | str() | int() | float() | bool() | list():
            raise UnsafeDispatchPayload("_dispatch")
        case unreachable:
            assert_never(unreachable)


def _safe_dispatch(dispatch: DispatchPayload) -> DispatchPayload:
    if not _SAFE_CODE.fullmatch(dispatch.target_type):
        raise UnsafeDispatchPayload("target_type")
    if not _SAFE_UUID.fullmatch(dispatch.target_id):
        raise UnsafeDispatchPayload("target_id")
    if not _SAFE_CODE.fullmatch(dispatch.queue):
        raise UnsafeDispatchPayload("queue")
    return DispatchPayload(
        dispatch.target_type,
        dispatch.target_id,
        dispatch.queue,
        tuple(_safe_arg(value, index) for index, value in enumerate(dispatch.task_args)),
    )


def _safe_arg(value: JSONValue, index: int) -> JSONValue:
    match value:
        case None | bool() | int():
            return value
        case str() if _SAFE_UUID.fullmatch(value):
            return value
        case str() | float() | list() | dict():
            raise UnsafeDispatchPayload(f"task_args[{index}]")
        case unreachable:
            assert_never(unreachable)


def _stored_text(value: JSONValue, field: str) -> str:
    match value:
        case str():
            return value
        case None | int() | float() | bool() | list() | dict():
            raise UnsafeDispatchPayload(field)
        case unreachable:
            assert_never(unreachable)


def _stored_args(value: JSONValue) -> tuple[JSONValue, ...]:
    match value:
        case list() as values:
            return tuple(values)
        case None | str() | int() | float() | bool() | dict():
            raise UnsafeDispatchPayload("task_args")
        case unreachable:
            assert_never(unreachable)
