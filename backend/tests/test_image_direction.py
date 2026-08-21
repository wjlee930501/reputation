from types import SimpleNamespace

from app.services.image_direction import hospital_image_direction, image_direction_prompt


def test_hospital_image_direction_sanitizes_and_caps_operator_context():
    hospital = SimpleNamespace(
        name="  노원탑365의원\n",
        specialties=["응급의학과", "정형외과"],
        director_philosophy="빠른 판단만큼 환자가 이해하는 설명을 중요하게 생각합니다.",
        image_style_direction=" 밝은 실제 진료 공간의 질감\x00, 과장 없는 생활 장면 ",
        brand_primary_color="#17365D",
        brand_accent_color="#B79045",
    )

    direction = hospital_image_direction(hospital)
    prompt = image_direction_prompt(direction)

    assert direction.clinic_name == "노원탑365의원"
    assert "\x00" not in prompt
    assert "응급의학과, 정형외과" in prompt
    assert "환자가 이해하는 설명" in prompt
    assert "#17365D" in prompt
