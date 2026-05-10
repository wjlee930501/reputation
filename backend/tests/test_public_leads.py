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


def test_mask_contact_phone():
    masked = notifier.mask_contact("010-1234-5678")
    assert "010" in masked and "5678" in masked and "1234" not in masked


def test_mask_contact_email():
    masked = notifier.mask_contact("woojin@motionlabs.kr")
    assert masked.startswith("wo")
    assert "@motionlabs.kr" in masked
    assert "woojin@" not in masked
