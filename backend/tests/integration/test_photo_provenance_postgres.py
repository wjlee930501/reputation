"""Real PostgreSQL proof for the public-photo provenance constraint and brand enum."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.essence import HospitalSourceAsset, SourceType
from app.models.hospital import Hospital


def _hospital(session: Session) -> Hospital:
    hospital = Hospital(name="사진 검증 병원", slug=f"photo-proof-{uuid.uuid4().hex}")
    session.add(hospital)
    session.flush()
    return hospital


def test_public_photo_without_provenance_is_rejected_by_postgres(pg_conn) -> None:
    # Given: an otherwise valid public photo with no operator rights evidence.
    session = Session(bind=pg_conn, join_transaction_mode="create_savepoint")
    hospital = _hospital(session)
    session.add(
        HospitalSourceAsset(
            hospital_id=hospital.id,
            source_type=SourceType.PHOTO_DOCTOR,
            title="미검증 원장 사진",
            file_url="gs://bucket/unverified.png",
            is_public=True,
        )
    )

    # When / Then: the durable database boundary refuses publication.
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_verified_brand_graphic_can_be_public_in_postgres(pg_conn) -> None:
    # Given: an official brand graphic with exact rights and verifier provenance.
    session = Session(bind=pg_conn, join_transaction_mode="create_savepoint")
    hospital = _hospital(session)
    asset = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.PHOTO_BRAND,
        title="공식 로고",
        file_url="gs://bucket/logo.png",
        source_metadata={
            "asset_kind": "VERIFIED_BRAND_GRAPHIC",
            "approved_usage": ["LOGO", "HERO"],
        },
        is_public=True,
        photo_source_owner="사진 검증 병원",
        photo_rights_basis="LICENSE",
        photo_evidence_reference="license/logo-1",
        photo_verified_by="owner@example.com",
        photo_verified_at=datetime.now(timezone.utc),
    )
    session.add(asset)

    # When: the exact brand contract is persisted.
    session.flush()

    # Then: PostgreSQL accepts the new semantic enum and provenance constraint.
    assert asset.id is not None
