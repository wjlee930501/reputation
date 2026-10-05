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
    """측정 질문이 이 병원에 배정할 수 없는 주제인가.

    두 규칙 중 하나라도 맞으면 그렇다. (1) 질문의 진료과가 병원 대표 진료과와 어울리지 않는다.
    (2) 질문이 묻는 검사·시술이 병원 키워드에만 있고 승인 프로필의 진료 항목에는 없다
    (`target_names_unoffered_service`).
    """

    if target_names_unoffered_service(target, hospital):
        return True
    forbidden = forbidden_topic_departments(hospital)
    if not forbidden:
        return False
    texts = list(_target_texts(target))
    return any(_mentions(text, department) for text in texts for department in forbidden)


# 질문 문장에서 검사·시술 이름을 읽는 접미. "골밀도검사"·"유방초음파"·"위내시경"처럼 붙여 쓴다.
_SERVICE_TERM = re.compile(
    r"[가-힣A-Za-z0-9]{1,12}(?:검사|시술|수술|주사|내시경|초음파|촬영|치료|요법|접종)"
)
_SERVICE_SUFFIX = re.compile(r"(?:검사|시술|수술|주사|치료|요법|접종)$")


def _target_service_terms(target: Any) -> tuple[str, ...]:
    """질문이 묻는 검사·시술. 구조화된 `treatment`가 있으면 그것만 쓴다."""

    if isinstance(target, Mapping):
        treatment, name = target.get("treatment"), target.get("name")
    else:
        treatment, name = getattr(target, "treatment", None), getattr(target, "name", None)
    structured = _compact(treatment)
    if structured:
        return (structured,)
    # 띄어 쓴 낱말 단위로 읽는다 — 공백을 먼저 지우면 "마산 골밀도검사"가 "마산골밀도검사"가 된다.
    return tuple(dict.fromkeys(_SERVICE_TERM.findall(str(name or ""))))


def _term_keys(term: str) -> tuple[str, ...]:
    """'골밀도검사' → ('골밀도검사', '골밀도'). 접미를 뗀 이름이 두 글자 미만이면 원형만."""

    stem = _SERVICE_SUFFIX.sub("", term)
    return (term, stem) if stem != term and len(stem) >= 2 else (term,)


def target_names_unoffered_service(target: Any, hospital: object) -> bool:
    """질문의 검사·시술이 병원 키워드에만 있고 승인 프로필 진료 항목에는 없는가.

    2026-10-04 강심장내과의원 글이 키워드에만 있던 `골밀도검사`를 이 병원이 하는 검사처럼
    썼다(진료 항목에는 없었다). 키워드는 검색어 후보이지 병원이 제공하는 서비스의 근거가
    아니다. 진료 항목(`treatments`)·진료과(`specialties`)가 비어 있으면 판단할 근거가 없으므로
    막지 않는다.
    """

    if hospital is None:
        return False
    keywords = [_compact(value) for value in (getattr(hospital, "keywords", None) or [])]
    keywords = [value for value in keywords if value]
    offered = [
        _compact(value)
        for field in ("treatments", "specialties", "hero_specialties")
        for value in (getattr(hospital, field, None) or [])
        if isinstance(value, str)
    ]
    offered = [value for value in offered if len(value) >= 2]
    if not keywords or not offered:
        return False
    for term in _target_service_terms(target):
        keys = _term_keys(term)
        in_keywords = any(key in keyword for key in keys for keyword in keywords)
        in_profile = any(
            key in service or service in term for key in keys for service in offered
        )
        if in_keywords and not in_profile:
            return True
    return False


def target_fits_hospital(target: Any, hospital: object) -> bool:
    return not target_conflicts_with_hospital(target, hospital)
