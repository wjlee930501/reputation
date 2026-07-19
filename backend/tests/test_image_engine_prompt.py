from app.models.content import ContentType
from app.services.image_engine import _build_openai_image_prompt


def test_gpt_image_prompt_uses_editorial_photography_and_hospital_palette():
    prompt = _build_openai_image_prompt(ContentType.HEALTH, "장 건강을 위한 생활 습관")

    assert "editorial photograph" in prompt
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
