"""Typed, operator-approved hospital identity context for editorial image generation."""

import re
from dataclasses import dataclass
from typing import Protocol


class HospitalVisualProfile(Protocol):
    name: str
    specialties: list
    director_philosophy: str | None
    image_style_direction: str | None
    brand_primary_color: str | None
    brand_accent_color: str | None


@dataclass(frozen=True, slots=True)
class HospitalImageDirection:
    clinic_name: str
    specialties: tuple[str, ...]
    care_philosophy: str | None
    visual_direction: str | None
    primary_color: str | None
    accent_color: str | None


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit] or None


def hospital_image_direction(hospital: HospitalVisualProfile) -> HospitalImageDirection:
    specialties = tuple(
        cleaned
        for value in hospital.specialties[:4]
        if (cleaned := _clean(value, 60)) is not None
    )
    return HospitalImageDirection(
        clinic_name=_clean(hospital.name, 120) or "Korean clinic",
        specialties=specialties,
        care_philosophy=_clean(getattr(hospital, "director_philosophy", None), 320),
        visual_direction=_clean(hospital.image_style_direction, 500),
        primary_color=_clean(hospital.brand_primary_color, 7),
        accent_color=_clean(hospital.brand_accent_color, 7),
    )


def image_direction_prompt(direction: HospitalImageDirection | None) -> str:
    if direction is None:
        return ""
    parts = [f"Clinic identity context: {direction.clinic_name}."]
    if direction.specialties:
        parts.append(f"Clinical focus: {', '.join(direction.specialties)}.")
    if direction.care_philosophy:
        parts.append(f"Care philosophy to express as mood, never as text: {direction.care_philosophy}.")
    if direction.visual_direction:
        parts.append(f"Operator-approved art direction: {direction.visual_direction}.")
    palette = [color for color in (direction.primary_color, direction.accent_color) if color]
    if palette:
        parts.append(f"Use these approved colors as restrained accents only: {', '.join(palette)}.")
    return " ".join(parts)
