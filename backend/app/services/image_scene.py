import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

from app.models.content import ContentType
from app.services.image_direction import (
    CareMode,
    ClinicalFocus,
    HospitalImageDirection,
    RegionalSetting,
    VisualStyle,
)


class SceneSubject(StrEnum):
    HYDRATION = "hydration"
    JOINT_MOBILITY = "joint_mobility"
    ULTRASOUND_PREPARATION = "ultrasound_preparation"
    PREVENTIVE_CHECKUP = "preventive_checkup"
    DIGESTIVE_WELLNESS = "digestive_wellness"
    VACCINATION_PREPARATION = "vaccination_preparation"
    URGENT_CARE_PREPARATION = "urgent_care_preparation"
    HEALTHY_ROUTINE = "healthy_routine"
    CAREFUL_EXAMINATION = "careful_examination"
    CLINICAL_EXPLANATION = "clinical_explanation"
    NEIGHBORHOOD_WELLNESS = "neighborhood_wellness"
    INFORMATION_UPDATE = "information_update"


@dataclass(frozen=True, slots=True)
class ImageScenePlan:
    content_type: ContentType
    subject: SceneSubject
    props: tuple[str, ...]
    clinical_focus: tuple[ClinicalFocus, ...]
    care_mode: CareMode
    visual_style: VisualStyle
    regional_setting: RegionalSetting
    palette: tuple[str, ...]


_TOPIC_SCENES: Final = (
    (
        ("응급", "외상", "야간진료", "주말진료", "공휴일진료"),
        SceneSubject.URGENT_CARE_PREPARATION,
        ("first_aid_pouch", "sealed_cold_pack", "elastic_bandage"),
    ),
    (("발열", "탈수", "수분"), SceneSubject.HYDRATION, ("water_glass", "thermometer", "linen")),
    (
        ("무릎", "관절", "걷기", "척추"),
        SceneSubject.JOINT_MOBILITY,
        ("walking_shoes", "wood_step", "plant"),
    ),
    (
        ("초음파",),
        SceneSubject.ULTRASOUND_PREPARATION,
        ("ultrasound_console", "folded_towel", "soft_lamp"),
    ),
    (
        ("검진", "건강검진"),
        SceneSubject.PREVENTIVE_CHECKUP,
        ("stethoscope", "calendar_blocks", "water_glass"),
    ),
    (
        ("항문", "치루", "치질", "치핵", "치열", "변비", "대장", "내시경"),
        SceneSubject.DIGESTIVE_WELLNESS,
        ("whole_grains", "water_glass", "walking_shoes"),
    ),
    (
        ("예방접종", "백신"),
        SceneSubject.VACCINATION_PREPARATION,
        ("calendar_blocks", "bandage_box", "warm_scarf"),
    ),
)


def _default_scene(content_type: ContentType) -> tuple[SceneSubject, tuple[str, ...]]:
    match content_type:
        case ContentType.FAQ | ContentType.COLUMN:
            return SceneSubject.CLINICAL_EXPLANATION, ("two_chairs", "paper_shapes", "soft_lamp")
        case ContentType.DISEASE | ContentType.TREATMENT:
            return SceneSubject.CAREFUL_EXAMINATION, ("stethoscope", "folded_towel", "wood_tray")
        case ContentType.HEALTH:
            return SceneSubject.HEALTHY_ROUTINE, ("walking_shoes", "fruit_bowl", "water_glass")
        case ContentType.LOCAL:
            return SceneSubject.NEIGHBORHOOD_WELLNESS, ("tree_lined_street", "bench", "bicycle")
        case ContentType.NOTICE:
            return SceneSubject.INFORMATION_UPDATE, ("calendar_blocks", "paper_shapes", "soft_lamp")
        case unreachable:
            assert_never(unreachable)


def _topic_scene(
    topic: str | None, content_type: ContentType
) -> tuple[SceneSubject, tuple[str, ...]]:
    normalized = re.sub(r"\s+", "", (topic or "")[:240].lower())
    for terms, subject, props in _TOPIC_SCENES:
        if any(term in normalized for term in terms):
            return subject, props
    return _default_scene(content_type)


def build_scene_plan(
    content_type: ContentType,
    topic: str | None,
    direction: HospitalImageDirection,
) -> ImageScenePlan:
    """Select a topic-specific scene without carrying raw text into the prompt."""
    subject, props = _topic_scene(topic, content_type)
    palette = tuple(
        color for color in (direction.primary_color, direction.accent_color) if color is not None
    )
    return ImageScenePlan(
        content_type=content_type,
        subject=subject,
        props=props,
        clinical_focus=direction.clinical_focus,
        care_mode=direction.care_mode,
        visual_style=direction.visual_style,
        regional_setting=direction.regional_setting,
        palette=palette,
    )


def _render_prompt(plan: ImageScenePlan, provider: str) -> str:
    return (
        f"Create an original 16:9 Korean health editorial image. Provider rendering: {provider}. "
        f"Structured scene subject: {plan.subject.value}; props: {', '.join(plan.props)}. "
        f"Hospital grounding: clinical focus {', '.join(x.value for x in plan.clinical_focus)}; "
        f"care mode {plan.care_mode.value}; visual style {plan.visual_style.value}; "
        f"regional setting {plan.regional_setting.value}; "
        f"approved accent colors {', '.join(plan.palette) or 'warm ivory and restrained navy'}. "
        "Render a coherent physical scene, not a card, collage, diagram, or floating icon layout. "
        "No text, letters, numbers, signage, captions, logos, or watermarks. No recognizable people, "
        "named doctors, faces, explicit anatomy, blood, invasive procedures, or outcome claims. "
        "Do not imitate documentary evidence, a real clinic interior, or a real medical professional."
    )


def render_google_prompt(plan: ImageScenePlan) -> str:
    return _render_prompt(plan, "softly modeled tactile illustration")


def render_openai_prompt(plan: ImageScenePlan) -> str:
    return _render_prompt(plan, "contemporary health-magazine illustration")
