"""등록 경쟁사 투영 — 보고서 조립(원장 뷰·전략 요약)과 재판정이 **같은 규칙**으로 기록의 `competitor_mentions`를 거른다.

- 등록 목록이 비어 있으면 기록을 그대로 쓴다(옛 보고서·테스트의 종전 동작).
- 등록 목록이 있으면 등록되지 않은 이름의 판정은 싣지 않고(등록에서 뺀 이름은 기록에 남아도 보고서에 없다), 남긴 판정은
  **등록 표기**로 이름을 맞춘다 — "가나 의원"과 "가나의원"이 두 병원으로 갈라져 50%가 100%·0%로 찢어지지 않게.
- 한 기록 안에 같은 병원의 항목이 여럿이면 확정 판정(bool)을 먼저, 없으면 미확정 하나만 남긴다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def competitor_key(name: str) -> str:
    """등록 표기와 판정 표기의 띄어쓰기·기호 차이를 접는다(`sov_engine._normalize_for_prefilter`와 같은 규칙)."""
    return re.sub(r"[\s\W]+", "", str(name or ""), flags=re.UNICODE)


def registered_competitor_names(hospital: Any) -> list[str]:
    """병원 정보 탭에 등록된 경쟁 병원 이름(공백 정리·중복 제거, 순서 유지)."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in getattr(hospital, "competitors", None) or []:
        name = str(raw or "").strip()
        key = competitor_key(name)
        if name and key and key not in seen:
            seen.add(key)
            names.append(name)
    return names


class ScopedRecord:
    """기록의 다른 값은 그대로 두고 `competitor_mentions`만 등록 경쟁사로 거른 읽기 전용 뷰."""

    __slots__ = ("_record", "competitor_mentions")

    def __init__(self, record: Any, mentions: list[dict[str, Any]]) -> None:
        self._record = record
        self.competitor_mentions = mentions

    def __getattr__(self, name: str) -> Any:
        return getattr(self._record, name)


def scope_competitor_mentions(
    mentions: Iterable[Any] | None, registered_names: Sequence[str]
) -> list[dict[str, Any]]:
    spelling = {competitor_key(name): name for name in registered_names}
    chosen: dict[str, dict[str, Any]] = {}
    for item in mentions or []:
        if not isinstance(item, Mapping):
            continue
        key = competitor_key(str(item.get("name") or ""))
        if key not in spelling:
            continue
        projected = {**item, "name": spelling[key]}
        current = chosen.get(key)
        if current is None or (
            not isinstance(current.get("is_mentioned"), bool) and isinstance(projected.get("is_mentioned"), bool)
        ):
            chosen[key] = projected
    return list(chosen.values())


def scope_competitor_records(records: Iterable[Any], registered_names: Sequence[str]) -> list[Any]:
    """등록 목록이 있으면 기록마다 등록 경쟁사만 남긴 뷰를, 없으면 기록 그대로를 돌려준다."""
    if not registered_names:
        return list(records)
    return [ScopedRecord(record, scope_competitor_mentions(getattr(record, "competitor_mentions", None), registered_names)) for record in records]
