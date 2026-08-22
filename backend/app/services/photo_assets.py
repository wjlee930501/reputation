"""공개 표면에 나가는 병원 사진의 시각 역할(asset_kind) 정책.

Admin 업로드/수정 경로와 공개 표면 직렬화가 같은 판단을 쓰도록 순수 함수만 둔다.
HTTP 오류로 바꾸는 책임은 호출하는 API 계층이 가진다.
"""

from __future__ import annotations

from app.models.essence import PHOTO_SOURCE_TYPES, SourceType

DOCTOR_ASSET_KINDS: dict[str, list[str]] = {
    "VERIFIED_REAL_PERSON": ["DOCTOR_IDENTITY"],
    "EDITORIAL_GRAPHIC": ["CONTENT_EDITORIAL"],
}

FACILITY_ASSET_KINDS: dict[str, list[str]] = {
    "VERIFIED_FACILITY": ["HERO", "GALLERY"],
    "EDITORIAL_GRAPHIC": ["CONTENT_EDITORIAL"],
}

LEGACY_ASSET_KIND_SOURCE = "LEGACY_BACKFILL"

DERIVED_METADATA_KEYS = frozenset(
    {
        "asset_kind",
        "approved_usage",
        "original_filename",
        "asset_kind_source",
        "needs_operator_review",
    }
)


def allowed_photo_asset_kinds(source_type: SourceType) -> dict[str, list[str]]:
    """사진 종류별로 허용되는 asset_kind와 그때 파생되는 approved_usage."""
    if source_type == SourceType.PHOTO_DOCTOR:
        return dict(DOCTOR_ASSET_KINDS)
    return dict(FACILITY_ASSET_KINDS)


def legacy_photo_asset_kind(source_type: SourceType) -> str:
    """분류 이전에 저장된 사진이 지금 렌더링되는 모습을 그대로 유지하는 asset_kind.

    공개 표면은 이미 분류가 없는 시설 사진을 hero/gallery 후보로 쓰고, 원장 사진은
    identity로 쓰지 않는다. 복구 값은 그 동작을 그대로 굳혀서 legacy 행이 사라지거나
    확인 없이 원장 identity로 승격되지 않게 한다.
    """
    if source_type == SourceType.PHOTO_DOCTOR:
        return "EDITORIAL_GRAPHIC"
    return "VERIFIED_FACILITY"


def is_classified_photo_metadata(metadata: object) -> bool:
    """운영자 분류가 이미 저장돼 있는지."""
    return isinstance(metadata, dict) and isinstance(metadata.get("asset_kind"), str)


def recovered_photo_metadata(
    source_type: SourceType,
    metadata: object,
) -> dict[str, object]:
    """asset_kind가 없는 legacy 사진에 채울 복구 분류.

    AE 확인 전까지 승격되지 않도록 보수적인 역할만 부여하고, 다시 확인해야 한다는
    표시를 함께 남긴다. 사진이 아닌 자료는 분류 대상이 아니다.
    """
    base = metadata if isinstance(metadata, dict) else {}
    if source_type not in PHOTO_SOURCE_TYPES:
        return dict(base)
    kind = legacy_photo_asset_kind(source_type)
    preserved = {key: value for key, value in base.items() if key not in DERIVED_METADATA_KEYS}
    original_filename = base.get("original_filename")
    return {
        **preserved,
        "original_filename": original_filename if isinstance(original_filename, str) else "photo",
        "asset_kind": kind,
        "approved_usage": list(allowed_photo_asset_kinds(source_type)[kind]),
        "asset_kind_source": LEGACY_ASSET_KIND_SOURCE,
        "needs_operator_review": True,
    }


def effective_photo_metadata(
    source_type: SourceType,
    metadata: object,
) -> dict[str, object]:
    """읽기 경로에서 쓸 확정 메타데이터. 저장을 바꾸지 않고 legacy 공백만 메운다."""
    if is_classified_photo_metadata(metadata):
        return dict(metadata)  # type: ignore[arg-type]
    return recovered_photo_metadata(source_type, metadata)
