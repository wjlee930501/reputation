"""Slack 웹훅 생존 탐침과 채널 단위 실패 분류 — DB 없이 확인한다."""

from __future__ import annotations

import httpx
import pytest

from app.models.operations import NotificationOutboxState
from app.services.notification_messages import payload_with_routing_marker, with_routing_marker
from app.services.notification_transport import (
    TransportDecision,
    channel_is_dead,
    classify_webhook_probe,
    probe_webhook,
)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, "no_text", "alive"),
        (400, "invalid_payload", "alive"),
        (400, "missing_text\n", "alive"),
        (302, "", "dead"),
        (301, "", "dead"),
        (403, "invalid_token", "dead"),
        (404, "no_service", "dead"),
        (410, "channel_is_archived", "dead"),
        (400, "invalid_blocks", "unknown"),
        (500, "", "unknown"),
        (200, "ok", "unknown"),
    ],
)
def test_probe_classification(status: int, body: str, expected: str) -> None:
    assert classify_webhook_probe(status, body) == expected


@pytest.mark.asyncio
async def test_probe_posts_an_empty_body_and_never_follows_redirects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://hooks.slack.com/elsewhere"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert await probe_webhook(client, "https://hooks.slack.com/services/T/B/X") == "dead"
        assert await probe_webhook(client, "") == "not_configured"
        assert await probe_webhook(client, "https://evil.example.test/x") == "dead"

    assert len(seen) == 1
    assert seen[0].content == b"{}"


@pytest.mark.asyncio
async def test_probe_network_failure_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await probe_webhook(client, "https://hooks.slack.com/services/T/B/X") == "unknown"


@pytest.mark.parametrize(
    "code,status,dead",
    [
        ("WEBHOOK_URL_REJECTED", 302, True),
        ("WEBHOOK_NOT_CONFIGURED", None, True),
        ("SLACK_PERMANENT_ERROR", 404, True),
        ("SLACK_PERMANENT_ERROR", 403, True),
        # 본문 하나가 거절된 400은 그 알림의 일이다 — 채널 전체를 죽었다고 보지 않는다.
        ("SLACK_PERMANENT_ERROR", 400, False),
        ("SLACK_SERVER_ERROR", 500, False),
        ("DELIVERY_OUTCOME_UNKNOWN", None, False),
    ],
)
def test_only_webhook_level_failures_mark_a_channel_dead(code, status, dead) -> None:
    decision = TransportDecision(
        NotificationOutboxState.FAILED,
        code,
        {"http_status": status} if status else None,
    )
    assert channel_is_dead(decision) is dead


def test_routing_marker_goes_right_after_the_label_once() -> None:
    labelled = "[Error : 오류 발생] [조치 필요] 제목"
    assert with_routing_marker(labelled, "[개발 확인]") == "[Error : 오류 발생] [개발 확인] [조치 필요] 제목"
    once = with_routing_marker(labelled, "[개발 확인]")
    assert with_routing_marker(once, "[개발 확인]") == once
    assert with_routing_marker("라벨 없는 글", "[채널 대체 전송]") == "[채널 대체 전송] 라벨 없는 글"


def test_routing_marker_keeps_the_header_within_the_slack_limit() -> None:
    header = "[Report : 운영 현황 보고] " + "가" * 130
    payload = {
        "text": header,
        "blocks": [{"type": "header", "text": {"type": "plain_text", "text": header}}],
    }

    marked = payload_with_routing_marker(payload, "[채널 대체 전송]")

    assert len(marked["blocks"][0]["text"]["text"]) == 150
    assert marked["blocks"][0]["text"]["text"].startswith("[Report : 운영 현황 보고] [채널 대체 전송]")
    assert payload["blocks"][0]["text"]["text"] == header  # 원본은 그대로
