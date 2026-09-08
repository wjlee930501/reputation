"""운영자가 노이즈로 제외한 근거 노트 — predicate·identity·로더를 한 곳에서.

제외 표시는 노트를 지우지 않고 `note_metadata["is_noise"]`에 남긴다(제외 해제가 재처리 없이
돌아가야 하므로). 그런데 어떤 노트를 근거에서 뺐는지는 승인의 일부다: 운영자가 뺀 주장으로
계속 생성하면 안 된다. 그래서 승인 시점의 제외 집합 hash를 `evidence_noise_hash`로 남기고,
엄격한 `current` 판정이 그 hash를 다시 비교한다. 자료 snapshot hash와 분리하는 이유는
기존 공개 글의 근거(`public_philosophy`)까지 무효화하지 않기 위해서다.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.essence import HospitalSourceAsset, HospitalSourceEvidenceNote
from app.services.essence_sources import required_text_source_predicate


def is_noise_note(note: Any) -> bool:
    metadata = getattr(note, "note_metadata", None) or {}
    return metadata.get("is_noise") is True


def noise_note_predicate():
    return func.coalesce(
        HospitalSourceEvidenceNote.note_metadata["is_noise"].as_boolean(),
        false(),
    ).is_(True)


def not_noise_note_predicate():
    return func.coalesce(
        HospitalSourceEvidenceNote.note_metadata["is_noise"].as_boolean(),
        false(),
    ).is_(False)


def compute_evidence_noise_hash(excluded_note_ids: Iterable[uuid.UUID | str]) -> str:
    """제외된 노트 id 집합의 identity. 순서·중복·표기 무관, 빈 집합도 고정 hash."""
    parts = sorted({str(uuid.UUID(str(value))) for value in excluded_note_ids})
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _excluded_note_rows_stmt(hospital_ids: list[uuid.UUID]):
    """필수 텍스트 자료(비제외·비사진)에 속한 노이즈 노트만 — readiness의 자료 집합과 같은 경계.

    제외된 자료나 사진 자료의 노트를 세면, readiness가 보지 않는 자료의 노트 토글만으로
    승인이 stale이 된다. 단건·묶음 로더가 각자 문장을 쓰면 이 경계가 갈라지므로 문장은
    여기 하나뿐이다.
    """
    return (
        select(HospitalSourceEvidenceNote.hospital_id, HospitalSourceEvidenceNote.id)
        .join(
            HospitalSourceAsset,
            HospitalSourceAsset.id == HospitalSourceEvidenceNote.source_asset_id,
        )
        .where(
            HospitalSourceEvidenceNote.hospital_id.in_(hospital_ids),
            required_text_source_predicate(),
            noise_note_predicate(),
        )
    )


def _excluded_note_ids_stmt(hospital_id: uuid.UUID):
    return _excluded_note_rows_stmt([hospital_id]).with_only_columns(
        HospitalSourceEvidenceNote.id
    )


async def load_evidence_noise_hash(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    result = await db.execute(_excluded_note_ids_stmt(hospital_id))
    return compute_evidence_noise_hash(result.scalars().all())


async def load_evidence_noise_hashes(
    db: AsyncSession, hospital_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """N개 병원의 제외 집합 identity를 한 문장으로 — 노트가 없는 병원도 빈 집합 hash를 받는다.

    병원마다 조회하면 병원 횡단 화면 하나가 병원 수에 비례하는 쿼리를 낸다. 판정에 쓰는
    hash는 단건 `load_evidence_noise_hash`와 같은 함수·같은 경계로 계산한다.
    """
    ids = list(dict.fromkeys(hospital_ids))
    if not ids:
        return {}
    rows = (await db.execute(_excluded_note_rows_stmt(ids))).all()
    grouped: dict[uuid.UUID, list[uuid.UUID]] = {hospital_id: [] for hospital_id in ids}
    for row in rows:
        grouped.setdefault(row.hospital_id, []).append(row.id)
    return {
        hospital_id: compute_evidence_noise_hash(note_ids)
        for hospital_id, note_ids in grouped.items()
    }


def load_evidence_noise_hash_sync(db: Session, hospital_id: uuid.UUID) -> str:
    result = db.execute(_excluded_note_ids_stmt(hospital_id))
    return compute_evidence_noise_hash(result.scalars().all())
