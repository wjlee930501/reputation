"""출처 원장 백필(P4-4) — 감사 로그의 사람 변경은 OPERATOR, 기록 없는 값은 LEGACY, 덮어쓰지 않는다."""

from __future__ import annotations

import uuid

import pytest

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.services import profile_provenance as prov


@pytest.mark.asyncio
async def test_backfill_from_audit_logs_is_idempotent_and_never_overwrites(pg_async_session):
    db = pg_async_session
    hospital = Hospital(
        id=uuid.uuid4(),
        name="원장백필의원",
        slug=f"prov-bf-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ONBOARDING,
        address="서울 성동구",
        phone="02-000-0000",
        director_name="김원장",
        keywords=["치질"],
        region=[],
        specialties=[],
        competitors=[],
        treatments=[],
        profile_field_provenance={"keywords": {"origin": "AUTOFILL", "source": "naver"}},
    )
    db.add(hospital)
    await db.flush()
    db.add_all(
        [
            AdminAuditLog(
                hospital_id=hospital.id,
                actor="ae@example.com",
                action="update_profile",
                detail={"changed_fields": ["phone", "physicians", "keywords", "director_career"]},
            ),
            AdminAuditLog(
                hospital_id=hospital.id,
                actor="system:cli",
                action="update_profile",
                detail={"changed_fields": ["address"]},
            ),
        ]
    )
    await db.flush()

    summary = await prov.backfill_provenance_from_audit(db, hospital)
    await db.flush()

    assert summary == {
        "operator": ["director_career", "keywords", "phone"],
        "legacy": ["address", "director_name"],
    }
    ledger = hospital.profile_field_provenance
    assert ledger["phone"]["origin"] == "OPERATOR"
    # 사람이 비운 약력도 마케터 소유다 — 값이 없어도 자동 입력이 채우지 않는다.
    assert ledger["director_career"]["origin"] == "OPERATOR"
    assert ledger["address"]["origin"] == "LEGACY"  # system: 변경은 사람 입력이 아니다
    # 사람이 바꾼 증거는 먼저 적힌 AUTOFILL 기록보다 우선한다(배포 직후 원장 백필 전에 돈 적용).
    assert ledger["keywords"]["origin"] == "OPERATOR"
    assert "physicians" not in ledger

    again = await prov.backfill_provenance_from_audit(db, hospital)
    assert again == {"operator": [], "legacy": []}

    await db.commit()
    await db.refresh(hospital)
    assert hospital.profile_field_provenance["phone"]["actor"] == "ae@example.com"
