"""Admin 의료진 입력 계약과 저장 — 병원 프로파일 저장(PATCH)이 함께 쓰는 부품.

`hospitals.py`는 이미 충분히 크다. 의료진 목록의 검증·치환·직렬화는 여기 둔다.
병원 단위 `director_*` 파생 규칙은 `app/services/hospital_physicians.py`가 소유한다.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import HospitalSourceAsset, SourceType
from app.models.physician import HospitalPhysician
from app.services.hospital_physicians import PhysicianNameRequired, normalized_physicians


class DirectorCredentials(BaseModel):
    """Physician.hasCredential / alumniOf / memberOf 매핑용."""

    medical_school: str | None = Field(None, max_length=200)
    board_certifications: list[str] | None = None
    society_memberships: list[str] | None = None
    license_number: str | None = Field(None, max_length=50)  # 공개 노출 X, 내부 보관


class PhysicianInput(BaseModel):
    """의료진 한 명. `id`가 있으면 기존 행 수정, 없으면 새 행."""

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    title: str | None = Field(None, max_length=50)
    specialties: list[str] = Field(default_factory=list)
    career: str | None = Field(None, max_length=2000)
    credentials: DirectorCredentials | None = None
    photo_source_id: uuid.UUID | None = None
    # 미입력은 "목록 순서대로"이고 0은 "첫 번째로 지정"이다 — 둘을 같은 값으로 두면
    # 화면이 정한 0을 서버가 목록 index로 덮어쓴다.
    display_order: int | None = None
    is_representative: bool = False


def _physician_photo_access_url(hospital_id: uuid.UUID, source_id: uuid.UUID) -> str:
    """Admin 사진 섹션이 이미 쓰는 자료 파일 경로. 서명 URL은 그 라우트가 만든다."""
    return f"/api/admin/hospitals/{hospital_id}/essence/sources/{source_id}/file"


def serialize_physician(
    physician: HospitalPhysician,
    *,
    photo_titles: dict[uuid.UUID, str] | None = None,
) -> dict:
    photo_source_id = physician.photo_source_id
    return {
        "id": str(physician.id),
        "name": physician.name,
        "title": physician.title,
        "specialties": list(physician.specialties or []),
        "career": physician.career,
        "credentials": physician.credentials,
        "photo_source_id": str(photo_source_id) if photo_source_id else None,
        "photo_title": (photo_titles or {}).get(photo_source_id) if photo_source_id else None,
        "photo_access_url": (
            _physician_photo_access_url(physician.hospital_id, photo_source_id)
            if photo_source_id
            else None
        ),
        "display_order": physician.display_order,
        "is_representative": bool(physician.is_representative),
        "created_at": physician.created_at.isoformat() if physician.created_at else None,
    }


async def _load_physician_rows(
    db: AsyncSession, hospital_id: uuid.UUID
) -> list[HospitalPhysician]:
    result = await db.execute(
        select(HospitalPhysician)
        .where(HospitalPhysician.hospital_id == hospital_id)
        .order_by(HospitalPhysician.display_order, HospitalPhysician.created_at)
    )
    return list(result.scalars().all())


async def _photo_titles(
    db: AsyncSession, hospital_id: uuid.UUID, source_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not source_ids:
        return {}
    result = await db.execute(
        select(HospitalSourceAsset.id, HospitalSourceAsset.title).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.id.in_(list(source_ids)),
        )
    )
    return {row[0]: row[1] for row in result.all()}


async def list_physicians(db: AsyncSession, hospital_id: uuid.UUID) -> list[dict]:
    rows = await _load_physician_rows(db, hospital_id)
    titles = await _photo_titles(
        db, hospital_id, {row.photo_source_id for row in rows if row.photo_source_id}
    )
    return [serialize_physician(row, photo_titles=titles) for row in rows]


async def _validated_photo_ids(
    db: AsyncSession, hospital_id: uuid.UUID, source_ids: set[uuid.UUID]
) -> None:
    """사진은 같은 병원의 검수된 원장 사진 자료만 연결할 수 있다."""
    if not source_ids:
        return
    result = await db.execute(
        select(HospitalSourceAsset.id).where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.id.in_(list(source_ids)),
            HospitalSourceAsset.source_type == SourceType.PHOTO_DOCTOR,
        )
    )
    found = {row[0] for row in result.all()}
    missing = source_ids - found
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PHYSICIAN_PHOTO_INVALID",
                "message": "의료진 사진은 같은 병원의 '사진 — 원장' 자료만 연결할 수 있습니다.",
                "invalid_photo_source_ids": sorted(str(value) for value in missing),
            },
        )


def _reject_unknown_ids(
    normalized: list[dict], existing: dict[uuid.UUID, HospitalPhysician]
) -> None:
    """다른 병원의 행이나 이미 삭제된 행의 id는 조용히 새 행으로 만들지 않는다.

    그대로 insert하면 운영자는 '수정했다'고 믿지만 실제로는 중복 행이 생기고, 원래 고치려던
    행은 그대로 남는다. 어느 쪽이 맞는지 서버가 고를 수 없으므로 요청을 거절한다.
    """
    unknown = sorted(
        str(item["id"]) for item in normalized if item.get("id") and item["id"] not in existing
    )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PHYSICIAN_NOT_FOUND",
                "message": "이 병원에 없는 의료진을 수정할 수 없습니다. 화면을 새로고침해 주세요.",
                "unknown_physician_ids": unknown,
            },
        )


async def replace_physicians(
    db: AsyncSession, hospital_id: uuid.UUID, inputs: list[PhysicianInput]
) -> list[dict]:
    """의료진 집합을 제출한 목록으로 치환한다 — id가 있으면 수정, 빠진 행은 삭제.

    커밋은 하지 않는다. 프로파일 저장과 같은 트랜잭션에 들어가야 병원 단위 `director_*`와
    의료진 행이 어긋난 채로 남지 않는다.
    """
    try:
        normalized = normalized_physicians([item.model_dump() for item in inputs])
    except PhysicianNameRequired as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "PHYSICIAN_NAME_REQUIRED", "message": str(exc)},
        ) from exc

    await _validated_photo_ids(
        db,
        hospital_id,
        {item["photo_source_id"] for item in normalized if item.get("photo_source_id")},
    )

    existing = {row.id: row for row in await _load_physician_rows(db, hospital_id)}
    _reject_unknown_ids(normalized, existing)
    kept: set[uuid.UUID] = set()
    resolved: list[dict] = []
    for index, item in enumerate(normalized):
        credentials = item.get("credentials")
        submitted_order = item.get("display_order")
        values = {
            "name": item["name"],
            "title": item.get("title"),
            "specialties": list(item.get("specialties") or []),
            "career": item.get("career"),
            "credentials": credentials,
            "photo_source_id": item.get("photo_source_id"),
            # 0은 화면이 실제로 보낸 첫 순서다 — falsy라는 이유로 index로 바꾸지 않는다.
            "display_order": index if submitted_order is None else submitted_order,
            "is_representative": bool(item.get("is_representative")),
        }
        resolved.append({**item, **values})
        row = existing.get(item.get("id"))
        if row is None:
            row = HospitalPhysician(hospital_id=hospital_id, **values)
            db.add(row)
        else:
            for field, value in values.items():
                setattr(row, field, value)
            kept.add(row.id)

    for row_id, row in existing.items():
        if row_id not in kept:
            await db.delete(row)

    return resolved


__all__ = (
    "DirectorCredentials",
    "PhysicianInput",
    "list_physicians",
    "replace_physicians",
    "serialize_physician",
)
