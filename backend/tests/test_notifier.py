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


class _RejectedWebhookClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json):
        return httpx.Response(410, request=httpx.Request("POST", url), text="revoked")


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


async def test_slack_http_failure_logs_safe_status_code_only(monkeypatch, caplog):
    webhook_url = "https://hooks.slack.com/services/T000/B000/super-secret-token"
    monkeypatch.setattr(notifier.settings, "SLACK_WEBHOOK_URL", webhook_url)
    monkeypatch.setattr(notifier.httpx, "AsyncClient", _RejectedWebhookClient)

    with caplog.at_level(logging.ERROR, logger="app.services.notifier"):
        sent = await notifier._send("hello")

    assert sent is False
    assert "status=410" in caplog.text
    assert webhook_url not in caplog.text
    assert "revoked" not in caplog.text


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
    assert "Gemini" in body
    assert "그라운디드" not in body


async def test_monthly_report_adds_new_mention_line_when_present(monkeypatch):
    captured = _capture_send(monkeypatch)
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, 42.0, 12.0, pdf_path="gs://x.pdf",
        platforms=["chatgpt"], new_mention_count=3,
        first_measured_mention_count=2, non_comparable_count=1,
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "지난달과 같은 기준으로 새로 확인된 질문: *3건*" in body
    assert "이번 달 처음 확인되어 새 언급으로 계산하지 않은 질문: *2건*" in body
    assert "지난달 측정이 끝나지 않아 비교에서 제외한 질문: *1건*" in body
    assert "고객 영향:" in body
    assert "지금 할 일:" in body
    assert "https://admin.example.test/operations?queue=REPORTS" in body
    assert "gs://x.pdf" not in body
    assert "NEW_MENTION" not in body
    assert "NON_COMPARABLE" not in body
    assert "분모" not in body
    assert "AI 답변 인용 리포트" not in body
    assert "AI 답변 언급 리포트" in body
    for jargon in ["Responses API", "gpt-4o", "그라운디드", "미상", "pdf_path"]:
        assert jargon not in body


async def test_monthly_report_omits_new_mention_line_when_zero(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, 42.0, 12.0, "gs://x.pdf",
        platforms=["chatgpt"], new_mention_count=0,
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "지난달과 같은 기준으로 새로 확인된 질문" not in body


async def test_monthly_report_uses_plain_fallback_for_unknown_platform(monkeypatch):
    captured = _capture_send(monkeypatch)

    await notifier.notify_monthly_report_ready(
        "장편한외과의원", 2026, 7, 42.0, None, "gs://x.pdf",
        platforms=["provider_internal_code"],
    )

    body = captured["blocks"][0]["text"]["text"]
    assert "측정한 AI 서비스 확인 필요" in body
    assert "provider_internal_code" not in body


async def test_lead_diagnosis_intake_message_is_actionable_and_omits_pii(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_lead_diagnosis_received(
        clinic_name="장편한외과의원",
        clinic_type="외과",
        region="수서역",
        keywords=["대장내시경", "치질"],
        contact="010-1234-5678",
        email="doctor@example.com",
        slot_no=4,
        admin_url="https://admin.example.test/leads",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "무료 AI 노출 진단 접수" in body
    assert "장편한외과의원" in body
    assert "무슨 문제인지:" in body
    assert "고객 영향:" in body
    assert "지금 할 일:" in body
    assert "처리 기한:" in body
    assert "외과 · 수서역" not in body
    assert "대장내시경" not in body
    assert "010-1234-5678" not in body
    assert "doctor@example.com" not in body
    assert "오늘 4번째 접수" in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_content_generation_digest_combines_hospitals_into_one_message(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_content_generation_digest(
        scheduled_date="2026-08-10",
        entries=[
            {
                "hospital_name": "장편한외과의원",
                "generated": 2,
                "failed": 0,
                "skipped": 0,
                "cost_blocked": 0,
                "discarded": 0,
                "image_missing": 0,
            },
            {
                "hospital_name": "행복드림의원",
                "generated": 0,
                "failed": 0,
                "skipped": 1,
                "cost_blocked": 0,
                "discarded": 0,
                "image_missing": 0,
            },
        ],
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "야간 콘텐츠 준비 결과" in body
    assert "2개 병원 · 3건" in body
    assert "*장편한외과의원* — 초안 저장 완료 2" in body
    assert "*행복드림의원* — 콘텐츠 운영 기준 승인 대기 1" in body
    assert all(
        label in body
        for label in ("무슨 문제인지", "고객 영향", "지금 할 일", "처리 기한")
    )
    assert len(captured["blocks"]) == 2
    assert captured["blocks"][1]["elements"][0]["url"].endswith(
        "/operations?queue=TODAY"
    )


async def test_content_missed_digest_combines_hospitals(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_content_missed_digest(
        entries=[
            {"hospital_name": "장편한외과의원", "missed_count": 2, "dates": ["2026-08-04", "2026-08-07"]},
            {"hospital_name": "행복드림의원", "missed_count": 1, "dates": ["2026-08-07"]},
        ],
        admin_url="https://admin.example.test/hospitals",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "아침 검수 대기 요약" in body
    assert "2개 병원 · 3건" in body
    assert "*장편한외과의원* — 2건" in body
    assert "*행복드림의원* — 1건" in body


async def test_auto_publish_block_digest_hides_reasons_and_has_one_admin_action(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_auto_publish_block_digest(
        entries=[
            {
                "hospital_name": "장편한외과의원",
                "title": "치질 진료 안내",
                "scheduled_date": "2026-08-10",
                "reason": "운영 기준 검사를 통과하지 못했습니다.",
            },
            {
                "hospital_name": "행복드림의원",
                "title": "내시경 전 준비",
                "scheduled_date": "2026-08-10",
                "reason": "의료광고 금지 표현이 확인됐습니다.",
            },
        ],
        admin_url="https://admin.example.test/hospitals",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "발행 차단 요약" in body
    assert "장편한외과의원" in body
    assert "행복드림의원" in body
    assert "운영 기준 검사" not in body
    assert "의료광고 금지 표현" not in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_auto_publish_digest_combines_successes_into_one_message(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_content_auto_publish_digest(
        entries=[
            {
                "hospital_name": "장편한외과의원",
                "title": "치질 진료 안내",
                "sequence_no": 3,
                "total_count": 12,
            },
            {
                "hospital_name": "행복드림의원",
                "title": "내시경 전 준비",
                "sequence_no": 1,
                "total_count": 16,
            },
        ],
        admin_url="https://admin.example.test/hospitals",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "오늘 발행 요약" in body
    assert "2개 병원 · 2건" in body
    assert "장편한외과의원" in body
    assert "12편 중 3번째" in body
    assert "행복드림의원" in body
    assert body.count("Admin에서 공개 내용 확인") == 1


async def test_auto_publish_digest_reports_only_exceptions_as_human_work(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_content_auto_publish_digest(
        entries=[
            {
                "hospital_name": "자동완료의원",
                "title": "진료 안내",
                "sequence_no": 2,
                "total_count": 8,
                "automatic_remediation_attempts": 1,
            }
        ],
        blocked_entries=[
            {
                "hospital_name": "근거확인의원",
                "scheduled_date": "2026-08-12",
                "reason": "승인된 근거 자료가 없습니다.",
            }
        ],
        admin_url="https://admin.example.test/operations?queue=TODAY",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "자동 검수 후 보완: *1건*" in body
    assert "사람 확인 필요: *1건*" in body
    assert "근거확인의원" in body
    assert "승인된 근거 자료가 없습니다" in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_post_publish_review_overdue_escalates_only_the_sample_queue(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_post_publish_review_overdue(
        entries=[
            {
                "hospital_name": "장편한외과의원",
                "title": "자동 보완된 진료 안내",
            }
        ],
        admin_url="https://admin.example.test/operations?queue=TODAY&sla=OVERDUE",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "공개 후 표본 확인 지연" in body
    assert "24시간" in body
    assert "즉시 비공개 후 재생성" in body
    assert captured["blocks"][1]["type"] == "actions"


async def test_zero_pii_purge_does_not_send_daily_noise(monkeypatch):
    async def should_not_send(*_args, **_kwargs):
        raise AssertionError("zero-work purge must stay in logs instead of Slack")

    monkeypatch.setattr(notifier, "_send", should_not_send)
    assert await notifier.notify_lead_purge_result(purged=0) is False


async def test_naver_asset_digest_combines_hospitals(monkeypatch):
    captured = _capture_send(monkeypatch)

    sent = await notifier.notify_naver_assets_digest(
        entries=[
            {"hospital_name": "장편한외과의원", "created": 7, "requested": 15},
            {"hospital_name": "행복드림의원", "created": 5, "requested": 15},
        ],
        admin_url="https://admin.example.test/hospitals",
    )

    assert sent is True
    body = captured["blocks"][0]["text"]["text"]
    assert "네이버 자산 주간 요약" in body
    assert "2개 병원 · 신규 12건" in body
    assert "*장편한외과의원* — 신규 7건" in body
    assert "*행복드림의원* — 신규 5건" in body
