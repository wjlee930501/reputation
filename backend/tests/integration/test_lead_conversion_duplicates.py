"""F-1: 상담 요청 전환이 이미 등록된 병원을 다시 만들지 않는다.

/hospitals/new 는 같은 이름의 병원 생성을 409로 막지만, 리드 전환은 아무 검사 없이
insert 했다. 그래서 이미 운영 중인 병원을 가진 리드가 '온보딩 대기'로 남은 채
빈 병원을 하나 더 만들어냈다. 여기서는 실제 Postgres로 그 경로를 검증한다.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.admin import leads as leads_api
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.lead import SalesLead
from app.services.hospital_duplicates import find_duplicate_hospitals, matches_hospital_name


async def _actor(db) -> AdminUser:
    account = AdminUser(
        email=f"{uuid.uuid4().hex}@example.com",
        name="Ops",
        role=ROLE_OWNER,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    db.add(account)
    await db.flush()
    return account


def _lead(**overrides) -> SalesLead:
    base = dict(
        clinic_name="장편한외과의원",
        clinic_type="외과",
        contact="010-1111-2222",
        question="치질 수술 상담",
        privacy=True,
        source_path="/",
        status="NEW",
    )
    base.update(overrides)
    return SalesLead(**base)


def _hospital(name: str, **overrides) -> Hospital:
    base = dict(
        id=uuid.uuid4(),
        name=name,
        slug=f"dup-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        plan=Plan.PLAN_12,
    )
    base.update(overrides)
    return Hospital(**base)


@pytest.mark.asyncio
async def test_converting_a_lead_links_the_existing_hospital_instead_of_duplicating(
    pg_async_session,
):
    existing = _hospital("장편한외과의원", aeo_domain="jangclinic.kr")
    lead = _lead()
    pg_async_session.add_all([existing, lead])
    await pg_async_session.commit()

    response = await leads_api.convert_sales_lead(
        lead.id,
        body=leads_api.LeadConvertRequest(plan=Plan.PLAN_12),
        db=pg_async_session,
        actor=await _actor(pg_async_session),
    )

    assert response["hospital"]["id"] == str(existing.id)
    assert response["duplicate_resolution"] == "LINKED_EXISTING"
    assert lead.converted_hospital_id == existing.id
    assert existing.source_lead_id == lead.id
    # 새 병원이 생기지 않았다.
    assert await find_duplicate_hospitals(pg_async_session, name="장편한외과의원") == [existing]


@pytest.mark.asyncio
async def test_whitespace_and_case_differences_still_count_as_the_same_hospital(
    pg_async_session,
):
    existing = _hospital("장편한 외과의원")
    lead = _lead(clinic_name="장편한외과의원")
    pg_async_session.add_all([existing, lead])
    await pg_async_session.commit()

    response = await leads_api.convert_sales_lead(lead.id, db=pg_async_session, actor=await _actor(pg_async_session))

    assert response["hospital"]["id"] == str(existing.id)


@pytest.mark.asyncio
async def test_a_phone_only_match_asks_the_operator_instead_of_linking_silently(
    pg_async_session,
):
    """대표번호만 같은 병원은 '같은 병원일 수 있다'는 신호일 뿐이다.

    말없이 이어 붙이면 이름이 다른 남의 병원 온보딩을 이 상담 요청으로 덮어쓴다.
    """
    existing = _hospital("장편한외과", phone="02-123-4567")
    lead = _lead(clinic_name="전혀 다른 이름 의원", clinic_phone="021234567")
    pg_async_session.add_all([existing, lead])
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await leads_api.convert_sales_lead(
            lead.id, db=pg_async_session, actor=await _actor(pg_async_session)
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DUPLICATE_HOSPITAL_FOR_LEAD"
    assert [item["id"] for item in exc.value.detail["candidates"]] == [str(existing.id)]
    assert lead.converted_hospital_id is None


@pytest.mark.asyncio
async def test_a_domain_only_candidate_is_not_a_name_match(pg_async_session):
    """도메인만 겹치는 후보도 자동 연결 조건(이름 일치)을 통과하지 못한다."""
    domain = f"dup-{uuid.uuid4().hex[:8]}.example.com"
    existing = _hospital("기존의원", aeo_domain=domain)
    pg_async_session.add(existing)
    await pg_async_session.commit()

    candidates = await find_duplicate_hospitals(pg_async_session, name="다른의원", domain=domain)

    assert candidates == [existing]
    assert matches_hospital_name(candidates[0], "다른의원") is False


@pytest.mark.asyncio
async def test_the_applicant_personal_phone_is_never_matched_against_a_hospital_line(
    pg_async_session,
):
    """lead.contact 는 문의한 사람의 개인 연락처다 — 병원 대표번호 대조에 쓰지 않는다."""
    existing = _hospital("무관한의원", phone="010-1111-2222")
    lead = _lead(clinic_name=f"신규의원-{uuid.uuid4().hex[:6]}", contact="010-1111-2222")
    pg_async_session.add_all([existing, lead])
    await pg_async_session.commit()

    response = await leads_api.convert_sales_lead(
        lead.id, db=pg_async_session, actor=await _actor(pg_async_session)
    )

    assert response["hospital"]["id"] != str(existing.id)
    assert response["duplicate_resolution"] is None


@pytest.mark.asyncio
async def test_several_candidates_block_the_conversion_so_an_operator_chooses(
    pg_async_session,
):
    first = _hospital("중복의원", phone="02-999-1111")
    second = _hospital("다른이름의원", phone="02-999-1111")
    lead = _lead(clinic_name="중복의원", clinic_phone="02-999-1111")
    pg_async_session.add_all([first, second, lead])
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await leads_api.convert_sales_lead(lead.id, db=pg_async_session, actor=await _actor(pg_async_session))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DUPLICATE_HOSPITAL_FOR_LEAD"
    assert {item["id"] for item in exc.value.detail["candidates"]} == {
        str(first.id),
        str(second.id),
    }
    assert lead.converted_hospital_id is None


@pytest.mark.asyncio
async def test_a_lead_with_no_matching_hospital_still_creates_one(pg_async_session):
    lead = _lead(clinic_name=f"신규의원-{uuid.uuid4().hex[:6]}", contact="010-3333-4444")
    pg_async_session.add(lead)
    await pg_async_session.commit()

    response = await leads_api.convert_sales_lead(
        lead.id,
        body=leads_api.LeadConvertRequest(plan=Plan.PLAN_12),
        db=pg_async_session,
        actor=await _actor(pg_async_session),
    )

    assert response["duplicate_resolution"] is None
    created = await pg_async_session.get(Hospital, uuid.UUID(response["hospital"]["id"]))
    assert created is not None
    assert created.name == lead.clinic_name
    assert created.source_lead_id == lead.id
    # PII-2: 리드 연락처는 공개 병원 전화번호로 복사되지 않는다.
    assert created.phone is None


@pytest.mark.asyncio
async def test_an_explicit_hospital_choice_is_never_overridden_by_detection(pg_async_session):
    chosen = _hospital(f"운영자선택의원-{uuid.uuid4().hex[:6]}")
    same_name = _hospital("이름중복의원")
    lead = _lead(clinic_name="이름중복의원", contact="010-5555-6666")
    pg_async_session.add_all([chosen, same_name, lead])
    await pg_async_session.commit()

    response = await leads_api.convert_sales_lead(
        lead.id,
        body=leads_api.LeadConvertRequest(hospital_id=chosen.id),
        db=pg_async_session,
        actor=await _actor(pg_async_session),
    )

    assert response["hospital"]["id"] == str(chosen.id)
    # 운영자가 고른 병원이므로 자동 연결로 표시하지 않는다.
    assert response["duplicate_resolution"] is None


@pytest.mark.asyncio
async def test_the_lead_candidate_list_uses_the_same_rule_as_the_conversion(pg_async_session):
    existing = _hospital("후보의원", phone="031-222-3333")
    lead = _lead(clinic_name="후보 의원", clinic_phone="0312223333")
    pg_async_session.add_all([existing, lead])
    await pg_async_session.commit()

    response = await leads_api.list_hospital_candidates(lead.id, db=pg_async_session)

    assert [item["id"] for item in response["candidates"]] == [str(existing.id)]


@pytest.mark.asyncio
async def test_the_candidate_list_does_not_surface_hospitals_by_applicant_phone(
    pg_async_session,
):
    unrelated = _hospital("무관한의원2", phone="010-9999-8888")
    lead = _lead(clinic_name=f"후보없음의원-{uuid.uuid4().hex[:6]}", contact="010-9999-8888")
    pg_async_session.add_all([unrelated, lead])
    await pg_async_session.commit()

    response = await leads_api.list_hospital_candidates(lead.id, db=pg_async_session)

    assert response["candidates"] == []
