"""공개 사진 저장 경로를 마이그레이션 적용된 Postgres에서 끝까지 커밋해 본다.

`ck_public_photo_requires_provenance`(0052)는 공개된 PHOTO_* 행에 소유자·권리 근거·
증빙 위치·확인자·확인 시각을 요구한다. 이 제약이 살아 있는 DB에서 업로드와 공개
토글을 실제로 커밋하는 테스트가 없어서, 앱이 provenance를 한 줄도 쓰지 않는데도 CI가
초록으로 남아 있었다(순수 함수 단위 테스트만 존재). 여기서는 라우트 함수를 실제
AsyncSession으로 호출해 다음을 고정한다:

* 근거를 갖춘 업로드는 공개 상태로 커밋된다 (500 없음)
* 근거 없이 공개를 요청하면 무엇이 비었는지 알려 주는 422로 막힌다 (조용한 비공개 저장 아님)
* 공개를 요청하지 않은 업로드는 근거 없이도 비공개로 저장된다 (사진은 게이트가 아니다)
* 근거 없는 공개 토글은 IntegrityError가 아니라 설명 가능한 422로 막힌다
* 0052가 비공개로 돌려놓은 기존 사진은 근거를 채우면서 다시 공개할 수 있다
"""

import io
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers

from app.api.admin import essence as essence_api
from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.schemas.essence import SourcePublicToggle

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def stored_asset_ref(monkeypatch):
    """저장소 쓰기는 이 테스트의 대상이 아니다 — 결정적인 참조만 돌려준다."""

    def _store(*, hospital_id, filename, data, mime_type):
        return f"local://{hospital_id}/{filename}"

    monkeypatch.setattr(essence_api, "store_asset_bytes", _store)


def _upload_file() -> UploadFile:
    return UploadFile(
        file=io.BytesIO(PNG_BYTES),
        size=len(PNG_BYTES),
        filename="clinic.png",
        headers=Headers({"content-type": "image/png"}),
    )


async def _seed_hospital(session) -> Hospital:
    hospital = Hospital(
        id=uuid.uuid4(),
        name="사진근거테스트병원",
        slug=f"photo-prov-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        # 공개 사이트가 없는 병원 — 캐시 갱신 경로를 건드리지 않고 저장만 검증한다.
        site_live=False,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        treatments=[],
    )
    session.add(hospital)
    await session.flush()
    return hospital


async def _photo_count(session, hospital_id) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(HospitalSourceAsset)
        .where(HospitalSourceAsset.hospital_id == hospital_id)
    )
    return int(result.scalar_one())


async def _get_source(session, source_id) -> HospitalSourceAsset:
    result = await session.execute(
        select(HospitalSourceAsset).where(HospitalSourceAsset.id == uuid.UUID(str(source_id)))
    )
    return result.scalar_one()


async def test_the_public_photo_constraint_is_live_on_this_database(pg_async_session):
    """제약이 없으면 아래 테스트들이 아무것도 증명하지 못한다 — 먼저 확인한다."""
    hospital = await _seed_hospital(pg_async_session)
    pg_async_session.add(
        HospitalSourceAsset(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            source_type=SourceType.PHOTO_CLINIC_INTERIOR,
            title="근거 없는 공개 사진",
            file_url="local://x/clinic.png",
            is_public=True,
            status=SourceStatus.PENDING,
        )
    )
    with pytest.raises(IntegrityError) as failure:
        await pg_async_session.flush()
    assert "ck_public_photo_requires_provenance" in str(failure.value)
    await pg_async_session.rollback()


async def test_public_photo_upload_with_rights_evidence_commits(pg_async_session):
    hospital = await _seed_hospital(pg_async_session)

    response = await essence_api.upload_source_file(
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_INTERIOR,
        title="진료실",
        file=_upload_file(),
        is_public=True,
        asset_kind="VERIFIED_FACILITY",
        photo_source_owner="사진근거테스트병원",
        photo_rights_basis="owner_consent",
        photo_evidence_reference="계약서 부속 합의서 3조",
        operator_note=None,
        created_by="AE",
        skip_revalidate=False,
        db=pg_async_session,
    )

    assert response["is_public"] is True
    provenance = response["photo_provenance"]
    assert provenance["is_complete"] is True
    assert provenance["rights_basis"] == "OWNER_CONSENT"
    # 확인자·확인 시각은 클라이언트가 아니라 서버가 찍는다.
    assert provenance["verified_by"]
    assert provenance["verified_at"]

    stored = await _get_source(pg_async_session, response["id"])
    assert stored.is_public is True
    assert stored.photo_source_owner == "사진근거테스트병원"
    assert stored.photo_evidence_reference == "계약서 부속 합의서 3조"


async def test_asking_to_publish_without_rights_evidence_is_refused_at_upload(pg_async_session):
    """공개를 요청했는데 근거가 없으면 조용히 비공개로 저장하지 않는다.

    비공개 201로 넘기면 운영자는 사진이 공개된 줄 알고 다음 단계로 간다. 무엇이
    비었는지 알려 주는 422로 돌려보낸다 — 500도, 저장소에 남는 파일도 없다.
    """
    hospital = await _seed_hospital(pg_async_session)

    with pytest.raises(HTTPException) as refused:
        await essence_api.upload_source_file(
            hospital_id=hospital.id,
            source_type=SourceType.PHOTO_CLINIC_EXTERIOR,
            title="외관",
            file=_upload_file(),
            is_public=True,
            asset_kind="VERIFIED_FACILITY",
            photo_source_owner=None,
            photo_rights_basis=None,
            photo_evidence_reference=None,
            operator_note=None,
            created_by="AE",
            skip_revalidate=False,
            db=pg_async_session,
        )

    assert refused.value.status_code == 422
    assert "사진 소유자" in refused.value.detail
    assert await _photo_count(pg_async_session, hospital.id) == 0


async def test_a_private_photo_upload_needs_no_rights_evidence(pg_async_session):
    """사진은 온보딩 게이트가 아니다 — 공개를 요청하지 않으면 근거 없이도 저장된다."""
    hospital = await _seed_hospital(pg_async_session)

    response = await essence_api.upload_source_file(
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_EXTERIOR,
        title="외관",
        file=_upload_file(),
        is_public=False,
        asset_kind="VERIFIED_FACILITY",
        photo_source_owner=None,
        photo_rights_basis=None,
        photo_evidence_reference=None,
        operator_note=None,
        created_by="AE",
        skip_revalidate=False,
        db=pg_async_session,
    )

    assert response["is_public"] is False
    provenance = response["photo_provenance"]
    assert provenance["is_complete"] is False
    assert "photo_source_owner" in provenance["missing_fields"]
    assert provenance["missing_message"]

    stored = await _get_source(pg_async_session, response["id"])
    assert stored.is_public is False
    assert stored.file_url


async def test_an_upload_that_never_asked_to_publish_is_stored_private(pg_async_session):
    """일괄 업로드가 공개 여부를 보내지 않으면 근거가 채워질 때까지 비공개로 둔다."""
    hospital = await _seed_hospital(pg_async_session)

    response = await essence_api.upload_source_file(
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_TREATMENT_ROOM,
        title="시술실",
        file=_upload_file(),
        is_public=None,
        asset_kind="VERIFIED_FACILITY",
        photo_source_owner=None,
        photo_rights_basis=None,
        photo_evidence_reference=None,
        operator_note=None,
        created_by="AE",
        skip_revalidate=False,
        db=pg_async_session,
    )

    assert response["is_public"] is False
    stored = await _get_source(pg_async_session, response["id"])
    assert stored.file_url


async def test_publishing_without_rights_evidence_is_refused_with_an_explanation(
    pg_async_session,
):
    hospital = await _seed_hospital(pg_async_session)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_INTERIOR,
        title="근거 미입력 사진",
        file_url="local://x/clinic.png",
        mime_type="image/png",
        is_public=False,
        status=SourceStatus.PENDING,
        source_metadata={"asset_kind": "VERIFIED_FACILITY", "approved_usage": ["HERO", "GALLERY"]},
    )
    source_id = source.id
    pg_async_session.add(source)
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as refused:
        await essence_api.toggle_source_public(
            hospital_id=hospital.id,
            source_id=source_id,
            body=SourcePublicToggle(is_public=True),
            db=pg_async_session,
        )

    assert refused.value.status_code == 422
    assert "권리 근거" in refused.value.detail or "사진 소유자" in refused.value.detail

    await pg_async_session.rollback()
    stored = await _get_source(pg_async_session, source_id)
    assert stored.is_public is False


async def test_a_photo_privated_by_the_migration_can_be_republished_with_evidence(
    pg_async_session,
):
    """0052 배포 직후 프로덕션 상태 — 공개였던 사진이 근거 없이 비공개로 남아 있다."""
    hospital = await _seed_hospital(pg_async_session)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_DOCTOR,
        title="원장 사진",
        file_url="local://x/doctor.png",
        mime_type="image/png",
        is_public=False,
        status=SourceStatus.PROCESSED,
        # 분류 이전에 올라온 legacy 행 — 재공개가 분류 때문에 막히지도 않아야 한다.
        source_metadata={"original_filename": "doctor.png"},
    )
    pg_async_session.add(source)
    await pg_async_session.commit()

    response = await essence_api.toggle_source_public(
        hospital_id=hospital.id,
        source_id=source.id,
        body=SourcePublicToggle(
            is_public=True,
            photo_source_owner="사진근거테스트병원",
            photo_rights_basis="LICENSE",
            photo_evidence_reference="촬영 계약 2026-03",
        ),
        db=pg_async_session,
    )

    assert response["is_public"] is True
    assert response["photo_provenance"]["is_complete"] is True

    stored = await _get_source(pg_async_session, source.id)
    assert stored.is_public is True
    assert stored.photo_rights_basis == "LICENSE"
    assert stored.photo_verified_at is not None
    # 재공개가 원장 사진을 확인 없이 identity로 승격시키지는 않는다.
    assert stored.source_metadata["asset_kind"] == "EDITORIAL_GRAPHIC"


async def test_an_unknown_rights_basis_is_refused_before_it_reaches_the_constraint(
    pg_async_session,
):
    hospital = await _seed_hospital(pg_async_session)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_INTERIOR,
        title="진료실",
        file_url="local://x/clinic.png",
        is_public=False,
        status=SourceStatus.PENDING,
        source_metadata={"asset_kind": "VERIFIED_FACILITY", "approved_usage": ["HERO", "GALLERY"]},
    )
    pg_async_session.add(source)
    await pg_async_session.commit()

    with pytest.raises(HTTPException) as refused:
        await essence_api.toggle_source_public(
            hospital_id=hospital.id,
            source_id=source.id,
            body=SourcePublicToggle(
                is_public=True,
                photo_source_owner="병원",
                photo_rights_basis="PROBABLY_FINE",
                photo_evidence_reference="메모",
            ),
            db=pg_async_session,
        )

    assert refused.value.status_code == 422
    await pg_async_session.rollback()


async def test_unpublishing_never_needs_rights_evidence(pg_async_session):
    """비공개로 되돌리는 길은 항상 열려 있어야 한다 — 사고 대응 경로다."""
    hospital = await _seed_hospital(pg_async_session)
    source = HospitalSourceAsset(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_CLINIC_INTERIOR,
        title="진료실",
        file_url="local://x/clinic.png",
        is_public=True,
        status=SourceStatus.PROCESSED,
        source_metadata={"asset_kind": "VERIFIED_FACILITY", "approved_usage": ["HERO", "GALLERY"]},
        photo_source_owner="병원",
        photo_rights_basis="LICENSE",
        photo_evidence_reference="계약",
        photo_verified_by="AE",
        photo_verified_at=datetime.now(timezone.utc),
    )
    pg_async_session.add(source)
    await pg_async_session.commit()

    response = await essence_api.toggle_source_public(
        hospital_id=hospital.id,
        source_id=source.id,
        body=SourcePublicToggle(is_public=False),
        db=pg_async_session,
    )

    assert response["is_public"] is False
