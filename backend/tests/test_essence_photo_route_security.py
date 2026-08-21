"""Route-level proof for photo provenance auth and rollback cleanup."""

import inspect
import uuid
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError

from app.api.admin import essence
from app.api.admin.accounts import require_active_account
from app.models.essence import SourceType
from app.schemas.essence import SourceAssetPatch


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (10, 10), (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


class _FailingDB:
    def __init__(self) -> None:
        self.rolled_back = False

    def add(self, _value) -> None:
        return None

    async def commit(self) -> None:
        raise SQLAlchemyError("persistence failed")

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, _value) -> None:
        return None


class _PatchDB:
    async def execute(self, _statement):
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _value) -> None:
        return None


@pytest.mark.parametrize(
    "endpoint",
    [essence.upload_source_file, essence.patch_source, essence.toggle_source_public],
)
def test_photo_mutation_routes_require_an_active_account(endpoint) -> None:
    # Given: each provenance-sensitive route's FastAPI signature.
    actor_dependency = inspect.signature(endpoint).parameters["actor"].default

    # When / Then: identity is resolved from an active server-side account.
    assert actor_dependency.dependency is require_active_account


async def test_upload_validates_provenance_before_storage(monkeypatch) -> None:
    # Given: a real photo but missing source-owner/rights evidence.
    stored = False

    async def _hospital(_db, hospital_id):
        return SimpleNamespace(
            id=hospital_id,
            slug="test-clinic",
            name="테스트병원",
            status="DRAFT",
            site_live=False,
        )

    def _store(**_kwargs):
        nonlocal stored
        stored = True
        return "local://unexpected"

    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "store_asset_bytes", _store)

    # When: the upload crosses the API boundary without provenance.
    with pytest.raises(HTTPException) as error:
        await essence.upload_source_file(
            hospital_id=uuid.uuid4(),
            source_type=SourceType.PHOTO_DOCTOR,
            title="원장 사진",
            file=UploadFile(filename="doctor.png", file=BytesIO(_png())),
            is_public=True,
            asset_kind="VERIFIED_REAL_PERSON",
            photo_source_owner=None,
            photo_rights_basis=None,
            photo_evidence_reference=None,
            operator_note=None,
            skip_revalidate=False,
            db=_FailingDB(),
            actor=SimpleNamespace(email="owner@example.com"),
        )

    # Then: validation rejects it before any storage write.
    assert error.value.status_code == 422
    assert stored is False


async def test_upload_deletes_stored_object_when_db_persistence_fails(monkeypatch) -> None:
    # Given: a valid verified photo and a database commit failure after storage.
    hospital_id = uuid.uuid4()
    asset_ref = f"local://{hospital_id}/canonical.png"
    deleted: list[tuple[str, uuid.UUID]] = []
    db = _FailingDB()

    async def _hospital(_db, requested_id):
        return SimpleNamespace(
            id=requested_id,
            slug="test-clinic",
            name="테스트병원",
            status="DRAFT",
            site_live=False,
        )

    async def _lock(_db, _hospital_id):
        return None

    async def _audit(_db, **_kwargs):
        return None

    def _delete(ref: str, *, expected_hospital_id: uuid.UUID) -> None:
        deleted.append((ref, expected_hospital_id))

    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "acquire_hospital_advisory_lock", _lock)
    monkeypatch.setattr(essence, "write_audit_log", _audit)
    monkeypatch.setattr(essence, "store_asset_bytes", lambda **_kwargs: asset_ref)
    monkeypatch.setattr(essence, "delete_asset_ref", _delete)

    # When: persistence fails after the canonical object was written.
    with pytest.raises(SQLAlchemyError):
        await essence.upload_source_file(
            hospital_id=hospital_id,
            source_type=SourceType.PHOTO_DOCTOR,
            title="원장 사진",
            file=UploadFile(filename="doctor.webp", file=BytesIO(_png())),
            is_public=True,
            asset_kind="VERIFIED_REAL_PERSON",
            photo_source_owner="홍길동 원장",
            photo_rights_basis="OWNER_CONSENT",
            photo_evidence_reference="consent/doctor-1",
            operator_note=None,
            skip_revalidate=False,
            db=db,
            actor=SimpleNamespace(email="owner@example.com"),
        )

    # Then: rollback cleanup targets the exact tenant-owned object.
    assert db.rolled_back is True
    assert deleted == [(asset_ref, hospital_id)]


async def test_reapproval_patch_server_stamps_actor_without_publishing(monkeypatch) -> None:
    # Given: a private legacy photo and an authenticated operator's explicit evidence.
    hospital_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(),
        source_type=SourceType.PHOTO_DOCTOR,
        title="기존 원장 사진",
        url=None,
        raw_text=None,
        operator_note=None,
        source_metadata={},
        file_url="gs://bucket/legacy.png",
        is_public=False,
        photo_source_owner=None,
        photo_rights_basis=None,
        photo_evidence_reference=None,
        photo_verified_by=None,
        photo_verified_at=None,
        status="PENDING",
        process_error=None,
        processed_at=None,
        content_hash=None,
    )

    async def _lock(_db, _hospital_id):
        return None

    async def _source(_db, _hospital_id, _source_id):
        return source

    async def _hospital(_db, _hospital_id):
        return SimpleNamespace(status="DRAFT", site_live=False)

    monkeypatch.setattr(essence, "acquire_hospital_advisory_lock", _lock)
    monkeypatch.setattr(essence, "_get_source_or_404", _source)
    monkeypatch.setattr(essence, "_get_hospital_or_404", _hospital)
    monkeypatch.setattr(essence, "_serialize_source", lambda saved: saved)
    actor = SimpleNamespace(email="operator@example.com")

    # When: classification and provenance are saved in one PATCH.
    saved = await essence.patch_source(
        hospital_id=hospital_id,
        source_id=source.id,
        body=SourceAssetPatch(
            source_metadata={
                "asset_kind": "VERIFIED_REAL_PERSON",
                "approved_usage": ["DOCTOR_IDENTITY"],
            },
            photo_source_owner="홍길동 원장",
            photo_rights_basis="OWNER_CONSENT",
            photo_evidence_reference="consent/legacy-42",
        ),
        db=_PatchDB(),
        actor=actor,
    )

    # Then: the server stamps identity/time but does not publish as a side effect.
    assert saved.photo_verified_by == actor.email
    assert saved.photo_verified_at is not None
    assert saved.photo_evidence_reference == "consent/legacy-42"
    assert saved.is_public is False
