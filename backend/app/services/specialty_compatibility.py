"""병원 대표 진료과와 어울리지 않는 측정 질문 주제를 거르는 좁은 규칙.

2026-09 신기한속내과연합의원(내과)에 V0 자동 시드가 `영상의학과` 질문(61810ef4 "대구 동구
영상의학과 병원 어디가 좋은지 비교해줘")을 만들었고, 그 슬롯은 참고자료를 끝내 확보하지
못해 3일을 소진한 뒤 주제 교체로 또 영상의학과 질문(d7a5603e)을 받았다. 병원의
`specialties` 목록에는 `영상의학과`가 들어 있었다 — 장비·판독을 갖춘 내과가 흔히 적는
값이라 목록만으로는 막을 수 없다. 그래서 **대표 진료과**(병원 이름에 쓰인 진료과, 없으면
진료과 목록의 첫 값)로 판정한다.

규칙은 일부러 좁다. 표에 적힌 조합만 막고, 병원 이름이 막히는 진료과 자체를 말하면
(예: "OO내과영상의학과의원") 막지 않는다. 새 조합은 `INCOMPATIBLE_TOPIC_DEPARTMENTS`에
한 줄로 더한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# 대표 진료과 → 그 병원에 배정하지 않을 측정 질문 진료과.
INCOMPATIBLE_TOPIC_DEPARTMENTS: Mapping[str, frozenset[str]] = {
    "내과": frozenset({"영상의학과"}),
}

_SPACES = re.compile(r"\s+")


def _compact(value: object) -> str:
    return _SPACES.sub("", str(value or ""))


def _department_stem(department: str) -> str:
    """'영상의학과' → '영상의학' — '영상의학 전문의' 같은 표기도 잡는다."""

    return department[:-1] if department.endswith("과") and len(department) > 2 else department


def _mentions(text: str, department: str) -> bool:
    return _department_stem(department) in text


def hospital_primary_departments(hospital: object) -> frozenset[str]:
    """병원 이름에 쓰인 진료과와 진료과 목록의 첫 값에서 대표 진료과를 읽는다."""

    name = _compact(getattr(hospital, "name", None))
    specialties = getattr(hospital, "specialties", None) or []
    first_specialty = _compact(specialties[0]) if specialties else ""
    departments: set[str] = set()
    for department in INCOMPATIBLE_TOPIC_DEPARTMENTS:
        if _mentions(name, department) or _mentions(first_specialty, department):
            departments.add(department)
    return frozenset(departments)


def forbidden_topic_departments(hospital: object) -> frozenset[str]:
    """이 병원에 배정하지 않을 질문 진료과. 병원 이름이 그 진료과를 말하면 막지 않는다."""

    if hospital is None:
        return frozenset()
    name = _compact(getattr(hospital, "name", None))
    forbidden: set[str] = set()
    for department in hospital_primary_departments(hospital):
        forbidden |= INCOMPATIBLE_TOPIC_DEPARTMENTS.get(department, frozenset())
    return frozenset(item for item in forbidden if not _mentions(name, item))


def _target_texts(target: Any) -> Iterable[str]:
    """질문의 진료과를 읽을 텍스트. 구조화된 `specialty`가 있으면 그것만 쓴다.

    질문 문장(`name`)은 "내과와 영상의학과 중 어디로 가야 하나요"처럼 다른 진료과를 비교로만
    말할 수 있다. 문장은 `specialty`가 비어 있을 때만 대신 읽는다.
    """

    if isinstance(target, Mapping):
        specialty, name = target.get("specialty"), target.get("name")
    else:
        specialty, name = getattr(target, "specialty", None), getattr(target, "name", None)
    structured = _compact(specialty)
    if structured:
        return (structured,)
    fallback = _compact(name)
    return (fallback,) if fallback else ()


def target_conflicts_with_hospital(target: Any, hospital: object) -> bool:
    """측정 질문(대상 진료과·질문 문장)이 이 병원 대표 진료과와 어울리지 않는가."""

    forbidden = forbidden_topic_departments(hospital)
    if not forbidden:
        return False
    texts = list(_target_texts(target))
    return any(_mentions(text, department) for text in texts for department in forbidden)


def target_fits_hospital(target: Any, hospital: object) -> bool:
    return not target_conflicts_with_hospital(target, hospital)
