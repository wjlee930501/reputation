"""Phase 4(D33): 승인본을 사람이 고칠 초안으로 복사하는 경로는 없다(410).

복사본은 PATCH·사람 승인으로만 쓰였는데 그 둘이 410이 되었다 — 남겨 두면 아무도 적용할 수 없는
초안만 쌓인다. 내용을 바꾸려면 병원 정보·근거 자료·원장 인터뷰 설문을 고치면 자동 요약이 다시
만들어 바로 적용한다.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin import essence as essence_api
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus


@pytest.mark.asyncio
async def test_copy_to_draft_is_retired_and_creates_nothing(pg_async_session):
    hospital = Hospital(
        id=uuid.uuid4(),
        name="복사 경로 병원",
        slug=f"draft-copy-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
    )
    approved = HospitalContentPhilosophy(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
    )
    pg_async_session.add_all([hospital, approved])
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await essence_api.copy_approved_philosophy_to_draft(hospital.id, approved.id)

    assert exc.value.status_code == 410
    assert exc.value.detail["code"] == "ESSENCE_APPROVAL_RETIRED"
    rows = (
        await pg_async_session.execute(
            select(HospitalContentPhilosophy.status).where(
                HospitalContentPhilosophy.hospital_id == hospital.id
            )
        )
    ).scalars().all()
    assert rows == [PhilosophyStatus.APPROVED]
