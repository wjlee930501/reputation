"""측정 키워드 30개 초안 생성·문형 검사(지침 3-1·3-3, 설계 §6)."""

from __future__ import annotations

import pytest

from app.services.measurement_question_set import (
    GROUP_PLAN,
    PROBLEM_EXAM_AS_CARE,
    PROBLEM_INSTITUTION_REDUNDANT,
    PROBLEM_POSSIBLE_HOSPITAL,
    PROBLEM_WRONG_INSTITUTION,
    common_clinic_name,
    draft_measurement_set,
    measurement_question_problems,
)
from app.services.question_wellformed import question_problems


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("강남 산부인과 병원 추천해줘", PROBLEM_INSTITUTION_REDUNDANT),
        ("세종 한의과 병원 추천해줘", PROBLEM_INSTITUTION_REDUNDANT),
        ("마포 갑상선초음파 진료 가능한 곳", PROBLEM_EXAM_AS_CARE),
        ("강남 임플란트 진료 가능한 병원", PROBLEM_POSSIBLE_HOSPITAL),
        ("마산 마산 심장초음파 되는 곳", "repeated_token"),
    ],
)
def test_guideline_3_1_phrasings_are_flagged(text, code):
    assert code in measurement_question_problems(text)


def test_dental_question_must_not_ask_for_a_hospital():
    assert PROBLEM_WRONG_INSTITUTION in measurement_question_problems(
        "어금니가 시린데 진료를 받으려는데 마산 어느 병원 가야 해?", clinic="치과"
    )
    assert measurement_question_problems("어금니가 시린데 마산 치과 어디가 좋아?", clinic="치과") == []


def test_new_rules_do_not_change_the_legacy_question_check():
    # 유료 매트릭스·시드·브리프가 쓰는 검사는 그대로다(지문·생성 결과가 바뀌지 않게).
    assert question_problems("강남 산부인과 병원 추천해줘") == []


@pytest.mark.parametrize(
    ("specialty", "expected"),
    [("한의과", "한의원"), ("내과 진료", "내과"), ("치과", "치과"), ("산부인과", "산부인과"), ("", "병원")],
)
def test_common_clinic_names(specialty, expected):
    assert common_clinic_name(specialty) == expected


def _draft(**overrides):
    values = {
        "hospital_name": "행복드림의원",
        "region": ["서울 마포구", "합정동"],
        "specialties": ["내과"],
        "keywords": ["위내시경", "갑상선초음파", "대장내시경", "당뇨"],
        "treatments": [{"name": "건강검진"}],
    }
    values.update(overrides)
    return draft_measurement_set(**values)


def test_draft_fills_groups_and_roles_by_the_guideline():
    rows = _draft()

    for group, (fixed, rotating) in GROUP_PLAN.items():
        assert sum(r.group == group and r.role == "FIXED" for r in rows) == fixed
        assert sum(r.group == group and r.role == "ROTATING" for r in rows) == rotating
    assert sum(r.role == "FIXED" for r in rows) == 20
    assert sum(r.role == "ROTATING" for r in rows) == 10
    assert len({r.text for r in rows}) == len(rows)
    assert all(measurement_question_problems(r.text, clinic="내과") == [] for r in rows)
    # A는 병원명 질문, 나머지에는 병원명이 없다.
    assert all("행복드림의원" in r.text for r in rows if r.group == "A")
    assert not any("행복드림의원" in r.text for r in rows if r.group != "A")


def test_draft_is_deterministic_and_mixes_regions_and_items():
    rows = _draft()

    assert rows == _draft()
    texts = " ".join(r.text for r in rows)
    for region in ("서울 마포구", "마포구", "합정동"):
        assert region in texts
    for item in ("위내시경", "갑상선초음파", "대장내시경", "건강검진"):
        assert item in texts
    assert "건강검진 검사" not in texts


def test_dental_and_korean_medicine_use_lay_names_and_no_awkward_procedure_forms():
    dental = draft_measurement_set(
        hospital_name="미소가득치과",
        region=["창원 마산", "합성동"],
        specialties=["치과"],
        keywords=["임플란트", "충치치료", "사랑니"],
    )
    korean = draft_measurement_set(
        hospital_name="편한한의원",
        region=["세종"],
        specialties=["한의과"],
        keywords=["교통사고", "추나요법"],
    )

    assert sum(r.role == "FIXED" for r in dental) == 20
    assert not any("병원 추천" in r.text or "어느 병원" in r.text for r in dental)
    assert any("어금니가 시린데" in r.text for r in dental if r.group == "C")
    assert not any("한의과" in r.text for r in korean)
    # 상황어(교통사고)에 '해?'·'잘하는'을 붙이지 않는다.
    assert not any("교통사고 해?" in r.text or "교통사고 잘하는" in r.text for r in korean)


def test_specialty_without_symptom_templates_falls_back_to_keywords():
    rows = draft_measurement_set(
        hospital_name="바른신경과의원",
        region=["대전 서구", "둔산동"],
        specialties=["신경과"],
        keywords=["두통", "어지럼증", "뇌MRI"],
    )

    c_rows = [r for r in rows if r.group == "C"]
    assert sum(r.role is not None for r in c_rows) == 8
    assert len({r.text for r in c_rows}) == len(c_rows)
