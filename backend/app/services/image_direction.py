"""Parse approved hospital identity into bounded image-direction tokens."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence


class HospitalVisualProfile(Protocol):
    region: Sequence[str]
    specialties: Sequence[str]
    image_style_direction: str | None
    brand_primary_color: str | None
    brand_accent_color: str | None


class ApprovedPhilosophyProfile(Protocol):
    positioning_statement: str | None
    doctor_voice: str | None
    patient_promise: str | None
    content_principles: Sequence[str]
    tone_guidelines: Sequence[str]


class ClinicalFocus(StrEnum):
    GENERAL = "general"
    INTERNAL_MEDICINE = "internal_medicine"
    ORTHOPEDICS = "orthopedics"
    EMERGENCY = "emergency"
    PEDIATRICS = "pediatrics"
    SURGERY = "surgery"
    WOMENS_HEALTH = "womens_health"
    SCREENING = "screening"


class CareMode(StrEnum):
    CALM_EXPLANATION = "calm_explanation"
    EVIDENCE_EXPLANATION = "evidence_explanation"
    LISTENING = "listening"
    DAILY_LIFE = "daily_life"


class VisualStyle(StrEnum):
    EDITORIAL = "editorial"
    WATERCOLOR = "watercolor"
    PAPER_CUT = "paper_cut"
    CLAY = "clay"
    MINIMAL_PHOTO = "minimal_photo"


class RegionalSetting(StrEnum):
    KOREAN_NEIGHBORHOOD = "korean_neighborhood"
    SEOUL_METROPOLITAN = "seoul_metropolitan"
    GYEONGGI = "gyeonggi"
    CHUNGCHEONG = "chungcheong"
    GYEONGSANG = "gyeongsang"
    JEOLLA = "jeolla"
    GANGWON = "gangwon"
    JEJU = "jeju"


@dataclass(frozen=True, slots=True)
class HospitalImageDirection:
    clinical_focus: tuple[ClinicalFocus, ...]
    care_mode: CareMode
    visual_style: VisualStyle
    regional_setting: RegionalSetting
    primary_color: str | None
    accent_color: str | None

    @classmethod
    def default(cls) -> "HospitalImageDirection":
        return cls(
            clinical_focus=(ClinicalFocus.GENERAL,),
            care_mode=CareMode.CALM_EXPLANATION,
            visual_style=VisualStyle.EDITORIAL,
            regional_setting=RegionalSetting.KOREAN_NEIGHBORHOOD,
            primary_color=None,
            accent_color=None,
        )


_FOCUS_TERMS: tuple[tuple[ClinicalFocus, tuple[str, ...]], ...] = (
    (ClinicalFocus.EMERGENCY, ("응급",)),
    (ClinicalFocus.ORTHOPEDICS, ("정형", "관절", "척추")),
    (ClinicalFocus.INTERNAL_MEDICINE, ("내과", "소화기")),
    (ClinicalFocus.PEDIATRICS, ("소아", "어린이")),
    (ClinicalFocus.SURGERY, ("일반외과", "대장", "항문")),
    (ClinicalFocus.WOMENS_HEALTH, ("여성", "산부인", "유방")),
    (ClinicalFocus.SCREENING, ("검진", "영상", "초음파")),
)

_REGION_TERMS: tuple[tuple[RegionalSetting, tuple[str, ...]], ...] = (
    (RegionalSetting.SEOUL_METROPOLITAN, ("서울", "인천")),
    (RegionalSetting.GYEONGGI, ("경기", "수원", "고양", "성남", "용인")),
    (RegionalSetting.CHUNGCHEONG, ("충청", "대전", "세종")),
    (RegionalSetting.GYEONGSANG, ("경상", "부산", "대구", "울산", "창원")),
    (RegionalSetting.JEOLLA, ("전라", "광주")),
    (RegionalSetting.GANGWON, ("강원",)),
    (RegionalSetting.JEJU, ("제주",)),
)


def _parse_focus(values: Sequence[str]) -> tuple[ClinicalFocus, ...]:
    joined = " ".join(value[:80].lower() for value in values[:6] if isinstance(value, str))
    parsed = tuple(focus for focus, terms in _FOCUS_TERMS if any(term in joined for term in terms))
    return parsed or (ClinicalFocus.GENERAL,)


def _parse_regional_setting(values: Sequence[str]) -> RegionalSetting:
    joined = " ".join(value[:80].lower() for value in values[:6] if isinstance(value, str))
    return next(
        (
            setting
            for setting, terms in _REGION_TERMS
            if any(term in joined for term in terms)
        ),
        RegionalSetting.KOREAN_NEIGHBORHOOD,
    )


def _parse_care_mode(philosophy: ApprovedPhilosophyProfile) -> CareMode:
    text = " ".join(
        value
        for value in (
            philosophy.positioning_statement,
            philosophy.doctor_voice,
            philosophy.patient_promise,
            *philosophy.content_principles[:6],
            *philosophy.tone_guidelines[:6],
        )
        if isinstance(value, str)
    ).lower()
    if any(term in text for term in ("근거", "검증")) and any(
        term in text for term in ("설명", "이해")
    ):
        return CareMode.EVIDENCE_EXPLANATION
    if any(term in text for term in ("듣", "경청")):
        return CareMode.LISTENING
    if any(term in text for term in ("생활", "일상")):
        return CareMode.DAILY_LIFE
    return CareMode.CALM_EXPLANATION


def _parse_style(raw: str | None) -> VisualStyle:
    text = (raw or "")[:500].lower()
    if any(term in text for term in ("수채", "watercolor")):
        return VisualStyle.WATERCOLOR
    if any(term in text for term in ("종이", "paper cut", "페이퍼")):
        return VisualStyle.PAPER_CUT
    if any(term in text for term in ("클레이", "clay")):
        return VisualStyle.CLAY
    if any(term in text for term in ("사진", "photograph")):
        return VisualStyle.MINIMAL_PHOTO
    return VisualStyle.EDITORIAL


def _parse_color(raw: str | None) -> str | None:
    value = (raw or "").strip().upper()
    return value if re.fullmatch(r"#[0-9A-F]{6}", value) else None


def hospital_image_direction(
    hospital: HospitalVisualProfile,
    philosophy: ApprovedPhilosophyProfile | None = None,
) -> HospitalImageDirection:
    """Convert raw profile fields and approved STEP5 philosophy to safe tokens."""
    return HospitalImageDirection(
        clinical_focus=_parse_focus(hospital.specialties),
        care_mode=(
            _parse_care_mode(philosophy) if philosophy is not None else CareMode.CALM_EXPLANATION
        ),
        visual_style=_parse_style(hospital.image_style_direction),
        regional_setting=_parse_regional_setting(hospital.region),
        primary_color=_parse_color(hospital.brand_primary_color),
        accent_color=_parse_color(hospital.brand_accent_color),
    )
