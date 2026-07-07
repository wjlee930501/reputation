"""Tests for public lead intake.

Single-actor model + PII masking + retention column. We bypass the slowapi
@limiter.limit decorator via __wrapped__ since unit tests don't run inside
the FastAPI request lifecycle.
"""
from types import SimpleNamespace

import pytest

from app.api.public import leads as leads_api
from app.services import notifier


# slowapi's @limiter.limit uses functools.wraps, so __wrapped__ is always
# present. Calling __wrapped__ bypasses the per-request rate-limit check that
# requires a real FastAPI app.state.limiter; rate-limit behavior itself is
# covered by the integration smoke (scripts/test_e2e.sh).
_create_lead = leads_api.create_lead.__wrapped__


class FakeRequest:
    def __init__(self, ip: str = "127.0.0.1", forwarded: str | None = None):
        headers = {}
        if forwarded:
            headers["x-forwarded-for"] = forwarded
        self.headers = SimpleNamespace(get=lambda key, default=None: headers.get(key.lower(), default))
        self.client = SimpleNamespace(host=ip)


class FakeDB:
    def __init__(self):
        self.added = []
        self.committed = False

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        item.id = "lead-id"
        item.created_at = None

    def add(self, item):
        self.added.append(item)


async def test_create_lead_persists_with_retention_and_consent(monkeypatch):
    notified = []

    async def fake_notify(**payload):
        notified.append(payload)
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
    monkeypatch.setattr(leads_api.settings, "LEAD_RETENTION_DAYS", 90)
    monkeypatch.setattr(leads_api.settings, "LEAD_CONSENT_VERSION", "v1.test")

    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="치질 수술 회복 기간은?",
        privacy=True,
        source_path="/",
    )
    response = await _create_lead(request=FakeRequest(forwarded="203.0.113.7"), body=body, db=db)

    assert response["ok"] is True
    lead = db.added[0]
    assert lead.privacy is True
    assert lead.consent_version == "v1.test"
    assert lead.consent_ip == "203.0.113.7"
    assert lead.retain_until is not None
    # Notifier receives the raw contact and masks it internally before sending to Slack.
    # The Slack-masking guarantee is asserted separately via test_mask_contact_*.
    assert notified[0]["contact"] == "010-0000-0000"
    assert lead.notification_status == "SENT"
    assert lead.notification_error is None


async def test_create_lead_records_notification_failure(monkeypatch):
    async def fake_notify(**payload):
        return False

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)

    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="치질 수술 회복 기간은?",
        privacy=True,
    )
    await _create_lead(request=FakeRequest(), body=body, db=db)

    assert db.added[0].notification_status == "FAILED"
    assert "Slack/webhook" in db.added[0].notification_error


async def test_create_lead_ignores_forwarded_ip_from_untrusted_remote(monkeypatch):
    async def fake_notify(**payload):
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)

    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="치질 수술 회복 기간은?",
        privacy=True,
    )
    await _create_lead(
        request=FakeRequest(ip="198.51.100.4", forwarded="203.0.113.7"),
        body=body,
        db=db,
    )

    assert db.added[0].consent_ip == "198.51.100.4"


async def test_create_lead_rejects_missing_privacy_consent():
    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="치질 수술 회복 기간은?",
        privacy=False,
    )
    with pytest.raises(leads_api.HTTPException) as exc:
        await _create_lead(request=FakeRequest(), body=body, db=db)
    assert exc.value.status_code == 400


async def test_create_lead_silently_drops_honeypot_filled():
    """봇이 honeypot website 필드를 채우면 silent 200으로 응답하고 DB에 저장하지 않음."""
    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="bot-clinic",
        clinic_type="bot-region",
        contact="bot@example.com",
        question="bot question",
        privacy=True,
        website="http://attacker.example.com",
    )
    response = await _create_lead(request=FakeRequest(), body=body, db=db)
    assert response["ok"] is True
    assert response["lead_id"] is None
    assert db.added == []


async def test_create_lead_silently_drops_url_honeypot_filled():
    """website 뿐 아니라 url honeypot이 채워져도 silent 200 + 저장 안 함 (#10)."""
    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="bot-clinic",
        clinic_type="bot-region",
        contact="bot@example.com",
        question="bot question",
        privacy=True,
        url="http://attacker.example.com",
    )
    response = await _create_lead(request=FakeRequest(), body=body, db=db)
    assert response["ok"] is True
    assert response["lead_id"] is None
    assert db.added == []


async def test_create_lead_ignores_blank_honeypot(monkeypatch):
    """공백만 든 honeypot은 정상 제출로 취급한다(정상 사용자 오탐 방지)."""
    async def fake_notify(**payload):
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)

    db = FakeDB()
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="치질 수술 회복 기간은?",
        privacy=True,
        website="   ",
        url="",
    )
    response = await _create_lead(request=FakeRequest(), body=body, db=db)
    assert response["lead_id"] is not None
    assert db.added and db.added[0].clinic_name == "장편한외과의원"


def test_lead_contact_format_validator():
    # 이메일 또는 전화번호 형식이 아니면 검증 실패
    with pytest.raises(ValueError):
        leads_api.LeadCreate(
            clinic_name="x",
            clinic_type="x",
            contact="invalid-no-format",
            question="x",
            privacy=True,
        )


def test_lead_question_rejects_patient_sensitive_free_text():
    blocked_questions = [
        "환자 홍길동 900101-1234567 수술 기록을 상담하고 싶습니다.",
        "환자 홍길동 9001011234567 수술 기록을 상담하고 싶습니다.",
        "환자 홍길동 900101 1234567 진료 기록을 상담하고 싶습니다.",
        "환자 홍길동 수술 기록 상담",
        "어제 검사 결과와 처방 내역 확인 부탁드립니다.",
    ]

    for question in blocked_questions:
        with pytest.raises(ValueError, match="환자 개인정보"):
            leads_api.LeadCreate(
                clinic_name="장편한외과의원",
                clinic_type="강남 대장항문외과",
                contact="010-0000-0000",
                question=question,
                privacy=True,
            )


def test_lead_question_allows_business_patient_acquisition_phrasing():
    body = leads_api.LeadCreate(
        clinic_name="장편한외과의원",
        clinic_type="강남 대장항문외과",
        contact="010-0000-0000",
        question="환자 유입 상담을 받고 싶습니다.",
        privacy=True,
    )
    assert body.question == "환자 유입 상담을 받고 싶습니다."


def test_mask_contact_phone():
    masked = notifier.mask_contact("010-1234-5678")
    assert "010" in masked and "5678" in masked and "1234" not in masked


def test_mask_contact_email():
    masked = notifier.mask_contact("woojin@motionlabs.kr")
    assert masked.startswith("wo")
    assert "@motionlabs.kr" in masked
    assert "woojin@" not in masked
