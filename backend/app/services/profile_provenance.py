"""프로필 필드 출처 원장 — 마케터 입력(OPERATOR)·자동 입력(AUTOFILL)·출처 모름(LEGACY).

`hospitals.profile_field_provenance`(0091) 모양::

    {"business_hours": {"origin": "OPERATOR", "actor": "ae@…", "at": "2026-10-14T…"},
     "address": {"origin": "AUTOFILL", "source": "naver", "run_id": "…", "at": "…",
                 "conflict": {"source": "naver", "value": "…", "observed_at": "…"}}}

규칙(D30·D34, 대표 답변 Q1):

- 마케터가 입력·수정·비운 칸은 언제나 마케터 값이 이긴다. 자동 입력은 OPERATOR 필드를 값이 비어
  있어도 덮지 않는다 — 사람이 일부러 지운 칸을 다시 채우지 않는다.
- 자동 수집 값이 마케터 값과 다르면 ``conflict``로만 남긴다(사고·Slack 없음).
- ``system:`` actor(배치·CLI)의 저장은 마케터 입력이 아니다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital

ORIGIN_OPERATOR = "OPERATOR"
ORIGIN_AUTOFILL = "AUTOFILL"
ORIGIN_LEGACY = "LEGACY"

SYSTEM_ACTOR_PREFIX = "system:"

#: 자동 입력이 채울 수 있는 병원 필드 — 원장 백필의 LEGACY 표시도 이 목록만 본다.
AUTOFILL_APPLICABLE_FIELDS: tuple[str, ...] = (
    "director_name",
    "director_career",
    "director_philosophy",
    "address",
    "phone",
    "business_hours",
    "website_url",
    "blog_url",
    "kakao_channel_url",
    "naver_place_url",
    "naver_place_id",
    "region",
    "specialties",
    "keywords",
    "competitors",
    "treatments",
)

#: 원장에 남기지 않는 PATCH 변경 이름 — 병원 열이 아니다(의료진 집합은 director_*로 파생해 남긴다).
_NON_COLUMN_CHANGES = frozenset({"physicians"})


def is_empty_value(value: Any) -> bool:
    """자동 입력이 '빈 칸'으로 보는 값. 진료시간처럼 값이 모두 빈 객체도 빈 칸이다."""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return all(is_empty_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(is_empty_value(item) for item in value)
    return False


def ledger(hospital: Any) -> dict[str, dict[str, Any]]:
    raw = getattr(hospital, "profile_field_provenance", None)
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, Mapping)}


def origin_of(hospital: Any, field: str) -> str | None:
    entry = ledger(hospital).get(field)
    return str(entry.get("origin")) if entry and entry.get("origin") else None


def is_operator_owned(hospital: Any, field: str) -> bool:
    """마케터가 손댄 필드인가. 값이 비어 있어도 참이다."""

    return origin_of(hospital, field) == ORIGIN_OPERATOR


def is_system_actor(actor: str | None) -> bool:
    return bool(actor) and str(actor).startswith(SYSTEM_ACTOR_PREFIX)


def _iso(at: datetime | None) -> str:
    return (at or datetime.now(UTC)).isoformat()


def _normalized(value: Any) -> Any:
    """비교용 정규화 — 화면이 전체 객체를 다시 보내 생긴 표기 차이를 변경으로 보지 않는다."""

    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        items = {str(key): _normalized(item) for key, item in value.items()}
        return {key: item for key, item in items.items() if item is not None} or None
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value] or None
    return value


def semantically_changed(before: Any, after: Any) -> bool:
    return _normalized(before) != _normalized(after)


def _store(hospital: Any, entries: dict[str, dict[str, Any]]) -> None:
    # 새 dict를 대입해야 JSON 열의 변경이 ORM에 잡힌다.
    hospital.profile_field_provenance = entries


def record_operator_fields(
    hospital: Any,
    fields: Iterable[str],
    *,
    actor: str | None,
    at: datetime | None = None,
) -> list[str]:
    """사람이 바꾼 필드를 OPERATOR로 적는다. ``system:`` actor면 아무것도 적지 않는다."""

    if is_system_actor(actor):
        return []
    entries = ledger(hospital)
    recorded: list[str] = []
    for field in dict.fromkeys(fields):
        if field in _NON_COLUMN_CHANGES:
            continue
        entries[field] = {"origin": ORIGIN_OPERATOR, "actor": actor, "at": _iso(at)}
        recorded.append(field)
    if recorded:
        _store(hospital, entries)
    return recorded


def record_autofill_fields(
    hospital: Any,
    sources: Mapping[str, str],
    *,
    run_id: uuid.UUID | str | None,
    at: datetime | None = None,
) -> None:
    """자동 입력이 채운 필드를 AUTOFILL로 적는다. OPERATOR 필드는 절대 바꾸지 않는다."""

    entries = ledger(hospital)
    stamp = _iso(at)
    changed = False
    for field, source in sources.items():
        if (entries.get(field) or {}).get("origin") == ORIGIN_OPERATOR:
            continue
        entries[field] = {
            "origin": ORIGIN_AUTOFILL,
            "source": source,
            "run_id": str(run_id) if run_id else None,
            "at": stamp,
        }
        changed = True
    if changed:
        _store(hospital, entries)


def record_conflict(
    hospital: Any,
    field: str,
    *,
    source: str,
    value: Any,
    at: datetime | None = None,
) -> None:
    """자동 수집 값이 현재 값과 다르다는 사실만 남긴다. 값은 바꾸지 않는다."""

    entries = ledger(hospital)
    entry = dict(entries.get(field) or {"origin": ORIGIN_LEGACY})
    entry["conflict"] = {"source": source, "value": value, "observed_at": _iso(at)}
    entries[field] = entry
    _store(hospital, entries)


def serialize_ledger(hospital: Any) -> dict[str, dict[str, Any]]:
    """Admin 응답용 원장(정보 화면 배지·충돌 회색 줄)."""

    return ledger(hospital)


async def backfill_provenance_from_audit(
    db: AsyncSession, hospital: Hospital, *, at: datetime | None = None
) -> dict[str, list[str]]:
    """원장이 없는 옛 병원의 출처를 감사 로그로 복원한다(덮어쓰지 않는다, 멱등).

    - 감사 로그 ``update_profile``의 사람 actor(``system:`` 아님)가 바꾼 필드 → OPERATOR.
    - 값은 있는데 기록이 없는 자동 입력 대상 필드 → LEGACY(어차피 '빈 칸만' 규칙에 걸린다).
    """

    entries = ledger(hospital)
    rows = (
        await db.execute(
            select(AdminAuditLog.actor, AdminAuditLog.detail, AdminAuditLog.created_at)
            .where(
                AdminAuditLog.hospital_id == hospital.id,
                AdminAuditLog.action == "update_profile",
            )
            .order_by(AdminAuditLog.created_at)
        )
    ).all()
    operator_fields: dict[str, dict[str, Any]] = {}
    for actor, detail, created_at in rows:
        if is_system_actor(actor):
            continue
        changed = (detail or {}).get("changed_fields") if isinstance(detail, Mapping) else None
        for field in changed or []:
            if not isinstance(field, str) or field in _NON_COLUMN_CHANGES:
                continue
            operator_fields[field] = {
                "origin": ORIGIN_OPERATOR,
                "actor": actor,
                "at": _iso(created_at),
                "backfilled": True,
            }
    added_operator: list[str] = []
    for field, entry in operator_fields.items():
        if field not in entries:
            entries[field] = entry
            added_operator.append(field)
    added_legacy: list[str] = []
    for field in AUTOFILL_APPLICABLE_FIELDS:
        if field in entries:
            continue
        if not is_empty_value(getattr(hospital, field, None)):
            entries[field] = {"origin": ORIGIN_LEGACY, "at": _iso(at), "backfilled": True}
            added_legacy.append(field)
    if added_operator or added_legacy:
        _store(hospital, entries)
    return {"operator": sorted(added_operator), "legacy": sorted(added_legacy)}
