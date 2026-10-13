"""도입문의 접수 안내 문자(NHN Cloud SMS) — 요청 형태, 응답 판정, 리드 기록."""

from types import SimpleNamespace

import httpx

from app.services import inquiry_sms


def _configure(monkeypatch, **overrides):
    values = {
        "INQUIRY_SMS_PROVIDER": "nhn",
        "NHN_SMS_APP_KEY": "app-key",
        "NHN_SMS_SECRET_KEY": "secret-key",
        "NHN_SMS_API_BASE": "https://api-sms.cloud.toast.com",
        "INQUIRY_SMS_SENDER_NO": "010-2492-8543",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(inquiry_sms.settings, name, value)


class FakeDB:
    def __init__(self, recent_count: int = 0):
        self.recent_count = recent_count
        self.queried = False

    async def scalar(self, stmt):
        self.queried = True
        return self.recent_count


def _lead(contact="010-1234-5678"):
    return SimpleNamespace(
        id="lead-1", contact=contact, ack_sms_status=None, ack_sms_error=None, ack_sms_sent_at=None
    )


def test_body_is_the_approved_message_and_not_an_advertisement():
    assert inquiry_sms.ACK_SMS_BODY.startswith(
        "원장님, 안녕하세요. 모션랩스 마케팅팀 팀장 김효진입니다.\nRe:putation 도입 문의 주셔서 감사드립니다."
    )
    assert inquiry_sms.ACK_SMS_BODY.endswith("진료 일정 고려하셔서 편하신 시간 회신 주시면 맞춰서 방문하겠습니다.")
    assert "(광고)" not in inquiry_sms.ACK_SMS_BODY
    # NHN LMS 본문 한도 2,000바이트(EUC-KR). 한글 2바이트 기준으로 여유가 있어야 한다.
    assert len(inquiry_sms.ACK_SMS_BODY.encode("euc-kr")) < 1900
    assert len(inquiry_sms.ACK_SMS_TITLE) <= 40


def test_mobile_normalisation():
    assert inquiry_sms.normalize_korean_mobile("010-1234-5678") == "01012345678"
    assert inquiry_sms.normalize_korean_mobile("+82 10-1234-5678") == "01012345678"
    assert inquiry_sms.normalize_korean_mobile("010-2492-8543") == "01024928543"
    assert inquiry_sms.normalize_korean_mobile("02-1234-5678") is None
    assert inquiry_sms.normalize_korean_mobile("director@example.com") is None
    assert inquiry_sms.normalize_korean_mobile(None) is None


def test_provider_must_be_fully_configured(monkeypatch):
    _configure(monkeypatch, INQUIRY_SMS_PROVIDER="")
    assert inquiry_sms.provider_configured() == "INQUIRY_SMS_PROVIDER is not set"
    _configure(monkeypatch, INQUIRY_SMS_PROVIDER="solapi")
    assert "unsupported" in inquiry_sms.provider_configured()
    _configure(monkeypatch, NHN_SMS_SECRET_KEY="")
    assert "NHN_SMS_APP_KEY" in inquiry_sms.provider_configured()
    _configure(monkeypatch, INQUIRY_SMS_SENDER_NO="02-123-4567")
    assert "INQUIRY_SMS_SENDER_NO" in inquiry_sms.provider_configured()
    _configure(monkeypatch)
    assert inquiry_sms.provider_configured() is None


def test_nhn_request_targets_the_mms_endpoint_with_the_registered_sender(monkeypatch):
    _configure(monkeypatch)
    url, headers, payload = inquiry_sms.build_nhn_request(to="01012345678")

    assert url == "https://api-sms.cloud.toast.com/sms/v3.0/appKeys/app-key/sender/mms"
    assert headers["X-Secret-Key"] == "secret-key"
    assert payload["sendNo"] == "01024928543"
    assert payload["recipientList"] == [{"recipientNo": "01012345678"}]
    assert payload["title"] == inquiry_sms.ACK_SMS_TITLE
    assert payload["body"] == inquiry_sms.ACK_SMS_BODY


def test_nhn_response_parsing_distinguishes_transport_header_and_recipient_failures():
    ok = {
        "header": {"isSuccessful": True, "resultCode": 0, "resultMessage": "SUCCESS"},
        "body": {"data": {"requestId": "req-1", "sendResultList": [{"recipientNo": "x", "resultCode": 0}]}},
    }
    assert inquiry_sms.parse_nhn_response(200, ok) == inquiry_sms.AckSmsOutcome("SENT", None, "req-1")

    assert inquiry_sms.parse_nhn_response(500, ok).status == "FAILED"
    assert inquiry_sms.parse_nhn_response(200, "junk").status == "FAILED"

    header_fail = {"header": {"isSuccessful": False, "resultCode": -1, "resultMessage": "인증 실패"}}
    assert inquiry_sms.parse_nhn_response(200, header_fail).detail == "-1 인증 실패"

    recipient_fail = {
        "header": {"isSuccessful": True},
        "body": {"data": {"requestId": "req-2", "sendResultList": [
            {"recipientNo": "x", "resultCode": -1002, "resultMessage": "발신번호 미등록"}
        ]}},
    }
    outcome = inquiry_sms.parse_nhn_response(200, recipient_fail)
    assert outcome.status == "FAILED"
    assert outcome.detail == "recipient -1002 발신번호 미등록"
    assert outcome.request_id == "req-2"


class _FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._error:
            raise self._error
        return self._response


async def test_acknowledge_sends_once_and_records_the_outcome_on_the_lead(monkeypatch):
    _configure(monkeypatch)
    client = _FakeClient(
        response=httpx.Response(
            200,
            json={
                "header": {"isSuccessful": True},
                "body": {"data": {"requestId": "req-9", "sendResultList": [{"resultCode": 0}]}},
            },
        )
    )
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), lead)

    assert outcome.sent
    assert lead.ack_sms_status == "SENT"
    assert lead.ack_sms_error is None
    assert lead.ack_sms_sent_at is not None
    assert client.calls[0]["json"]["recipientList"] == [{"recipientNo": "01012345678"}]


async def test_acknowledge_records_provider_failure_without_raising(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _FakeClient(error=httpx.ConnectTimeout("timeout"))
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), lead)

    assert outcome.status == "FAILED"
    assert lead.ack_sms_status == "FAILED"
    assert lead.ack_sms_error == "ConnectTimeout"
    assert lead.ack_sms_sent_at is None


async def test_acknowledge_skips_when_unconfigured_or_contact_is_not_mobile(monkeypatch):
    _configure(monkeypatch, INQUIRY_SMS_PROVIDER="")
    db = FakeDB()
    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(db, lead)
    assert outcome.status == "SKIPPED"
    assert lead.ack_sms_status == "SKIPPED"
    assert db.queried is False  # 설정이 없으면 DB도 사업자도 건드리지 않는다

    _configure(monkeypatch)
    lead = _lead(contact="director@example.com")
    outcome = await inquiry_sms.acknowledge_inquiry(db, lead)
    assert outcome.status == "SKIPPED"
    assert "mobile" in lead.ack_sms_error


async def test_acknowledge_suppresses_a_repeat_from_the_same_contact(monkeypatch):
    _configure(monkeypatch)
    client = _FakeClient(response=httpx.Response(200, json={"header": {"isSuccessful": True}}))
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(recent_count=1), lead)

    assert outcome.status == "SKIPPED"
    assert lead.ack_sms_status == "SKIPPED"
    assert client.calls == []


class _SequenceClient(_FakeClient):
    def __init__(self, outcomes):
        super().__init__()
        self._outcomes = list(outcomes)

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok():
    return httpx.Response(
        200,
        json={"header": {"isSuccessful": True}, "body": {"data": {"sendResultList": [{"resultCode": 0}]}}},
    )


async def test_a_connect_failure_is_retried_once_and_can_succeed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.ConnectError("refused"), _ok()])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.sent
    assert len(client.calls) == 2


async def test_a_read_timeout_is_not_retried_because_the_text_may_have_gone_out(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.ReadTimeout("slow"), _ok()])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), lead)

    assert outcome.status == "FAILED"
    assert outcome.unconfirmed
    assert inquiry_sms.delivery_unconfirmed(lead.ack_sms_error)
    assert len(client.calls) == 1


async def test_a_503_is_not_retried_and_is_recorded_as_unconfirmed(monkeypatch):
    """사업자 멱등키가 없다 — 응답 경로의 503은 문자가 이미 나갔을 수 있어 다시 보내지 않는다(Codex R4-2)."""

    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.Response(503), _ok()])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), lead)

    assert outcome.status == "FAILED"
    assert outcome.unconfirmed
    assert len(client.calls) == 1
    assert lead.ack_sms_status == "FAILED"
    assert inquiry_sms.delivery_unconfirmed(lead.ack_sms_error)
    assert "HTTP 503" in lead.ack_sms_error
    assert "not resent" in lead.ack_sms_error


async def test_a_429_is_retried_once_because_the_provider_did_not_take_it(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.Response(429), _ok()])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.sent
    assert len(client.calls) == 2


async def test_a_definite_rejection_is_not_marked_unconfirmed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.Response(429), httpx.Response(429)])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    lead = _lead()
    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), lead)

    assert outcome.status == "FAILED"
    assert not outcome.unconfirmed
    assert lead.ack_sms_error == "HTTP 429"
    assert not inquiry_sms.delivery_unconfirmed(lead.ack_sms_error)


async def test_a_plain_500_is_not_retried(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.Response(500), _ok()])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.status == "FAILED"
    assert outcome.unconfirmed
    assert len(client.calls) == 1


async def test_an_unconfirmed_sms_incident_says_it_was_not_resent(monkeypatch):
    opened = []

    async def capture(_db, **kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(inquiry_sms, "open_ops_incident_in", capture)

    await inquiry_sms.open_failure_incident(object(), "lead-1", actor="public-inquiry", unconfirmed=True)
    await inquiry_sms.open_failure_incident(object(), "lead-2", actor="public-inquiry")

    unconfirmed, failed = opened
    assert unconfirmed["incident_type"] == failed["incident_type"] == "INQUIRY_SMS_FAILED"
    assert "확인하지 못했습니다" in unconfirmed["problem"]
    assert "재발송하지 않" in unconfirmed["customer_impact"]
    assert "보내지 못했습니다" in failed["problem"]
    assert "재발송" not in failed["customer_impact"]


async def test_the_rendered_sms_notice_never_contradicts_an_unconfirmed_delivery(monkeypatch):
    """공통 문구(제목·지금 할 일)는 중립이고, 수신 불명이면 상세가 '확인 못 함·재발송 안 함'을 말한다(Codex R5-2)."""

    import json
    import uuid

    from app.services.notification_contracts import IncidentSlackProjection
    from app.services.notification_messages import build_open_incident_notification

    opened = []

    async def capture(_db, **kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(inquiry_sms, "open_ops_incident_in", capture)
    await inquiry_sms.open_failure_incident(object(), "lead-1", actor="public-inquiry", unconfirmed=True)
    await inquiry_sms.open_failure_incident(object(), "lead-2", actor="public-inquiry")

    def rendered(kwargs) -> str:
        intent = build_open_incident_notification(
            IncidentSlackProjection(
                incident_id=uuid.uuid4(),
                hospital_name=kwargs["hospital_name"],
                severity="HIGH",
                customer_impact=kwargs["customer_impact"],
                next_action=kwargs["next_action"],
                admin_path=kwargs["admin_path"],
                owner_label="미지정",
                sla_label="확인 필요",
                problem=kwargs["problem"],
                incident_type=kwargs["incident_type"],
            ),
            "https://admin.example.com",
        )
        return json.dumps(intent.message.payload(), ensure_ascii=False)

    unconfirmed, failed = (rendered(kwargs) for kwargs in opened)

    for text in (unconfirmed, failed):
        assert "문자 전달 확인 필요" in text
        assert "직접 연락" in text
    # 수신 불명: 갔다고도 안 갔다고도 단정하지 않는다.
    assert "확인하지 못했습니다" in unconfirmed
    assert "재발송하지 않았습니다" in unconfirmed
    for asserted in ("가지 않았습니다", "보내지 못했습니다", "미발송", "받지 못했습니다."):
        assert asserted not in unconfirmed
    # 확정 실패: 보내지 못했다고 말한다.
    assert "보내지 못했습니다" in failed
    assert "재발송" not in failed
