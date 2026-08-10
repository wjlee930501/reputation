"""Typed, operator-safe outcomes for each discovered Naver post."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import assert_never

from app.models.operations import JSONValue
from app.services.asset_extractor import naver_blog_post_hash, naver_blog_post_identity


class NaverHandoffState(StrEnum):
    PENDING = "PENDING"
    INGESTED = "INGESTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class InvalidNaverHandoffPayload(ValueError):
    field: str

    def __str__(self) -> str:
        return f"invalid Naver handoff {self.field}"


@dataclass(frozen=True, slots=True)
class NaverHandoffItem:
    url: str
    url_hash: str
    state: NaverHandoffState
    safe_error_code: str | None = None
    safe_error_message: str | None = None
    next_action: str | None = None
    source_id: uuid.UUID | None = None
    retry_of_run_id: uuid.UUID | None = None

    def payload(self) -> dict[str, JSONValue]:
        return {
            "url": self.url,
            "url_hash": self.url_hash,
            "state": self.state.value,
            "safe_error_code": self.safe_error_code,
            "safe_error_message": self.safe_error_message,
            "next_action": self.next_action,
            "source_id": str(self.source_id) if self.source_id else None,
            "retry_of_run_id": str(self.retry_of_run_id) if self.retry_of_run_id else None,
        }


@dataclass(frozen=True, slots=True)
class NaverHandoffResult:
    blog_id: str | None = None
    requested: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    skipped_empty: int = 0
    failed: tuple[str, ...] = ()
    items: tuple[NaverHandoffItem, ...] = ()
    source_ids: tuple[uuid.UUID, ...] = ()
    run_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    error: str | None = None


def pending_item(url: str, *, retry_of_run_id: uuid.UUID | None = None) -> NaverHandoffItem:
    canonical = naver_blog_post_identity(url)
    return NaverHandoffItem(
        url=canonical,
        url_hash=naver_blog_post_hash(canonical),
        state=NaverHandoffState.PENDING,
        retry_of_run_id=retry_of_run_id,
    )


def ingested_item(item: NaverHandoffItem, source_id: uuid.UUID) -> NaverHandoffItem:
    return replace(item, state=NaverHandoffState.INGESTED, source_id=source_id)


def skipped_item(
    item: NaverHandoffItem, code: str, message: str
) -> NaverHandoffItem:
    return replace(
        item,
        state=NaverHandoffState.SKIPPED,
        safe_error_code=code,
        safe_error_message=message,
        next_action="추가 조치가 필요하지 않습니다.",
    )


def failed_item(item: NaverHandoffItem, raw_error: str) -> NaverHandoffItem:
    code = "NAVER_HTTP_ERROR" if "HTTP " in raw_error.upper() else "NAVER_FETCH_FAILED"
    return replace(
        item,
        state=NaverHandoffState.FAILED,
        safe_error_code=code,
        safe_error_message="네이버 블로그 글을 가져오지 못해 근거 자료에 추가되지 않았습니다.",
        next_action=(
            "‘실패한 글 다시 수집’을 눌러 주세요. 다시 실패하면 작업 번호와 글 식별값을 "
            "개발팀에 전달해 주세요. 원문이나 환자 정보는 전달하지 마세요."
        ),
    )


def parse_item(payload: dict[str, JSONValue]) -> NaverHandoffItem:
    url = _required_text(payload.get("url"), "url")
    url_hash = _required_text(payload.get("url_hash"), "url_hash")
    state = NaverHandoffState(_required_text(payload.get("state"), "state"))
    source_id = _optional_uuid(payload.get("source_id"))
    retry_of_run_id = _optional_uuid(payload.get("retry_of_run_id"))
    return NaverHandoffItem(
        url=url,
        url_hash=url_hash,
        state=state,
        safe_error_code=_optional_text(payload.get("safe_error_code")),
        safe_error_message=_optional_text(payload.get("safe_error_message")),
        next_action=_optional_text(payload.get("next_action")),
        source_id=source_id,
        retry_of_run_id=retry_of_run_id,
    )


def _required_text(value: JSONValue, field: str) -> str:
    match value:
        case str() if value:
            return value
        case None | str() | int() | float() | bool() | list() | dict():
            raise InvalidNaverHandoffPayload(field)
        case unreachable:
            assert_never(unreachable)


def _optional_text(value: JSONValue) -> str | None:
    match value:
        case None:
            return None
        case str():
            return value
        case int() | float() | bool() | list() | dict():
            raise InvalidNaverHandoffPayload("text")
        case unreachable:
            assert_never(unreachable)


def _optional_uuid(value: JSONValue) -> uuid.UUID | None:
    text = _optional_text(value)
    return uuid.UUID(text) if text else None
