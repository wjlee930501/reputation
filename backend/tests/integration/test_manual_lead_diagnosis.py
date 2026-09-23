"""Admin이 직접 만드는 노출 진단 — 새로 만들기와 틀린 입력 고쳐 만들기.

도입문의 폼에 진료과·지역·키워드를 **틀리게** 적는 원장이 있다. 비어 있으면 자동
생성이 거절되지만 틀린 값은 통과해 잘못된 질의로 측정이 끝난다. 그때 손댈 길이
있어야 하고, 옛 진단은 지우지 않아야 한다 — 실제로 지출한 공급자 호출이 있다.
"""

import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from app.api.admin import lead_diagnosis_creation as manual_api
from app.models.admin_user import AdminUser
from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    ExecutionStatus,
    LeadDiagnosis,
    ReportStatus,
)
from app.workers import lead_diagnosis_tasks as leadgen_tasks


def _actor() -> AdminUser:
    return AdminUser(
        id=uuid.uuid4(),
        email="ae@motionlabs.kr",
        name="AE",
        role="OPERATOR",
        password_hash="not-used",
        is_active=True,
    )


def _request(**overrides) -> manual_api.ManualDiagnosisRequest:
    payload = {
        "clinic_name": f"수동진단{uuid.uuid4().hex[:6]}의원",
        "specialty": "정형외과",
        "region_keyword": "강남역",
        "core_keywords": ["도수치료", "허리통증"],
        "contact": "02-123-4567",
        "contact_name": "김원장",
        "reason": "원장이 진료과를 잘못 적어 다시 측정",
    }
    payload.update(overrides)
    return manual_api.ManualDiagnosisRequest(**payload)


async def _inquiry_lead(session, *, clinic_name: str | None = None) -> SalesLead:
    lead = SalesLead(
        clinic_name=clinic_name or f"도입문의{uuid.uuid4().hex[:6]}의원",
        clinic_type="도입문의",
        contact="010-1234-5678",
        privacy=True,
        source="INQUIRY",
    )
    session.add(lead)
    await session.flush()
    return lead


def test_a_new_hospital_still_needs_a_contact():
    # 고쳐 만드는 경로만 연락처를 생략할 수 있다 — 새 리드에는 연락할 수단이 없다.
    with pytest.raises(ValueError):
        _request(contact=None)


@pytest.mark.asyncio
class TestManualCreation:
    async def test_a_hospital_that_never_wrote_to_us_can_be_diagnosed(
        self, pg_async_session
    ):
        # Given: 문의가 없는 병원. When: AE가 값을 직접 넣어 만든다.
        body = _request()
        result = await manual_api.create_manual_diagnosis(
            body, BackgroundTasks(), db=pg_async_session, actor=_actor()
        )

        # Then: 리드와 INTERNAL 진단이 함께 생긴다.
        assert result["superseded_diagnosis_id"] is None
        assert result["delivery_status"] == DeliveryStatus.INTERNAL.value
        lead = await pg_async_session.get(SalesLead, uuid.UUID(result["lead_id"]))
        assert lead is not None
        assert lead.clinic_name == body.clinic_name
        # 도입문의 표식이 있어야 고객 발송 폴러 밖에 있는다.
        assert lead.clinic_type == "도입문의"
        # 원장이 동의한 적이 없다 — 받지 않은 동의를 받았다고 적지 않는다.
        assert lead.privacy is False

        diagnosis = await pg_async_session.get(
            LeadDiagnosis, uuid.UUID(result["diagnosis_id"])
        )
        assert diagnosis is not None
        assert diagnosis.delivery_status == DeliveryStatus.INTERNAL.value
        assert diagnosis.superseded_at is None
        # 무료 진단 자리·잠금·공개 토큰을 소비하지 않는다.
        assert diagnosis.slot_no is None
        assert diagnosis.applicant_email_hash is None

    async def test_wrong_inputs_are_corrected_by_superseding_the_old_diagnosis(
        self, pg_async_session
    ):
        # Given: 틀린 값으로 이미 진단이 만들어진 도입문의.
        lead = await _inquiry_lead(pg_async_session)
        first = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, specialty="내과", reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # When: AE가 진료과를 고쳐 다시 만든다.
        second = await manual_api.create_manual_diagnosis(
            _request(
                lead_id=lead.id,
                specialty="정형외과",
                reason="원장이 진료과를 내과로 잘못 적음",
            ),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # Then: 옛 진단은 지워지지 않고 갈음 관계로 남는다.
        old = await pg_async_session.get(
            LeadDiagnosis, uuid.UUID(first["diagnosis_id"])
        )
        new = await pg_async_session.get(
            LeadDiagnosis, uuid.UUID(second["diagnosis_id"])
        )
        assert second["superseded_diagnosis_id"] == first["diagnosis_id"]
        assert old is not None and old.superseded_at is not None
        assert old.superseded_by_id == new.id
        assert new.superseded_at is None
        # 고친 값이 실제 질의에 들어갔다.
        assert any("정형외과" in q["text"] for q in new.queries)

        # And: 리드에는 활성 진단이 하나뿐이다.
        from app.services import inquiry_diagnosis

        active = await inquiry_diagnosis.active_diagnosis_for(pg_async_session, lead.id)
        assert active is not None and active.id == new.id

    async def test_a_corrected_clinic_name_is_what_gets_measured(self, pg_async_session):
        # Given: 원장이 병원명을 줄여 적은 도입문의와 그 이름으로 만들어진 진단.
        lead = await _inquiry_lead(pg_async_session, clinic_name="연세정정의원")
        original_contact = lead.contact
        first = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, clinic_name="연세정정의원", reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # When: AE가 간판의 정식 병원명으로 고쳐 다시 만든다. 연락처는 보내지 않는다.
        second = await manual_api.create_manual_diagnosis(
            _request(
                lead_id=lead.id,
                clinic_name="강남연세정정의원",
                contact=None,
                reason="병원명을 간판 이름으로 정정",
            ),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # Then: 새 진단은 고친 이름으로 판정하고, 옛 진단은 그때 쟀던 이름을 보존한다.
        old = await pg_async_session.get(LeadDiagnosis, uuid.UUID(first["diagnosis_id"]))
        new = await pg_async_session.get(LeadDiagnosis, uuid.UUID(second["diagnosis_id"]))
        assert new.subject_hospital_name == "강남연세정정의원"
        assert old.subject_hospital_name == "연세정정의원"
        await pg_async_session.refresh(lead)
        assert lead.clinic_name == "강남연세정정의원"
        # 원장이 남긴 연락처는 고쳐 만들기가 덮지 않는다.
        assert lead.contact == original_contact

    async def test_the_corrected_name_is_the_one_kept_out_of_the_keywords(
        self, pg_async_session
    ):
        lead = await _inquiry_lead(pg_async_session, clinic_name="짧은이름의원")
        with pytest.raises(HTTPException) as exc:
            await manual_api.create_manual_diagnosis(
                _request(
                    lead_id=lead.id,
                    clinic_name="강남바른정형외과의원",
                    core_keywords=["강남바른정형외과의원 도수치료"],
                ),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        # 판정은 저장된 옛 이름이 아니라 고친 이름으로 한다 — 화면 검증과 같은 기준이다.
        assert exc.value.status_code == 400

    async def test_a_delivered_diagnosis_is_never_superseded(self, pg_async_session):
        # Given: 고객에게 나간 진단이 붙은 리드.
        lead = await _inquiry_lead(pg_async_session)
        created = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        diagnosis = await pg_async_session.get(
            LeadDiagnosis, uuid.UUID(created["diagnosis_id"])
        )
        # 제약(ck_lead_diagnoses_report_requires_execution)이 순서를 강제한다 —
        # 측정이 끝나야 리포트가 READY일 수 있다.
        diagnosis.execution_status = ExecutionStatus.SUCCEEDED.value
        diagnosis.report_status = ReportStatus.READY.value
        diagnosis.delivery_status = DeliveryStatus.SENT.value
        await pg_async_session.flush()

        # When/Then: 공개 링크가 걸린 판을 조용히 비활성으로 만들지 않는다.
        with pytest.raises(HTTPException) as exc:
            await manual_api.create_manual_diagnosis(
                _request(lead_id=lead.id, reason="고쳐서 다시"),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        assert exc.value.status_code == 409

    async def test_a_non_inquiry_lead_is_refused(self, pg_async_session):
        lead = SalesLead(
            clinic_name="무료진단신청의원",
            clinic_type="내과",
            contact="010-0000-0000",
            privacy=True,
            source="AI_DIAGNOSIS",
        )
        pg_async_session.add(lead)
        await pg_async_session.flush()

        with pytest.raises(HTTPException) as exc:
            await manual_api.create_manual_diagnosis(
                _request(lead_id=lead.id),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        assert exc.value.status_code == 400

    async def test_the_hospital_name_cannot_hide_in_the_keywords(self, pg_async_session):
        # 병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다.
        with pytest.raises(HTTPException) as exc:
            await manual_api.create_manual_diagnosis(
                _request(clinic_name="강남연세의원", core_keywords=["강남연세의원 도수치료"]),
                BackgroundTasks(),
                db=pg_async_session,
                actor=_actor(),
            )
        assert exc.value.status_code == 400
        # 거절된 요청이 리드를 남기지 않는다.
        assert (
            await pg_async_session.scalar(
                select(SalesLead).where(SalesLead.clinic_name == "강남연세의원")
            )
            is None
        )


@pytest.mark.asyncio
class TestSupersededWorkIsAbandoned:
    async def test_a_superseded_diagnosis_never_calls_a_provider_again(
        self, pg_async_session
    ):
        # Given: 갈음된 진단이 아직 PENDING이다(측정이 시작되기 전에 AE가 고쳤다).
        lead = await _inquiry_lead(pg_async_session)
        first = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, specialty="정형외과", reason="고쳐서 다시"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        old_id = uuid.UUID(first["diagnosis_id"])

        # When/Then: 폴러가 집지 않고, 직접 claim해도 물러난다.
        pending = await leadgen_tasks._pending_to_dispatch(pg_async_session)
        assert str(old_id) not in pending
        assert await leadgen_tasks._claim_for_execution(pg_async_session, old_id) is False

        old = await pg_async_session.get(LeadDiagnosis, old_id)
        assert old.execution_status == ExecutionStatus.PENDING.value

    async def test_a_superseded_report_is_not_built_or_marked_failed(
        self, pg_async_session
    ):
        lead = await _inquiry_lead(pg_async_session)
        first = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        old = await pg_async_session.get(
            LeadDiagnosis, uuid.UUID(first["diagnosis_id"])
        )
        old.execution_status = ExecutionStatus.SUCCEEDED.value
        old.report_status = ReportStatus.PENDING.value
        await pg_async_session.flush()

        await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, specialty="정형외과", reason="고쳐서 다시"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # 보고서를 더 만들지 않는다.
        assert str(old.id) not in await leadgen_tasks._reports_to_build(pg_async_session)
        assert (
            await leadgen_tasks._claim_for_report(pg_async_session, old.id) is False
        )

        # 시도를 소진시켜도 DLQ로 보내지 않는다 — 사람이 이미 대체본을 만들었다.
        old.execution_status = ExecutionStatus.PENDING.value
        old.execution_attempts = leadgen_tasks.MAX_EXECUTION_ATTEMPTS
        await pg_async_session.flush()
        exhausted = await leadgen_tasks._exhausted_to_failed(pg_async_session)
        assert old.id not in {row.id for row in exhausted}


@pytest.mark.asyncio
class TestHistoryListing:
    async def test_the_creation_screen_can_show_what_it_created(self, pg_async_session):
        # Given: 방금 만든 콜용 진단.
        created = await manual_api.create_manual_diagnosis(
            _request(clinic_name="이력확인정형외과의원", reason="이력 확인"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        # When: 생성 화면 하단 목록을 읽는다.
        listing = await manual_api.list_manual_diagnoses(
            db=pg_async_session, limit=50, _actor=_actor()
        )

        # Then: 방금 만든 것이 무엇으로 만들어졌는지와 함께 보인다.
        row = next(
            item for item in listing["items"] if item["id"] == created["diagnosis_id"]
        )
        assert row["clinic_name"] == "이력확인정형외과의원"
        assert row["specialty"] == "정형외과"
        assert row["region_keyword"] == "강남역"
        assert row["core_keywords"] == ["도수치료", "허리통증"]
        assert row["lead_id"] == created["lead_id"]
        assert row["report_ready"] is False
        assert row["superseded_at"] is None

    async def test_the_listing_never_carries_contact_details(self, pg_async_session):
        # 이 목록은 식별과 진행 상태 확인용이다. 연락처를 실으면 상담 요청 목록과 같은
        # 대량 PII 표면이 되고, 그쪽이 감사 로그를 남기는 이유가 사라진다.
        await manual_api.create_manual_diagnosis(
            _request(contact="010-9999-8888", contact_name="박원장"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        listing = await manual_api.list_manual_diagnoses(
            db=pg_async_session, limit=50, _actor=_actor()
        )
        body = str(listing)
        assert "010-9999-8888" not in body
        assert "박원장" not in body

    async def test_free_diagnosis_applications_stay_out_of_this_listing(
        self, pg_async_session
    ):
        # Given: 고객에게 발송되는 무료 진단 신청 건.
        lead = SalesLead(
            clinic_name="무료진단신청의원",
            clinic_type="내과",
            contact="010-0000-0000",
            privacy=True,
            source="AI_DIAGNOSIS",
        )
        pg_async_session.add(lead)
        await pg_async_session.flush()
        pg_async_session.add(
            LeadDiagnosis(
                lead_id=lead.id,
                applicant_email_hash=uuid.uuid4().hex,
                subject_phone_hash=uuid.uuid4().hex,
                subject_hospital_name=lead.clinic_name,
                subject_region="강남역",
                queries=[{"slot": 1, "kind": "진료과형", "text": "강남역 근처 내과"}],
                requested_models={"openai": "m", "gemini": "g", "judge": "j"},
                repeat_count=3,
                delivery_status=DeliveryStatus.PENDING.value,
            )
        )
        await pg_async_session.flush()

        # Then: 콜용 화면의 목록에는 섞이지 않는다.
        listing = await manual_api.list_manual_diagnoses(
            db=pg_async_session, limit=100, _actor=_actor()
        )
        assert all(item["clinic_name"] != "무료진단신청의원" for item in listing["items"])

    async def test_a_superseded_row_says_so_instead_of_disappearing(
        self, pg_async_session
    ):
        lead = await _inquiry_lead(pg_async_session)
        first = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, reason="최초 생성"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )
        second = await manual_api.create_manual_diagnosis(
            _request(lead_id=lead.id, specialty="정형외과", reason="고쳐서 다시"),
            BackgroundTasks(),
            db=pg_async_session,
            actor=_actor(),
        )

        listing = await manual_api.list_manual_diagnoses(
            db=pg_async_session, limit=50, _actor=_actor()
        )
        old = next(i for i in listing["items"] if i["id"] == first["diagnosis_id"])
        assert old["superseded_at"] is not None
        assert old["superseded_by_id"] == second["diagnosis_id"]


def _lead_incident(diagnosis_id: uuid.UUID, pipeline: str = "lead_diagnosis"):
    from app.models.operations import IncidentSeverity
    from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest

    return IncidentOpenRequest(
        pipeline=pipeline,
        object_type="diagnosis",
        object_id=str(diagnosis_id),
        fingerprint=IncidentFingerprint.UNKNOWN,
        incident_type="LEAD_DIAGNOSIS_FAILED",
        severity=IncidentSeverity.HIGH,
        customer_impact="신청자에게 진단 리포트를 전달할 수 없습니다.",
        source_type="LEAD_DIAGNOSIS",
        source_id=str(diagnosis_id),
        next_action="운영센터에서 원인을 확인하세요.",
        admin_path="/operations",
        safe_error_code="LEAD_DIAGNOSIS_FAILED",
    )


@pytest.mark.asyncio
async def test_superseding_closes_the_failures_left_on_the_old_diagnosis(pg_async_session):
    """갈음된 진단은 아무도 다시 집지 않는다 — 그 위의 인시던트가 운영자 큐에 남으면 안 된다."""
    from app.models.operations import Incident, IncidentState
    from app.services.incidents import open_or_touch_incident

    # Given: 실패로 끝나 인시던트가 열린 진단과, 상관없는 다른 진단의 인시던트.
    lead = await _inquiry_lead(pg_async_session)
    first = await manual_api.create_manual_diagnosis(
        _request(lead_id=lead.id, reason="최초 생성"),
        BackgroundTasks(),
        db=pg_async_session,
        actor=_actor(),
    )
    old_id = uuid.UUID(first["diagnosis_id"])
    stale = await open_or_touch_incident(
        pg_async_session, _lead_incident(old_id), actor="test", reason="seed"
    )
    stale_report = await open_or_touch_incident(
        pg_async_session, _lead_incident(old_id, "lead_report"), actor="test", reason="seed"
    )
    unrelated = await open_or_touch_incident(
        pg_async_session, _lead_incident(uuid.uuid4()), actor="test", reason="seed"
    )
    await pg_async_session.flush()

    # When: AE가 값을 고쳐 새 진단으로 갈음한다.
    await manual_api.create_manual_diagnosis(
        _request(lead_id=lead.id, specialty="정형외과", reason="진료과 정정"),
        BackgroundTasks(),
        db=pg_async_session,
        actor=_actor(),
    )

    # Then: 옛 진단의 인시던트만 기계가 닫는다.
    for incident in (stale, stale_report, unrelated):
        await pg_async_session.refresh(incident)
    assert stale.state == IncidentState.ACKNOWLEDGED.value
    assert stale.acknowledged_by_id is None
    assert stale_report.state == IncidentState.ACKNOWLEDGED.value
    assert unrelated.state == IncidentState.OPEN.value
    assert isinstance(unrelated, Incident)
