"""One-attempt Slack transport classification with sanitized provider facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

import httpx

from app.models.operations import JSONValue, NotificationOutboxState
from app.services import notifier

_MAX_RETRY_SECONDS = 3600
_SLACK_BODY_CODES: Final = frozenset(
    {
        "ok",
        "invalid_payload",
        "user_not_found",
        "action_prohibited",
        "channel_not_found",
        "channel_is_archived",
        "rollup_error",
        "posting_to_general_channel_denied",
        "too_many_attachments",
        "no_text",
        "invalid_token",
        "no_service",
        "no_team",
        "team_disabled",
    }
)


@dataclass(frozen=True, slots=True)
class TransportDecision:
    state: NotificationOutboxState
    code: str | None
    provider_response: dict[str, JSONValue] | None
    retry_after_seconds: int | None = None
    attempted: bool = False


async def deliver_once(
    client: httpx.AsyncClient,
    webhook_url: str,
    payload: dict[str, JSONValue],
    now: datetime,
) -> TransportDecision:
    if not webhook_url:
        return TransportDecision(NotificationOutboxState.FAILED, "WEBHOOK_NOT_CONFIGURED", None)
    if not notifier._is_allowed_webhook(webhook_url):
        return TransportDecision(NotificationOutboxState.FAILED, "WEBHOOK_URL_REJECTED", None)
    try:
        response = await client.post(
            webhook_url,
            json=payload,
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
        )
    except (
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ):
        return TransportDecision(
            NotificationOutboxState.HOLD, "DELIVERY_OUTCOME_UNKNOWN", None, attempted=True
        )
    except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError):
        return TransportDecision(
            NotificationOutboxState.RETRYING, "SLACK_TRANSIENT_NETWORK", None, attempted=True
        )
    except httpx.HTTPError:
        return TransportDecision(
            NotificationOutboxState.HOLD, "DELIVERY_OUTCOME_UNKNOWN", None, attempted=True
        )
    provider: dict[str, JSONValue] = {
        "http_status": response.status_code,
        "body_code": _safe_body_code(response.text),
    }
    if response.status_code == 200 and response.content == b"ok":
        return TransportDecision(
            NotificationOutboxState.SENT, None, provider, attempted=True
        )
    if response.status_code == 429:
        return TransportDecision(
            NotificationOutboxState.RETRYING,
            "SLACK_RATE_LIMITED",
            provider,
            _retry_after(response.headers.get("Retry-After"), now),
            True,
        )
    if 500 <= response.status_code <= 599:
        return TransportDecision(
            NotificationOutboxState.RETRYING, "SLACK_SERVER_ERROR", provider, attempted=True
        )
    if 400 <= response.status_code <= 499:
        return TransportDecision(
            NotificationOutboxState.FAILED, "SLACK_PERMANENT_ERROR", provider, attempted=True
        )
    return TransportDecision(
        NotificationOutboxState.HOLD, "DELIVERY_OUTCOME_UNKNOWN", provider, attempted=True
    )


def retry_delay(attempt_count: int, provider_delay: int | None) -> int:
    return provider_delay or min(15 * (2 ** max(0, attempt_count - 1)), _MAX_RETRY_SECONDS)


def safe_error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return {
        "DELIVERY_OUTCOME_UNKNOWN": "Slack 수신 여부를 확인한 뒤 수동으로 재시도해 주세요.",
        "DELIVERY_RETRY_EXHAUSTED": "Slack 설정과 상태를 확인한 뒤 수동으로 재시도해 주세요.",
        "WEBHOOK_NOT_CONFIGURED": "Slack Webhook 설정을 확인해 주세요.",
        "WEBHOOK_URL_REJECTED": "허용된 Slack Webhook 주소인지 확인해 주세요.",
        "SLACK_PERMANENT_ERROR": "Slack 요청 구성을 확인해 주세요.",
        "SLACK_RATE_LIMITED": "Slack 제한이 해제되면 자동으로 재시도합니다.",
        "SLACK_SERVER_ERROR": "Slack 복구 후 자동으로 재시도합니다.",
        "SLACK_TRANSIENT_NETWORK": "네트워크 복구 후 자동으로 재시도합니다.",
    }.get(code, "Slack 알림 상태를 확인해 주세요.")


def _safe_body_code(body: str) -> str:
    candidate = body.strip().lower()
    return candidate if candidate in _SLACK_BODY_CODES else "unrecognized_response"


def _retry_after(value: str | None, now: datetime) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            return None
        seconds = int((parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
    return max(1, seconds)
