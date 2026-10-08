"""One-attempt Slack transport classification with sanitized provider facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, Literal

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


# 빈 본문 `{}`을 보냈을 때 살아 있는 Slack 웹훅이 돌려주는 거절 사유. 메시지를 만들지 않고도
# 주소가 살아 있는지 확인할 수 있다 — 302·403·404는 주소 자체가 죽었다는 뜻이다.
_ALIVE_PROBE_BODIES: Final = frozenset({"no_text", "invalid_payload", "missing_text"})
# 이 HTTP 상태는 메시지 하나가 아니라 웹훅(채널) 자체의 문제다 — 무효 토큰·없는/보관된 채널.
_DEAD_CHANNEL_STATUSES: Final = frozenset({403, 404, 410})
CHANNEL_UNAVAILABLE_CODE: Final = "SLACK_CHANNEL_UNAVAILABLE"

WebhookProbe = Literal["alive", "dead", "unknown", "not_configured"]


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
    if 300 <= response.status_code <= 399:
        # 리다이렉트는 Slack이 메시지를 받지 않았다는 뜻이다(httpx는 따라가지 않는다). 수신이
        # 불확실한 것이 아니라 웹훅 주소가 잘못된 설정 오류다 — 2026-09-20부터 개발 채널 웹훅이
        # 302를 돌려줘 알림마다 '수신 여부 확인' 사고가 열렸다.
        return TransportDecision(
            NotificationOutboxState.FAILED, "WEBHOOK_URL_REJECTED", provider, attempted=True
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


def channel_is_dead(decision: TransportDecision) -> bool:
    """이 결과가 메시지 하나가 아니라 웹훅 주소 전체가 죽었다는 증거인가.

    400(본문 거절)은 그 알림 하나의 일이다. 주소가 없거나 허용 밖이거나, 리다이렉트·
    403·404·410을 받으면 그 채널로는 어떤 알림도 닿지 않는다.
    """

    if decision.code in {"WEBHOOK_NOT_CONFIGURED", "WEBHOOK_URL_REJECTED"}:
        return True
    if decision.code != "SLACK_PERMANENT_ERROR":
        return False
    status = (decision.provider_response or {}).get("http_status")
    return isinstance(status, int) and status in _DEAD_CHANNEL_STATUSES


def classify_webhook_probe(status_code: int, body: str) -> WebhookProbe:
    """`{}` 탐침 응답 분류. 살아 있음 = 400 + 본문 누락 사유, 죽음 = 3xx·403·404·410."""

    if status_code == 400 and body.strip().lower() in _ALIVE_PROBE_BODIES:
        return "alive"
    if 300 <= status_code <= 399 or status_code in _DEAD_CHANNEL_STATUSES:
        return "dead"
    return "unknown"


async def probe_webhook(client: httpx.AsyncClient, webhook_url: str) -> WebhookProbe:
    """메시지를 남기지 않는 빈 POST로 웹훅이 살아 있는지 본다. 5xx·네트워크 오류는 모름이다."""

    if not webhook_url.strip():
        return "not_configured"
    if not notifier._is_allowed_webhook(webhook_url):
        return "dead"
    try:
        response = await client.post(
            webhook_url,
            json={},
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return "unknown"
    return classify_webhook_probe(response.status_code, response.text)


def probe_webhook_sync(webhook_url: str, *, timeout: float = 10.0) -> WebhookProbe:
    """준비 점검용 동기 탐침. 분류는 `probe_webhook`과 같다."""

    if not webhook_url.strip():
        return "not_configured"
    if not notifier._is_allowed_webhook(webhook_url):
        return "dead"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(webhook_url, json={})
    except httpx.HTTPError:
        return "unknown"
    return classify_webhook_probe(response.status_code, response.text)


def retry_delay(attempt_count: int, provider_delay: int | None) -> int:
    return provider_delay or min(15 * (2 ** max(0, attempt_count - 1)), _MAX_RETRY_SECONDS)


def safe_error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return {
        "DELIVERY_OUTCOME_UNKNOWN": "Slack 수신 여부를 확인한 뒤 수동으로 재시도해 주세요.",
        "DELIVERY_RETRY_EXHAUSTED": "Slack 설정과 상태를 확인한 뒤 수동으로 재시도해 주세요.",
        "DEV_WEBHOOK_MISSING": "개발팀 Slack Webhook 설정 후 알림을 수동 재시도해 주세요.",
        CHANNEL_UNAVAILABLE_CODE: "보낼 수 있는 Slack 채널이 없습니다. 채널이 복구되면 자동으로 다시 보냅니다.",
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
