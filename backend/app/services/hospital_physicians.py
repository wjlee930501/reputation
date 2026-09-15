"""의료진 행과 병원 단위 `director_*` 컬럼의 동기화 규칙.

공개 표면·llms.txt·JSON-LD는 계속 `hospitals.director_*`를 읽는다. 의료진을 행으로
옮긴 뒤에도 그 값이 대표 의료진과 어긋나지 않도록, 저장할 때마다 대표 행에서 파생한다.
HTTP 오류로 바꾸는 책임은 호출하는 API 계층이 가진다 — 여기는 순수 함수만 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: `hospitals.director_name` 컬럼 길이. 공동원장을 이어 붙여도 저장이 깨지지 않게 자른다.
DIRECTOR_NAME_MAX_LENGTH = 100

NAME_SEPARATOR = " · "


class PhysicianNameRequired(ValueError):
    """비어 있지 않은 목록인데 이름이 있는 행이 하나도 없다."""


def normalized_physicians(items: Sequence[Mapping]) -> list[dict]:
    """저장할 의료진 목록을 정규화한다 — 원본을 바꾸지 않고 새 dict를 만든다.

    이름이 빈 행은 화면의 빈 입력 칸이므로 버린다. 대표 표시가 하나도 없으면 첫 행을
    대표로 삼는다 — 대표가 없으면 병원 단위 `director_*`를 파생할 수 없다.
    """
    cleaned: list[dict] = []
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        cleaned.append({**dict(item), "name": name})

    if items and not cleaned:
        raise PhysicianNameRequired("의료진 이름을 한 명 이상 입력해 주세요.")

    if cleaned and not any(item.get("is_representative") for item in cleaned):
        cleaned = [
            {**item, "is_representative": index == 0} for index, item in enumerate(cleaned)
        ]
    return cleaned


def _representatives(physicians: Sequence[Mapping]) -> list[Mapping]:
    return sorted(
        (item for item in physicians if item.get("is_representative")),
        key=lambda item: (item.get("display_order") or 0),
    )


def _career_block(physician: Mapping) -> str:
    """여러 명일 때 약력이 누구 것인지 남긴다 — 직함이 없으면 이름만 쓴다."""
    title = (physician.get("title") or "").strip()
    name = physician.get("name") or ""
    label = f"{title} {name}".strip()
    return f"[{label}] {physician.get('career')}"


def derive_director_fields(physicians: Sequence[Mapping]) -> dict[str, object]:
    """대표 의료진에서 파생한 병원 단위 표시값.

    빈 목록이면 빈 dict를 돌려준다 — 의료진을 비우는 것과 원장 정보를 지우는 것은
    다른 결정이고, 원장 이름은 필수 항목이라 조용히 비우면 공개가 막힌다.
    `director_philosophy`는 의료진 행에 없는 값이므로 건드리지 않는다.
    """
    representatives = _representatives(physicians)
    if not representatives:
        return {}

    name = NAME_SEPARATOR.join(item["name"] for item in representatives)
    if len(representatives) == 1:
        career = representatives[0].get("career")
    else:
        blocks = [_career_block(item) for item in representatives if item.get("career")]
        career = "\n".join(blocks) if blocks else None

    fields: dict[str, object] = {"director_name": name[:DIRECTOR_NAME_MAX_LENGTH]}
    # 파생값이 비었다는 것은 "의료진 행에 그 정보가 없다"는 뜻이지 "지우라"는 뜻이 아니다.
    # 덮어쓰면 화면에서 약력·자격을 입력하지 않은 저장 한 번이 기존 값을 조용히 날린다.
    if career:
        fields["director_career"] = career
    credentials = representatives[0].get("credentials")
    if credentials:
        fields["director_credentials"] = credentials
    return fields


__all__ = (
    "DIRECTOR_NAME_MAX_LENGTH",
    "PhysicianNameRequired",
    "derive_director_fields",
    "normalized_physicians",
)
