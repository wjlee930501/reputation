"""Tests for public lead intake.

Single-actor model + PII masking + retention column. We bypass the slowapi
@limiter.limit decorator via __wrapped__ since unit tests don't run inside
the FastAPI request lifecycle.
"""
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.api.public import leads as leads_api
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDiagnosis
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


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    """접수 알림의 SENDING claim·결과 CAS(`UPDATE … RETURNING`)는 항상 한 행을 잡는다."""

    def __init__(self):
        self.added = []
        self.committed = False
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result("lead-id")

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
    response = await _create_lead(
        request=FakeRequest(forwarded="203.0.113.7"), body=body, background_tasks=BackgroundTasks(), db=db
    )

    assert response["ok"] is True
    lead = db.added[0]
    assert len(db.added) == 1
    assert isinstance(lead, SalesLead)
    assert not any(isinstance(row, LeadDiagnosis) for row in db.added)
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
    await _create_lead(request=FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=db)

    # 실패는 끝이 아니다 — drain이 1시간 동안 다시 보낸다.
    assert db.added[0].notification_status == "PENDING"
    assert "Slack/webhook" in db.added[0].notification_error


def test_diagnosis_note_constants_stay_importable_from_the_api():
    """문구 정본은 서비스(drain 재발송이 같은 줄을 다시 만든다), API 이름도 그대로 보인다."""
    from app.services import inquiry_diagnosis

    assert leads_api.DIAGNOSIS_NOTE_QUEUED == inquiry_diagnosis.DIAGNOSIS_NOTE_QUEUED
    assert leads_api.DIAGNOSIS_NOTE_NO_INPUT == inquiry_diagnosis.DIAGNOSIS_NOTE_NO_INPUT
    assert leads_api.DIAGNOSIS_NOTE_REFUSED == inquiry_diagnosis.DIAGNOSIS_NOTE_REFUSED


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
        background_tasks=BackgroundTasks(),
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
        await _create_lead(request=FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=db)
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
    response = await _create_lead(request=FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=db)
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
    response = await _create_lead(request=FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=db)
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
    response = await _create_lead(request=FakeRequest(), body=body, background_tasks=BackgroundTasks(), db=db)
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


def test_lead_clinic_name_and_type_reject_patient_sensitive_free_text():
    """병원명·진료과 칸도 공개 폼 자유 텍스트다 — question과 동일하게 차단되어야 한다.

    이 검증이 없으면 clinic_name이 검증 없이 Slack 제목/본문으로 평문 송출된다.
    """
    sensitive = "홍길동 환자 900101-1234567"

    with pytest.raises(ValueError, match="환자 개인정보"):
        leads_api.LeadCreate(
            clinic_name=sensitive,
            clinic_type="강남 대장항문외과",
            contact="010-0000-0000",
            question="치질 수술 회복 기간은?",
            privacy=True,
        )

    with pytest.raises(ValueError, match="환자 개인정보"):
        leads_api.LeadCreate(
            clinic_name="장편한외과의원",
            clinic_type=sensitive,
            contact="010-0000-0000",
            question="치질 수술 회복 기간은?",
            privacy=True,
        )


async def test_notify_lead_created_masks_residual_identifiers_in_clinic_name(monkeypatch):
    """입력 검증을 통과해도 Slack 라벨에는 식별정보가 남지 않는다(2차 방어)."""
    sent = {}

    async def fake_send(text, blocks=None, **_kwargs):
        sent["text"] = text
        sent["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)

    await notifier.notify_lead_created(
        clinic_name="장편한외과의원 900101-1234567 wjlee@motionlabs.kr",
        contact="010-0000-0000",
        admin_url="https://admin.example.com/leads",
    )

    payload = sent["text"] + sent["blocks"][0]["text"]["text"]
    assert "900101-1234567" not in payload
    assert "wjlee@motionlabs.kr" not in payload
    assert "[id]" in payload
    assert "[email]" in payload
    assert "장편한외과의원" in payload  # 식별에 필요한 병원명 자체는 유지


async def test_notify_lead_created_truncates_and_flattens_long_clinic_name(monkeypatch):
    """긴 자유 텍스트/개행으로 Slack 블록을 밀어내는 스팸을 막는다."""
    sent = {}

    async def fake_send(text, blocks=None, **_kwargs):
        sent["text"] = text
        sent["blocks"] = blocks
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)

    await notifier.notify_lead_created(
        clinic_name="가" * 200 + "\n연락처: 010-1111-2222",
        contact="010-0000-0000",
    )

    assert "\n" not in sent["text"]
    assert sent["text"].split(" | ", 1)[0].endswith("…")
    assert len(sent["text"].split("[새 문의] ", 1)[1].split(" | ", 1)[0]) == notifier._SAFE_LABEL_MAX_CHARS + 1


def test_mask_contact_free_masks_resident_registration_number():
    """전화 패턴이 주민번호 뒷자리를 먼저 먹어 앞 6자리가 남는 일이 없어야 한다."""
    masked = notifier.mask_contact_free("환자 900101-1234567 문의")
    assert "900101" not in masked
    assert "1234567" not in masked
    assert "[id]" in masked


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


# ── 초도 노출 진단 자동 생성 + 접수 안내 문자 ─────────────────────────────────────

from app.services import inquiry_diagnosis, inquiry_sms  # noqa: E402


def _inquiry_body(**overrides):
    fields = dict(
        clinic_name="강심장내과의원",
        clinic_type="도입문의",
        contact="010-1234-5678",
        question="병원 주소: 서울 강남구\n원장님 성함: 김원장\n병원 홈페이지: https://x.example",
        privacy=True,
        source_path="/contact",
        specialty="내과",
        region_keyword="강남역",
        core_keywords=["고혈압", "심장초음파"],
        contact_name="김원장",
    )
    fields.update(overrides)
    return leads_api.LeadCreate(**fields)


def _silence_notifier(monkeypatch):
    captured = []

    async def fake_notify(**payload):
        captured.append(payload)
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
    return captured


def _sms_result(monkeypatch, status="SENT"):
    async def fake_ack(db, lead):
        lead.ack_sms_status = status
        return inquiry_sms.AckSmsOutcome(status, None if status == "SENT" else "stub")

    monkeypatch.setattr(inquiry_sms, "acknowledge_inquiry", fake_ack)


async def test_intake_with_diagnosis_fields_creates_the_internal_diagnosis(monkeypatch):
    slack = _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "SENT")
    created = []
    queued = []

    async def fake_create(db, lead, spec, *, actor):
        created.append((lead, spec, actor))
        return SimpleNamespace(id="diag-1")

    monkeypatch.setattr(inquiry_diagnosis, "create_inquiry_diagnosis", fake_create)
    monkeypatch.setattr(inquiry_diagnosis, "enqueue_inquiry_diagnosis", queued.append)

    db = FakeDB()
    background_tasks = BackgroundTasks()
    response = await _create_lead(
        request=FakeRequest(), body=_inquiry_body(), background_tasks=background_tasks, db=db
    )
    assert queued == []  # 큐잉은 응답 뒤 background task에서 — 요청 안에서 브로커를 기다리지 않는다
    await background_tasks()

    lead = db.added[0]
    assert lead.specialty == "내과"
    assert lead.region_keyword == "강남역"
    assert lead.core_keywords == ["고혈압", "심장초음파"]
    assert lead.contact_name == "김원장"
    assert lead.clinic_type == "도입문의"
    spec = created[0][1]
    assert created[0][0] is lead
    assert created[0][2] == "system:public-inquiry"
    assert spec.specialty == "내과"
    assert spec.core_keywords == ["고혈압", "심장초음파"]
    assert queued == ["diag-1"]
    assert response["diagnosis_id"] == "diag-1"
    assert response["ack_sms"] == "sent"
    assert slack[0]["diagnosis_note"] == leads_api.DIAGNOSIS_NOTE_QUEUED
    # Slack 요약은 처리방침 고지 범위의 필드만 받는다 — 핵심 키워드·문의 본문·담당자 성함은 넘기지 않는다.
    assert slack[0]["specialty"] == "내과"
    assert slack[0]["region_keyword"] == "강남역"
    assert slack[0]["source_path"] == "/contact"
    assert not {"question", "contact_name", "core_keywords"} & slack[0].keys()


async def test_intake_without_diagnosis_fields_leaves_creation_to_admin(monkeypatch):
    slack = _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "SKIPPED")

    async def forbidden(*args, **kwargs):
        raise AssertionError("diagnosis must not be created without inputs")

    monkeypatch.setattr(inquiry_diagnosis, "create_inquiry_diagnosis", forbidden)

    db = FakeDB()
    response = await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=db,
    )

    assert response["diagnosis_id"] is None
    assert response["ack_sms"] == "skipped"
    assert slack[0]["diagnosis_note"] == leads_api.DIAGNOSIS_NOTE_NO_INPUT


async def test_intake_survives_a_refused_diagnosis_and_still_notifies_and_texts(monkeypatch):
    slack = _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "SENT")

    async def refuse(db, lead, spec, *, actor):
        raise inquiry_diagnosis.InquiryDiagnosisError(400, "진료과·지역·키워드에는 병원명을 넣을 수 없습니다.")

    monkeypatch.setattr(inquiry_diagnosis, "create_inquiry_diagnosis", refuse)

    db = FakeDB()
    response = await _create_lead(
        request=FakeRequest(), body=_inquiry_body(), background_tasks=BackgroundTasks(), db=db
    )

    assert response["ok"] is True
    assert response["diagnosis_id"] is None
    assert response["ack_sms"] == "sent"
    assert slack[0]["diagnosis_note"] == leads_api.DIAGNOSIS_NOTE_REFUSED
    assert db.added[0].notification_status == "SENT"


async def test_intake_records_sms_failure_without_failing_the_lead(monkeypatch):
    _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "FAILED")

    db = FakeDB()
    response = await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=db,
    )

    assert response["ok"] is True
    assert response["ack_sms"] == "failed"
    assert db.added[0].ack_sms_status == "FAILED"
    assert db.committed is True


async def test_honeypot_sends_neither_diagnosis_nor_sms(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not be called for a honeypot submission")

    monkeypatch.setattr(inquiry_diagnosis, "create_inquiry_diagnosis", forbidden)
    monkeypatch.setattr(inquiry_sms, "acknowledge_inquiry", forbidden)
    monkeypatch.setattr(notifier, "notify_lead_created", forbidden)

    db = FakeDB()
    response = await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(website="http://bot.example"),
        background_tasks=BackgroundTasks(),
        db=db,
    )
    assert response["lead_id"] is None
    assert db.added == []


def test_diagnosis_fields_are_cleaned_and_screened():
    body = _inquiry_body(core_keywords=[" 고혈압 ", "고혈압", "", "부정맥", "당뇨", "갑상선", "비만"])
    assert body.core_keywords == ["고혈압", "부정맥", "당뇨", "갑상선"]
    assert _inquiry_body(core_keywords=["", "  "]).core_keywords is None
    assert _inquiry_body(core_keywords=["", "  "]).diagnosis_input() is None

    with pytest.raises(ValueError, match="환자 개인정보"):
        _inquiry_body(region_keyword="환자 900101-1234567 진료 기록")
    with pytest.raises(ValueError, match="환자 개인정보"):
        _inquiry_body(core_keywords=["환자 홍길동 수술 기록"])
    with pytest.raises(ValueError, match="50자"):
        _inquiry_body(core_keywords=["가" * 51])


class _OrderedDB(FakeDB):
    """커밋 순서를 기록한다 — 사고가 실패 상태와 같은 커밋에 실리는지 본다."""

    def __init__(self):
        super().__init__()
        self.events = []

    async def commit(self):
        self.events.append("commit")
        await super().commit()


async def test_a_failed_sms_records_its_incident_in_the_status_commit(monkeypatch):
    _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "FAILED")
    db = _OrderedDB()

    async def fake_record(target_db, lead_id, *, unconfirmed=False):
        assert target_db is db
        assert unconfirmed is False
        db.events.append(("incident", lead_id))
        return True

    monkeypatch.setattr(inquiry_sms, "record_failure_incident", fake_record)
    tasks = BackgroundTasks()

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=tasks,
        db=db,
    )

    # 리드 커밋 → SENDING claim 커밋 → 발송 결과 커밋, 그다음 실패 상태와 사고가 한 커밋.
    # 응답 뒤 작업으로 미루지 않는다.
    assert db.events == ["commit", "commit", "commit", ("incident", "lead-id"), "commit"]
    assert tasks.tasks == []


async def test_an_unconfirmed_sms_records_its_incident_as_unconfirmed(monkeypatch):
    """문자 사업자가 받았는지 모르는 실패(5xx·응답 유실)는 사고에 '재발송하지 않음'으로 남는다(Codex R4-2)."""

    _silence_notifier(monkeypatch)

    async def fake_ack(_db, lead):
        lead.ack_sms_status = "FAILED"
        return inquiry_sms.AckSmsOutcome("FAILED", "stub", unconfirmed=True)

    monkeypatch.setattr(inquiry_sms, "acknowledge_inquiry", fake_ack)
    recorded = []

    async def fake_record(_db, lead_id, *, unconfirmed=False):
        recorded.append(unconfirmed)
        return True

    monkeypatch.setattr(inquiry_sms, "record_failure_incident", fake_record)

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=_OrderedDB(),
    )

    assert recorded == [True]


async def test_skipped_sms_opens_nothing(monkeypatch):
    _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "SKIPPED")

    async def forbidden(*_a, **_k):
        pytest.fail("no incident")

    monkeypatch.setattr(inquiry_sms, "record_failure_incident", forbidden)

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=FakeDB(),
    )


async def test_an_incident_failure_never_breaks_the_intake(monkeypatch):
    class _Nested:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _DB:
        def begin_nested(self):
            return _Nested()

    async def broken_open(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(inquiry_sms, "open_ops_incident_in", broken_open)

    assert await inquiry_sms.record_failure_incident(_DB(), "lead-id") is False


async def test_the_sms_incident_keeps_the_privacy_scope(monkeypatch):
    opened = []

    async def capture(_db, **kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(inquiry_sms, "open_ops_incident_in", capture)

    await inquiry_sms.open_failure_incident(object(), "lead-id", actor="public-inquiry")

    assert [item["incident_type"] for item in opened] == ["INQUIRY_SMS_FAILED"]
    assert opened[0]["object_id"] == "lead-id"
    assert opened[0]["hospital_name"] == "도입문의"  # 병원명·연락처를 사고 제목에 싣지 않는다


async def test_the_intake_notice_is_claimed_as_sending_and_committed_before_slack(monkeypatch):
    """SENDING을 커밋한 뒤에만 보낸다 — 보낸 직후 죽어도 drain이 NULL로 보고 다시 보내지 않는다(R2-1)."""

    db = _OrderedDB()

    async def fake_execute(statement):
        compiled = statement.compile()
        db.events.append(("execute", compiled.params.get("notification_status")))
        return _Result("lead-id")

    db.execute = fake_execute

    async def fake_notify(**_payload):
        db.events.append("slack")
        return True

    monkeypatch.setattr(notifier, "notify_lead_created", fake_notify)
    _sms_result(monkeypatch, "SKIPPED")

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=db,
    )

    assert db.events[:6] == [
        "commit",  # 리드
        ("execute", "SENDING"),
        "commit",  # claim — 발송 전에 영속한다
        "slack",
        ("execute", "SENT"),
        "commit",  # 결과 CAS — 문자를 보내기 전에 남긴다
    ]
    assert db.added[0].notification_status == "SENT"


async def test_the_intake_skips_slack_when_its_claim_was_already_taken(monkeypatch):
    """접수가 15분 넘게 멈춘 사이 drain이 NULL을 집어 보냈다면 접수 API는 다시 보내지 않는다."""

    class _TakenDB(FakeDB):
        async def execute(self, statement):
            self.statements.append(statement)
            return _Result(None)

    async def forbidden(**_payload):
        pytest.fail("the worker already owns this notice")

    monkeypatch.setattr(notifier, "notify_lead_created", forbidden)
    _sms_result(monkeypatch, "SKIPPED")

    response = await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=BackgroundTasks(),
        db=_TakenDB(),
    )

    assert response["ok"] is True
