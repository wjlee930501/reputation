import logging

import httpx

from app.services import notifier


class FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        request = httpx.Request("POST", url)
        raise httpx.ConnectError(f"failed to connect to {url}", request=request)


class _ShouldNotPostClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):  # pragma: no cover - must never run
        raise AssertionError("disallowed webhook host should never be POSTed to")


async def test_send_rejects_non_allowlisted_webhook_host(monkeypatch, caplog):
    # SSRF/exfil 방어: 허용 호스트가 아니면 POST 자체를 하지 않는다 (EXT-1/V-013).
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "http://169.254.169.254/latest/meta-data")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _ShouldNotPostClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "allowlist" in caplog.text


async def test_send_rejects_lookalike_webhook_host(monkeypatch):
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com.evil.test/x")
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _ShouldNotPostClient)
    assert await notifier._send("hello") is False


def test_is_allowed_webhook_accepts_slack_only(monkeypatch):
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_ALLOWED_HOSTS", "hooks.slack.com")
    assert notifier._is_allowed_webhook("https://hooks.slack.com/services/T/B/x") is True
    assert notifier._is_allowed_webhook("http://hooks.slack.com/services/T/B/x") is False  # not https
    assert notifier._is_allowed_webhook("https://evil.test/x") is False


async def test_slack_failure_log_does_not_include_webhook_url(monkeypatch, caplog):
    webhook_url = "https://hooks.slack.com/services/T000/B000/super-secret-token"
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", webhook_url)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", FailingAsyncClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "ConnectError" in caplog.text
    assert webhook_url not in caplog.text
    assert "super-secret-token" not in caplog.text


# ── 측정 방식 라벨: 실제 사용 플랫폼 기준 동적 구성 (GEMINI_API_KEY 미설정 시 Gemini 제외) ──


def test_measurement_label_excludes_gemini_when_not_measured(monkeypatch):
    monkeypatch.setattr(notifier.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", False)
    label = notifier._measurement_label(["chatgpt"])
    assert "Gemini" not in label
    assert "OpenAI" in label


def test_measurement_label_includes_gemini_when_measured(monkeypatch):
    monkeypatch.setattr(notifier.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", True)
    label = notifier._measurement_label(["chatgpt", "gemini"])
    assert "Gemini 그라운디드" in label
    assert "웹검색" in label


def test_format_sov_distinguishes_none_from_zero():
    assert notifier._format_sov(None) == "측정 데이터 없음"
    assert notifier._format_sov(0.0) == "0.0%"


def _capture_send(monkeypatch):
    captured = {}

    async def fake_send(text, blocks=None):
        captured["text"] = text
        captured["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)
    return captured


async def test_v0_report_label_omits_gemini_when_only_chatgpt(monkeypatch):
    monkeypatch.setattr(notifier.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", False)
    captured = _capture_send(monkeypatch)

    await notifier.notify_v0_report_ready("장편한외과의원", 12.5, "gs://x.pdf", platforms=["chatgpt"])

    body = captured["blocks"][0]["text"]["text"]
    assert "Gemini" not in body
    assert "12.5%" in body


async def test_v0_report_shows_no_data_when_sov_none(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_v0_report_ready("장편한외과의원", None, "gs://x.pdf", platforms=["chatgpt"])

    body = captured["blocks"][0]["text"]["text"]
    assert "측정 데이터 없음" in body


async def test_monthly_report_shows_no_data_when_sov_none(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, None, None, "gs://x.pdf", platforms=["chatgpt", "gemini"]
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "측정 데이터 없음" in body
    assert "Gemini 그라운디드" in body


async def test_monthly_report_adds_new_mention_line_when_present(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, 42.0, 12.0, "gs://x.pdf",
        platforms=["chatgpt"], new_mention_count=3,
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "신규 언급 시작 쿼리: *3건*" in body


async def test_monthly_report_omits_new_mention_line_when_zero(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, 42.0, 12.0, "gs://x.pdf",
        platforms=["chatgpt"], new_mention_count=0,
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "신규 언급 시작 쿼리" not in body
