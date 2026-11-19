"""원장 사전 설문의 사실 답 → 병원 정보 반영 제안(AE가 보고 반영한다).

병원명·의료진·진료 항목·주소·전화·진료시간·채널은 공개 페이지에 바로 나가는 사실이라 자동으로 쓰지 않는다
(사용자 결정 2026-11-19). AE가 병원 정보 탭 설문 카드에서 제안을 보고 골라 반영하면, 화면이 기존 프로필
저장(`PATCH /admin/hospitals/{id}/profile`)으로 보낸다 — 사람이 확인한 값이므로 출처 원장에는 마케터 입력으로
남는다.

- **빈 칸에만** 반영할 수 있다. 이미 값이 있는 칸은 설문 값과 지금 값을 나란히 보여 주기만 한다(덮어쓰지 않는다,
  자동 입력 D30과 같은 원칙).
- 의료진은 병원에 의료진 행이 하나도 없을 때만 통째로 제안한다(프로필 저장의 `physicians`는 집합 치환이다).
- 측정 기준(검색 키워드·AI 질문·비교 병원)은 반영하지 않는다 — 바꾸면 그 달 측정을 지난달과 비교할 수 없다.
  참고로만 보인다. 병원명은 계약 등록 값이라 참고로만 보인다.
- 제출한 답(`submitted_answers`)만 쓴다. 작성 중인 답은 아직 원장이 확정하지 않은 값이다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.services import doctor_survey_schema as schema

_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
_DAYS = ("mon", "tue", "wed", "thu", "fri")
_CHANNEL_FIELDS = {
    "homepage": "website_url",
    "blog": "blog_url",
    "kakao": "kakao_channel_url",
}
REASON_MEASUREMENT = "측정 기준이라 반영하지 않습니다 — 바꾸면 이번 달 측정을 지난달과 비교할 수 없습니다. 필요하면 대표와 정해 주세요."
REASON_IDENTITY = "병원명은 계약 등록 값입니다. 다르면 담당자가 확인해 주세요."
REASON_REFERENCE = "병원 정보에 칸이 없는 답입니다. 콘텐츠·AE 상담에 참고해 주세요."
REASON_FILLED = "이미 값이 있어 반영하지 않습니다(덮어쓰지 않습니다)."
REASON_INVALID = "병원 정보 형식(글자 수 등)에 맞지 않아 반영할 수 없습니다. 병원 정보에서 직접 고쳐 입력해 주세요."


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strs(value: Any) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not any(not _empty(item) for item in (value.values() if isinstance(value, dict) else value))
    return False


# ── 진료시간 ────────────────────────────────────────────────────────────


def _day_text(day: Mapping[str, Any] | None, *, default_open: bool) -> str | None:
    """설문의 하루 → 병원 정보 진료시간 문자열(공개 페이지 파서가 읽는 '09:00-18:00 (점심 13:00-14:00)')."""

    day = day or {}
    is_open = day.get("open") if isinstance(day.get("open"), bool) else default_open
    if not is_open:
        return "휴진"
    start, end = _str(day.get("start")), _str(day.get("end"))
    if day.get("night") is True and _str(day.get("night_end")):
        end = _str(day.get("night_end"))
    if not start or not end:
        return None
    text = f"{start}-{end}"
    if day.get("lunch") is True and _str(day.get("lunch_start")) and _str(day.get("lunch_end")):
        text += f" (점심 {_str(day.get('lunch_start'))}-{_str(day.get('lunch_end'))})"
    return text


def business_hours_from_survey(answers: Mapping[str, Any]) -> dict[str, str]:
    hours = answers.get("hours") if isinstance(answers.get("hours"), Mapping) else {}
    out: dict[str, str] = {}
    if hours:
        same = hours.get("same") if isinstance(hours.get("same"), bool) else True
        for key in _DAYS:
            text = _day_text(hours.get("weekday" if same else key), default_open=True)
            if text:
                out[key] = text
        for key, default_open in (("sat", True), ("sun", False)):
            text = _day_text(hours.get(key), default_open=default_open)
            if text:
                out[key] = text
    holiday = _str(answers.get("holiday"))
    if holiday == "휴진":
        out["holiday"] = "휴진"
    elif holiday == "진료" and _str(answers.get("holiday_start")) and _str(answers.get("holiday_end")):
        out["holiday"] = f"{_str(answers.get('holiday_start'))}-{_str(answers.get('holiday_end'))}"
    if _str(answers.get("biweekly_off")) == "있음" and _str(answers.get("biweekly_day")):
        key = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat"}.get(
            _str(answers.get("biweekly_day"))
        )
        if key and key in out and out[key] != "휴진":
            # '휴진'이라고 쓰면 공개 페이지가 그 요일을 매주 휴진으로 읽는다(`site/lib/business-hours.ts`).
            out[key] = f"{out[key]} (격주로 쉼)"
    return out


def _hours_display(hours: Mapping[str, str]) -> str:
    labels = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일", "holiday": "공휴일"}
    return " · ".join(f"{labels[key]} {value}" for key, value in hours.items() if key in labels)


# ── 의료진 ──────────────────────────────────────────────────────────────


def physicians_from_survey(answers: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, doctor in enumerate(answers.get("doctors") or []):
        if not isinstance(doctor, Mapping) or not _str(doctor.get("name")):
            continue
        bio = [row for row in doctor.get("bio") or [] if isinstance(row, Mapping) and _str(row.get("text"))]
        # 대표 경력(☆)을 먼저 둔다 — 공개 페이지는 약력 앞줄부터 보인다.
        bio.sort(key=lambda row: 0 if row.get("star") is True else 1)
        career = "\n".join(
            f"{_str(row.get('kind'))}: {_str(row.get('text'))}" if _str(row.get("kind")) else _str(row.get("text"))
            for row in bio
        )
        specialty = _str(doctor.get("specialty"))
        credentials = {
            "medical_school": _str(doctor.get("school")) or None,
            "board_certifications": [specialty] if specialty and "전문의 자격 없음" not in specialty else [],
            "society_memberships": _strs(doctor.get("societies")),
        }
        out.append(
            {
                "name": _str(doctor.get("name"))[:100],
                "title": _str(doctor.get("title"))[:50] or None,
                "specialties": [specialty] if specialty and "전문의 자격 없음" not in specialty else [],
                "career": career[:2000] or None,
                "credentials": credentials,
                "is_representative": index == 0,
            }
        )
    return out


def _physician_detail(doctor: Mapping[str, Any]) -> str:
    credentials = doctor.get("credentials") or {}
    parts = [f"{doctor['name']}{' ' + doctor['title'] if doctor.get('title') else ''}"]
    if credentials.get("medical_school"):
        parts.append(f"졸업: {credentials['medical_school']}")
    if credentials.get("board_certifications"):
        parts.append(f"전문의: {', '.join(credentials['board_certifications'])}")
    if credentials.get("society_memberships"):
        parts.append(f"학회: {', '.join(credentials['society_memberships'])}")
    if doctor.get("career"):
        parts.append("약력: " + " / ".join(str(doctor["career"]).split("\n")))
    return " · ".join(parts)


# ── 제안 목록 ───────────────────────────────────────────────────────────


def _patch_fits_profile(patch: Mapping[str, Any]) -> bool:
    """프로필 저장 스키마가 받는 값인가 — 설문 한도(500자)가 병원 정보 한도(전화 50자 등)보다 넓다."""

    from pydantic import ValidationError

    from app.api.admin.hospitals import HospitalProfileUpdate

    try:
        HospitalProfileUpdate.model_validate(dict(patch))
    except ValidationError:
        return False
    return True


def _item(
    field: str,
    label: str,
    value: str,
    *,
    patch: dict[str, Any] | None,
    current: str,
    current_empty: bool,
    reason: str | None = None,
    details: list[str] | None = None,
) -> dict[str, Any]:
    if patch is not None and reason is None and not _patch_fits_profile(patch):
        reason = REASON_INVALID
    applicable = patch is not None and current_empty and reason is None
    if patch is not None and not current_empty and reason is None:
        reason = REASON_FILLED
    return {
        "field": field,
        "label": label,
        "value": value,
        # 반영하면 저장되는 하위 내용 전부(약력·자격·진료 설명·요일별 시간) — AE가 보고 고른다.
        "details": details or [],
        "current": current,
        "applicable": applicable,
        "patch": patch if applicable else None,
        "reason": reason,
    }


def fact_suggestions(
    answers: Mapping[str, Any] | None,
    hospital: Any,
    *,
    physician_count: int,
) -> list[dict[str, Any]]:
    """제출한 답에서 병원 정보 반영 제안. 답이 없는 항목은 싣지 않는다."""

    answers = answers or {}
    items: list[dict[str, Any]] = []

    name = _str(answers.get("clinic_name"))
    if name and name != _str(getattr(hospital, "name", "")):
        items.append(
            _item("name", "병원명", name, patch=None, current=_str(hospital.name), current_empty=False, reason=REASON_IDENTITY)
        )

    departments = _strs(answers.get("departments"))
    if departments:
        current = _strs(getattr(hospital, "specialties", None))
        items.append(
            _item(
                "specialties", "진료과", ", ".join(departments),
                patch={"specialties": departments}, current=", ".join(current), current_empty=not current,
            )
        )

    physicians = physicians_from_survey(answers)
    if physicians:
        director = _str(getattr(hospital, "director_name", ""))
        current_empty = physician_count == 0 and not director
        items.append(
            _item(
                "physicians", "의료진", " · ".join(
                    f"{doctor['name']}{' ' + doctor['title'] if doctor['title'] else ''}" for doctor in physicians
                ),
                patch={"physicians": physicians}, current=director or (f"{physician_count}명" if physician_count else ""),
                current_empty=current_empty,
                details=[_physician_detail(doctor) for doctor in physicians],
            )
        )

    treatments = [
        {"name": _str(row.get("name"))[:200], "description": _str(row.get("summary"))[:500] or None}
        for row in answers.get("treatments") or []
        if isinstance(row, Mapping) and _str(row.get("name"))
    ]
    if treatments:
        current = getattr(hospital, "treatments", None) or []
        items.append(
            _item(
                "treatments", "진료 항목", ", ".join(t["name"] for t in treatments),
                patch={"treatments": treatments},
                current=", ".join(_str(t.get("name")) for t in current if isinstance(t, Mapping)),
                current_empty=_empty(current),
                details=[f"{t['name']} — {t['description']}" if t["description"] else t["name"] for t in treatments],
            )
        )

    for field, label, key in (("address", "도로명 주소", "address"), ("address_detail", "층·호수", "address_detail"), ("phone", "대표 전화번호", "phone")):
        value = _str(answers.get(key))
        if value:
            current = _str(getattr(hospital, field, ""))
            items.append(_item(field, label, value, patch={field: value}, current=current, current_empty=not current))

    hours = business_hours_from_survey(answers)
    if hours:
        current_hours = getattr(hospital, "business_hours", None) or {}
        items.append(
            _item(
                "business_hours", "진료시간", _hours_display(hours),
                patch={"business_hours": hours},
                details=_hours_display(hours).split(" · "),
                current=_hours_display({k: v for k, v in current_hours.items() if isinstance(v, str)}) if isinstance(current_hours, Mapping) else "",
                current_empty=_empty(current_hours),
            )
        )

    channels = set(_strs(answers.get("channels")))
    for key, label in schema.CHANNELS:
        url = _str(answers.get(f"channel_url_{key}"))
        if label not in channels or not url:
            continue
        field = _CHANNEL_FIELDS.get(key)
        if field and _URL.match(url):
            current = _str(getattr(hospital, field, ""))
            items.append(_item(field, f"{label} 주소", url, patch={field: url}, current=current, current_empty=not current))
        else:
            items.append(_item(f"channel_{key}", f"{label} 주소", url, patch=None, current="", current_empty=True, reason=REASON_REFERENCE))

    for key, label in (("search_keywords", "검색 키워드"), ("ai_questions", "AI 질문"), ("competitors", "비교 병원")):
        values = _strs(answers.get(key))
        if values:
            items.append(_item(key, label, ", ".join(values), patch=None, current="", current_empty=True, reason=REASON_MEASUREMENT))

    reference: list[tuple[str, str, str]] = []
    if _str(answers.get("google_status")):
        reference.append(("google_status", "구글 지도 등록", _str(answers.get("google_status"))))
    prices = [
        f"{_str(row.get('treatment'))} "
        + (
            f"{_str(row.get('price_min'))}~{_str(row.get('price_max'))}원"
            if row.get("price_range") is True
            else f"{_str(row.get('price'))}원"
        )
        for row in answers.get("prices") or []
        if isinstance(row, Mapping) and _str(row.get("treatment"))
    ]
    if _str(answers.get("prices_public")) == "있음" and prices:
        reference.append(("prices", "비급여 진료비(제출일 기준)", ", ".join(prices)))
    certifications = [
        f"{_str(row.get('name'))}{' (' + _str(row.get('year')) + ')' if _str(row.get('year')) else ''}"
        for row in answers.get("certifications") or []
        if isinstance(row, Mapping) and _str(row.get("name"))
    ]
    if certifications:
        reference.append(("certifications", "병원 인증·지정", ", ".join(certifications)))
    if _strs(answers.get("stopped_channels")):
        reference.append(("stopped_channels", "운영을 멈춘 채널", ", ".join(_strs(answers.get("stopped_channels")))))
    if _str(answers.get("wait_time")):
        reference.append(("wait_time", "평균 대기 시간", _str(answers.get("wait_time"))))
    if _str(answers.get("logo_color")):
        reference.append(("logo_color", "선호하는 색", _str(answers.get("logo_color"))))
    for field, label, value in reference:
        items.append(_item(field, label, value, patch=None, current="", current_empty=True, reason=REASON_REFERENCE))
    return items


__all__ = (
    "business_hours_from_survey",
    "fact_suggestions",
    "physicians_from_survey",
)
