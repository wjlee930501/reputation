"""공개 표면 시각 승인 판정 — 목록·상세·점검 스크립트가 같은 답을 내야 한다.

이 판정이 화면마다 갈리면 운영자는 목록에서 `✓`를, 상세에서 `7/8 진행 필요`를 본다.
실제로 그런 상태였고, 목록만 보는 운영자는 5개 병원에 할 일이 있다는 사실을 알 수
없었다(O-2). 그래서 기준을 여기 한 곳에 두고 API와 스크립트가 함께 쓴다.

admin/lib/clinic-visual-readiness.ts가 같은 규칙의 화면 쪽 구현이다 — 항목과 판정이
어긋나면 목록과 상세가 다시 갈라진다.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.services.hospital_logo import is_stored_logo_ref

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
ACCESS_MODES = frozenset({"urgent", "appointment", "specialist"})

# 화면(admin/lib/clinic-visual-readiness.ts)이 쓰는 라벨과 같아야 배지 문구가 일치한다.
VISUAL_ITEM_LABELS: dict[str, str] = {
    "logo": "공식 로고",
    "primary_color": "대표색 1개",
    "hero_copy": "첫 화면 카피",
    "access_mode": "첫 화면 정보 우선순위",
}


@dataclass(frozen=True)
class VisualReadiness:
    """승인이 남은 항목. 사진은 필수가 아니므로 절대 포함되지 않는다."""

    missing: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return not self.missing

    @property
    def missing_labels(self) -> tuple[str, ...]:
        return tuple(VISUAL_ITEM_LABELS.get(key, key) for key in self.missing)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def evaluate_visual_readiness(source: Any) -> VisualReadiness:
    """Hospital ORM 객체 또는 dict 어느 쪽이든 같은 기준으로 판정한다."""

    def field(name: str) -> Any:
        if isinstance(source, dict):
            return source.get(name)
        return getattr(source, name, None)

    missing: list[str] = []
    # 값이 있는 것으로는 부족하다 — 공개 화면이 실제로 서빙할 수 있는 자산이어야 한다.
    # 외부 CDN 주소는 저장돼 있어도 헤더에 아무것도 그리지 못한다(O-5).
    if not is_stored_logo_ref(_text(field("logo_url"))):
        missing.append("logo")
    if not HEX_COLOR.fullmatch(_text(field("brand_primary_color"))):
        missing.append("primary_color")
    if not (_text(field("hero_headline")) or _text(field("hero_description"))):
        missing.append("hero_copy")
    if _text(field("site_access_mode")) not in ACCESS_MODES:
        missing.append("access_mode")

    return VisualReadiness(missing=tuple(missing))
