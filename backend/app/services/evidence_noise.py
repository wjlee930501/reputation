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

from app.models.essence import HospitalSourceEvidenceNote


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
    """제외된 노트 id 집합의 identity. 순서 무관, 빈 집합도 고정 hash."""
    parts = sorted(str(note_id) for note_id in excluded_note_ids)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _excluded_note_ids_stmt(hospital_id: uuid.UUID):
    return select(HospitalSourceEvidenceNote.id).where(
        HospitalSourceEvidenceNote.hospital_id == hospital_id,
        noise_note_predicate(),
    )


async def load_evidence_noise_hash(db: AsyncSession, hospital_id: uuid.UUID) -> str:
    result = await db.execute(_excluded_note_ids_stmt(hospital_id))
    return compute_evidence_noise_hash(result.scalars().all())


def load_evidence_noise_hash_sync(db: Session, hospital_id: uuid.UUID) -> str:
    result = db.execute(_excluded_note_ids_stmt(hospital_id))
    return compute_evidence_noise_hash(result.scalars().all())
