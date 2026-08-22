"""One rule for "this hospital already exists", shared by every creation path.

`POST /admin/hospitals` refuses to create a second hospital with the same name,
but the lead path (`POST /admin/leads/{id}/convert`) inserted a hospital with no
duplicate check at all. A lead whose hospital was already onboarded therefore
produced a second, empty hospital record and kept showing as "온보딩 대기".

The signals here are exact after normalization — never fuzzy. A wrong match
would attach a lead to somebody else's hospital, which is worse than a duplicate.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import Hospital

MIN_PHONE_DIGITS = 7


def normalize_hospital_name(value: str | None) -> str:
    """공백 접힘 + 소문자. '장편한 외과'와 '장편한외과'를 같은 이름으로 본다."""
    return re.sub(r"\s+", " ", value or "").strip().lower()


def hospital_name_match_key(value: str | None) -> str:
    """이름 비교용 키. `/hospitals/new` 의 SQL 비교와 같은 정규화다."""
    return normalize_hospital_name(value).replace(" ", "")


def matches_hospital_name(candidate: Any, name: str | None) -> bool:
    """후보가 **이름**으로 일치했는지.

    전화나 도메인이 같다는 사실은 "같은 병원일 수 있다"는 신호일 뿐이라 운영자 확인이
    필요하지만, 정규화된 이름이 같으면 새 병원을 만드는 길 자체가 이미 막혀 있다.
    """
    key = hospital_name_match_key(name)
    return bool(key) and hospital_name_match_key(getattr(candidate, "name", None)) == key


def normalize_phone_digits(value: str | None) -> str:
    """숫자만 남긴다. '02-123-4567'과 '021234567'은 같은 번호다."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


def usable_phone_digits(value: str | None) -> str | None:
    """전화번호로 쓸 수 있는 값만. 이메일이나 너무 짧은 문자열은 대조 대상이 아니다."""
    if not value or "@" in value:
        return None
    digits = normalize_phone_digits(value)
    return digits if len(digits) >= MIN_PHONE_DIGITS else None


def normalize_domain_key(value: str | None) -> str:
    return (value or "").strip().lower().rstrip(".")


async def find_duplicate_hospitals(
    db: AsyncSession,
    *,
    name: str | None = None,
    phones: tuple[str | None, ...] = (),
    domain: str | None = None,
    limit: int = 10,
) -> list[Hospital]:
    """이름·전화·도메인 중 하나라도 정확히 일치하는 기존 병원."""
    filters = []

    name_key = hospital_name_match_key(name)
    if name_key:
        filters.append(
            func.replace(func.lower(func.trim(Hospital.name)), " ", "") == name_key
        )

    phone_keys = sorted({digits for digits in (usable_phone_digits(p) for p in phones) if digits})
    if phone_keys:
        filters.append(
            func.regexp_replace(
                func.coalesce(Hospital.phone, ""), r"[^0-9]", "", "g"
            ).in_(phone_keys)
        )

    domain_key = normalize_domain_key(domain)
    if domain_key:
        filters.append(func.lower(Hospital.aeo_domain) == domain_key)

    if not filters:
        return []

    result = await db.execute(
        select(Hospital)
        .where(or_(*filters))
        .order_by(Hospital.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
