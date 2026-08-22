"""공개 사진의 권리 근거 정책 — 마이그레이션 0052의 CHECK 제약과 같은 계약을 앱이 쓴다.

`ck_public_photo_requires_provenance`는 공개된 PHOTO_* 행에 소유자·권리 근거·증빙
위치·확인자·확인 시각 다섯 값을 요구한다. 앱이 이 계약을 모르면 저장은 IntegrityError로
죽고(운영자에게는 500), 무엇이 비었는지도 알려 줄 수 없다. 여기서는 순수 함수로
"무엇이 비었는지"와 "무엇을 저장할지"만 판단하고, HTTP 오류로 바꾸는 책임은 API 계층이
갖는다(`photo_assets.py`와 같은 분담).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# 0052의 CHECK가 허용하는 권리 근거. 문자열이 바뀌면 저장이 다시 제약에 걸리므로
# 값을 늘릴 때는 반드시 마이그레이션과 함께 바꾼다.
PHOTO_RIGHTS_BASES: tuple[str, ...] = ("LICENSE", "OWNER_CONSENT")

RIGHTS_BASIS_LABELS: dict[str, str] = {
    "LICENSE": "라이선스 보유",
    "OWNER_CONSENT": "촬영 대상·소유자 동의",
}

PROVENANCE_FIELD_LABELS: dict[str, str] = {
    "photo_source_owner": "사진 소유자",
    "photo_rights_basis": "권리 근거",
    "photo_evidence_reference": "증빙 위치",
    "photo_verified_by": "확인한 담당자",
    "photo_verified_at": "확인 시각",
}

SOURCE_OWNER_MAX_LENGTH = 200
EVIDENCE_REFERENCE_MAX_LENGTH = 500
VERIFIED_BY_MAX_LENGTH = 320


class InvalidPhotoRightsBasis(ValueError):
    """운영자가 고른 권리 근거가 저장 가능한 값이 아니다."""


@dataclass(frozen=True)
class PhotoProvenanceInput:
    """운영자가 이번 요청에서 보낸 권리 근거. 보내지 않은 값은 None으로 남는다."""

    source_owner: str | None = None
    rights_basis: str | None = None
    evidence_reference: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.source_owner, self.rights_basis, self.evidence_reference))


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def normalize_rights_basis(value: object) -> str | None:
    """대소문자만 정리한다. 저장 못 하는 값은 제약에 닿기 전에 여기서 걸러진다."""
    cleaned = _clean(value, 32)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if normalized not in PHOTO_RIGHTS_BASES:
        raise InvalidPhotoRightsBasis(cleaned)
    return normalized


def normalize_provenance_input(
    source_owner: object = None,
    rights_basis: object = None,
    evidence_reference: object = None,
) -> PhotoProvenanceInput:
    return PhotoProvenanceInput(
        source_owner=_clean(source_owner, SOURCE_OWNER_MAX_LENGTH),
        rights_basis=normalize_rights_basis(rights_basis),
        evidence_reference=_clean(evidence_reference, EVIDENCE_REFERENCE_MAX_LENGTH),
    )


def apply_photo_provenance(
    source,
    provenance: PhotoProvenanceInput,
    *,
    verified_by: str,
    now: datetime | None = None,
) -> bool:
    """운영자가 보낸 값만 행에 반영하고, 확인자·확인 시각은 서버가 찍는다.

    확인 기록을 클라이언트가 보내게 두면 근거 없는 사진도 "확인됨"으로 저장할 수 있다.
    값을 하나라도 새로 쓴 경우에만 확인 기록을 갱신해, 손대지 않은 행의 기존 확인
    이력이 조용히 지금 시각으로 바뀌지 않게 한다.
    """
    changed = False
    if provenance.source_owner is not None:
        source.photo_source_owner = provenance.source_owner
        changed = True
    if provenance.rights_basis is not None:
        source.photo_rights_basis = provenance.rights_basis
        changed = True
    if provenance.evidence_reference is not None:
        source.photo_evidence_reference = provenance.evidence_reference
        changed = True
    if changed:
        source.photo_verified_by = _clean(verified_by, VERIFIED_BY_MAX_LENGTH)
        source.photo_verified_at = now or datetime.now(timezone.utc)
    return changed


def missing_photo_provenance(source) -> list[str]:
    """0052의 CHECK가 요구하는 값 중 비어 있는 컬럼명. 공개 가능하면 빈 목록."""
    missing: list[str] = []
    if not _clean(getattr(source, "photo_source_owner", None), SOURCE_OWNER_MAX_LENGTH):
        missing.append("photo_source_owner")
    if getattr(source, "photo_rights_basis", None) not in PHOTO_RIGHTS_BASES:
        missing.append("photo_rights_basis")
    if not _clean(
        getattr(source, "photo_evidence_reference", None), EVIDENCE_REFERENCE_MAX_LENGTH
    ):
        missing.append("photo_evidence_reference")
    if not _clean(getattr(source, "photo_verified_by", None), VERIFIED_BY_MAX_LENGTH):
        missing.append("photo_verified_by")
    if getattr(source, "photo_verified_at", None) is None:
        missing.append("photo_verified_at")
    return missing


def photo_provenance_is_complete(source) -> bool:
    return not missing_photo_provenance(source)


def describe_missing_provenance(missing: list[str]) -> str:
    """운영자가 지금 무엇을 채워야 하는지 한 문장으로."""
    operator_inputs = [
        PROVENANCE_FIELD_LABELS[field]
        for field in missing
        if field in {"photo_source_owner", "photo_rights_basis", "photo_evidence_reference"}
    ]
    if not operator_inputs:
        # 확인자·확인 시각은 서버가 찍는 값이라 비어 있을 이유가 없다.
        return "사진 공개에 필요한 권리 근거 확인 기록이 없습니다. 출처 정보를 다시 저장해 주세요."
    return (
        f"사진을 공개하려면 {', '.join(operator_inputs)}을(를) 입력해야 합니다. "
        "권리 근거는 라이선스 보유 또는 촬영 대상·소유자 동의 중 하나를 고릅니다."
    )


def serialize_photo_provenance(source) -> dict[str, object]:
    """운영 화면이 '공개 가능한지'와 '무엇이 비었는지'를 그대로 보여줄 수 있는 형태."""
    missing = missing_photo_provenance(source)
    verified_at = getattr(source, "photo_verified_at", None)
    return {
        "source_owner": getattr(source, "photo_source_owner", None),
        "rights_basis": getattr(source, "photo_rights_basis", None),
        "rights_basis_label": RIGHTS_BASIS_LABELS.get(
            getattr(source, "photo_rights_basis", None) or ""
        ),
        "evidence_reference": getattr(source, "photo_evidence_reference", None),
        "verified_by": getattr(source, "photo_verified_by", None),
        "verified_at": verified_at.isoformat() if verified_at else None,
        "is_complete": not missing,
        "missing_fields": missing,
        "missing_message": describe_missing_provenance(missing) if missing else None,
    }
