from app.models.content import ContentType
from app.services.image_direction import HospitalImageDirection
from app.services.image_engine import (
    IMAGE_POLICY_FALLBACK_PROMPT_VERSION,
    IMAGE_POLICY_REPAIR_PROMPT_VERSION,
    _build_google_image_prompt,
    _build_google_policy_repair_prompt,
    _build_google_safety_fallback_prompt,
    _build_openai_image_prompt,
    _safe_google_visual_scene,
)


def test_gpt_image_prompt_uses_editorial_illustration_and_hospital_palette():
    prompt = _build_openai_image_prompt(ContentType.HEALTH, "장 건강을 위한 생활 습관")

    assert "editorial illustration" in prompt
    assert "subtle navy details" in prompt
    assert "muted-gold accent" in prompt
    assert "NO text" in prompt
    assert "recognizable faces" in prompt
    assert "specific subject" in prompt


def test_sensitive_topic_prompt_forbids_explicit_proctology_imagery():
    prompt = _build_openai_image_prompt(ContentType.DISEASE, "치핵과 항문 통증")

    assert "do NOT depict bare skin, buttocks" in prompt
    assert "fully clothed" in prompt
    assert "not documentary evidence of a real clinic" in prompt


def test_prompt_uses_operator_approved_hospital_art_direction_without_claiming_documentary_truth():
    direction = HospitalImageDirection(
        clinic_name="행복드림의원",
        specialties=("일반의원", "내과 진료"),
        care_philosophy="환자의 말을 먼저 듣고 생활 맥락까지 살핀다",
        visual_direction="지역 가족의 일상을 돌보는 따뜻한 손그림 질감",
        primary_color="#D6A72C",
        accent_color="#6F8A56",
    )

    prompt = _build_openai_image_prompt(ContentType.HEALTH, "예방접종 안내", direction)

    assert "행복드림의원" in prompt
    assert "지역 가족의 일상을 돌보는 따뜻한 손그림 질감" in prompt
    assert "환자의 말을 먼저 듣고 생활 맥락까지 살핀다" in prompt
    assert "#D6A72C" in prompt
    assert "not documentary evidence of a real clinic" in prompt


def test_google_prompt_keeps_operator_direction_inside_non_overridable_editorial_safety():
    direction = HospitalImageDirection(
        clinic_name="노원탑365의원",
        specialties=("내과",),
        care_philosophy="환자가 이해할 때까지 차분히 설명한다",
        visual_direction="따뜻한 종이 질감",
        primary_color="#17365D",
        accent_color="#B79045",
    )

    prompt = _build_google_image_prompt(ContentType.COLUMN, "진료 철학", direction)

    assert prompt.index("따뜻한 종이 질감") < prompt.index("No real clinic documentary claim")
    assert "No recognizable face or named person" in prompt


def test_policy_repair_prompt_keeps_palette_but_drops_clinic_identity_and_freeform_direction():
    direction = HospitalImageDirection(
        clinic_name="노원탑365의원",
        specialties=("척추", "무릎 재활"),
        care_philosophy="환자가 이해할 때까지 차분히 설명한다",
        visual_direction="의료진 얼굴과 병원 간판 중심",
        primary_color="#17365D",
        accent_color="#B79045",
    )

    prompt = _build_google_safety_fallback_prompt(
        ContentType.TREATMENT, "손목 통증 재활", direction
    )

    assert "plain resistance band" in prompt
    assert "#17365D" in prompt
    assert "#B79045" in prompt
    assert "노원탑365의원" not in prompt
    assert "척추" not in prompt
    assert "무릎 재활" not in prompt
    assert "환자가 이해" not in prompt
    assert "의료진 얼굴" not in prompt
    assert "손목 통증 재활" not in prompt
    assert IMAGE_POLICY_FALLBACK_PROMPT_VERSION == "topical-no-text-v1"


def test_second_policy_repair_prompt_is_distinct_and_uses_prior_typed_facts():
    prompt = _build_google_policy_repair_prompt(
        ContentType.TREATMENT,
        "무릎 재활",
        prior_rejection={"topic_relevant": False, "has_text": True},
    )

    assert "single dominant subject" in prompt
    assert "large, clear, and unobscured" in prompt
    assert "remove every mark or glyph-like detail" in prompt
    assert IMAGE_POLICY_REPAIR_PROMPT_VERSION == "topical-no-text-repair-v2"


def test_safe_scene_maps_common_orthopedic_topics_to_no_text_objects():
    for topic in ("손목 터널 증후군", "무릎 관절 통증", "수술 후 재활 운동"):
        scene = _safe_google_visual_scene(topic, ContentType.TREATMENT)
        assert "resistance band" in scene
        assert not any(
            risky in scene for risky in ("text", "thermometer", "monitor", "calendar", "card")
        )
