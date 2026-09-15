"""공개 표면의 의료진 직렬화.

공동원장 병원의 두 번째 원장이 `director_name` 문자열 안에서만 존재하던 시절의 공개
페이지는 한 명분의 사진·약력만 보여 줄 수 있었다. 의료진 행을 그대로 내보내되, 사진은
이미 검수된 근거 자료(공개·미제외·원장 사진·실인물 인증·원장 identity 승인)만 쓴다 —
`director_photo_url`이 지키던 게이트를 의료진 사진에도 똑같이 적용한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.api.public.assets import public_asset_url
from app.models.essence import HospitalSourceAsset, SourceType
from app.services.photo_assets import effective_photo_metadata


def doctor_identity_asset_ids(photos: Sequence[HospitalSourceAsset]) -> set:
    """원장 identity로 공개할 수 있는 사진 자료 id — 호출부가 이미 공개 자료만 넘긴다."""
    approved = set()
    for asset in photos:
        if asset.source_type != SourceType.PHOTO_DOCTOR:
            continue
        metadata = effective_photo_metadata(
            asset.source_type, getattr(asset, "source_metadata", None)
        )
        usage = metadata.get("approved_usage")
        if metadata.get("asset_kind") == "VERIFIED_REAL_PERSON" and isinstance(usage, list):
            if "DOCTOR_IDENTITY" in usage:
                approved.add(asset.id)
    return approved


def _order_key(physician) -> tuple:
    created_at = getattr(physician, "created_at", None)
    # 생성 시각이 없는 행(테스트·백필 직후)이 섞여도 비교가 깨지지 않게 존재 여부를 먼저 본다.
    return (getattr(physician, "display_order", 0) or 0, created_at is None, created_at)


def _ordered(physicians: Sequence) -> list:
    return sorted(physicians, key=_order_key)


def serialize_public_physicians(
    slug: str,
    physicians: Sequence,
    approved_photo_ids: set,
    *,
    safe_credentials,
) -> list[dict]:
    serialized = []
    for physician in _ordered(physicians):
        photo_source_id = getattr(physician, "photo_source_id", None)
        photo_url = (
            public_asset_url(slug, photo_source_id)
            if photo_source_id and photo_source_id in approved_photo_ids
            else None
        )
        serialized.append(
            {
                "id": str(physician.id),
                "name": physician.name,
                "title": getattr(physician, "title", None),
                "specialties": list(getattr(physician, "specialties", None) or []),
                "career": getattr(physician, "career", None),
                "credentials": safe_credentials(getattr(physician, "credentials", None)),
                "photo_url": photo_url,
                "is_representative": bool(getattr(physician, "is_representative", False)),
                "display_order": getattr(physician, "display_order", 0) or 0,
            }
        )
    return serialized


def representative_photo_url(serialized_physicians: Sequence[dict]) -> str | None:
    """대표 의료진에 연결된 유효한 사진. 없으면 None — 호출부가 기존 선택으로 물러난다."""
    for physician in serialized_physicians:
        if physician["is_representative"] and physician["photo_url"]:
            return physician["photo_url"]
    return None


__all__ = (
    "doctor_identity_asset_ids",
    "representative_photo_url",
    "serialize_public_physicians",
)
