"""원장 사전 설문 사실 답 → 병원 정보 반영 제안(순수 함수)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.doctor_survey_facts import (
    REASON_FILLED,
    REASON_MEASUREMENT,
    business_hours_from_survey,
    fact_suggestions,
    physicians_from_survey,
)


def _hospital(**fields):
    base = dict(name="A정형외과의원", specialties=[], director_name=None, treatments=[], address=None,
                address_detail=None, phone=None, business_hours={}, website_url=None, blog_url=None,
                kakao_channel_url=None)
    base.update(fields)
    return SimpleNamespace(**base)


def test_hours_become_the_public_page_format():
    hours = business_hours_from_survey(
        {
            "hours": {
                "same": True,
                "weekday": {"open": True, "start": "09:00", "end": "18:00", "lunch": True, "lunch_start": "13:00", "lunch_end": "14:00"},
                "sat": {"open": True, "start": "09:00", "end": "13:00"},
            },
            "holiday": "휴진",
            "biweekly_off": "있음",
            "biweekly_day": "토",
        }
    )
    assert hours == {
        "mon": "09:00-18:00 (점심 13:00-14:00)",
        "tue": "09:00-18:00 (점심 13:00-14:00)",
        "wed": "09:00-18:00 (점심 13:00-14:00)",
        "thu": "09:00-18:00 (점심 13:00-14:00)",
        "fri": "09:00-18:00 (점심 13:00-14:00)",
        "sat": "09:00-13:00 (격주로 쉼)",
        "sun": "휴진",
        "holiday": "휴진",
    }


def test_night_clinic_extends_the_closing_time_and_unfilled_days_are_skipped():
    hours = business_hours_from_survey(
        {"hours": {"same": False, "mon": {"start": "09:00", "end": "18:00", "night": True, "night_end": "21:00"}}}
    )
    assert hours["mon"] == "09:00-21:00"
    assert "tue" not in hours
    assert hours["sun"] == "휴진"


def test_physicians_put_highlighted_career_first():
    doctors = physicians_from_survey(
        {
            "doctors": [
                {
                    "name": "홍길동",
                    "title": "대표원장",
                    "school": "○○대학교 의과대학",
                    "specialty": "정형외과",
                    "societies": ["대한정형외과학회"],
                    "bio": [{"kind": "학력", "text": "○○대 졸업"}, {"kind": "현재 직책", "text": "대표원장", "star": True}],
                },
                {"name": "김의사", "title": "원장", "specialty": "일반의 (전문의 자격 없음)"},
                {"name": "", "title": "원장"},
            ]
        }
    )
    assert [doctor["name"] for doctor in doctors] == ["홍길동", "김의사"]
    assert doctors[0]["career"] == "현재 직책: 대표원장\n학력: ○○대 졸업"
    assert doctors[0]["is_representative"] is True and doctors[1]["is_representative"] is False
    assert doctors[0]["credentials"]["board_certifications"] == ["정형외과"]
    assert doctors[1]["specialties"] == []


def test_only_empty_fields_are_applicable_and_measurement_answers_never_are():
    answers = {
        "departments": ["정형외과"],
        "phone": "02-555-1234",
        "address": "서울 강남구 ○○로 1",
        "search_keywords": ["어깨 통증"],
        "competitors": ["B의원"],
        "channels": ["홈페이지", "인스타그램"],
        "channel_url_homepage": "https://a.example",
        "channel_url_instagram": "https://instagram.com/a",
    }
    items = {item["field"]: item for item in fact_suggestions(answers, _hospital(phone="02-000-0000"), physician_count=0)}
    assert items["specialties"]["applicable"] and items["specialties"]["patch"] == {"specialties": ["정형외과"]}
    assert items["address"]["applicable"]
    assert not items["phone"]["applicable"] and items["phone"]["reason"] == REASON_FILLED
    assert items["phone"]["patch"] is None
    assert items["search_keywords"]["reason"] == REASON_MEASUREMENT and not items["search_keywords"]["applicable"]
    assert items["competitors"]["reason"] == REASON_MEASUREMENT
    assert items["website_url"]["patch"] == {"website_url": "https://a.example"}
    assert not items["channel_instagram"]["applicable"]


def test_physicians_are_offered_only_when_the_hospital_has_none():
    answers = {"doctors": [{"name": "홍길동", "title": "대표원장"}]}
    offered = {i["field"]: i for i in fact_suggestions(answers, _hospital(), physician_count=0)}
    assert offered["physicians"]["applicable"]
    kept = {i["field"]: i for i in fact_suggestions(answers, _hospital(director_name="홍길동"), physician_count=1)}
    assert not kept["physicians"]["applicable"]


def test_biweekly_note_never_uses_a_word_the_public_page_reads_as_closed():
    """`site/lib/business-hours.ts`의 isClosedLabel은 휴진·휴무·closed가 든 요일을 매주 휴진으로 본다."""

    hours = business_hours_from_survey(
        {"hours": {"sat": {"open": True, "start": "09:00", "end": "13:00"}}, "biweekly_off": "있음", "biweekly_day": "토"}
    )
    assert not any(word in hours["sat"].lower() for word in ("휴진", "휴무", "closed"))


def test_a_holiday_answer_alone_is_still_offered():
    assert business_hours_from_survey({"holiday": "휴진"}) == {"holiday": "휴진"}


def test_values_longer_than_the_profile_accepts_are_not_applicable():
    from app.services.doctor_survey_facts import REASON_INVALID

    items = {i["field"]: i for i in fact_suggestions({"address_detail": "가" * 201}, _hospital(), physician_count=0)}
    assert not items["address_detail"]["applicable"]
    assert items["address_detail"]["reason"] == REASON_INVALID


def test_every_saved_sub_value_is_shown_to_the_ae():
    answers = {
        "doctors": [{"name": "홍길동", "title": "대표원장", "school": "○○의대", "specialty": "정형외과", "bio": [{"kind": "학력", "text": "○○대 졸업"}]}],
        "treatments": [{"name": "충격파", "summary": "비수술 치료"}],
    }
    items = {i["field"]: i for i in fact_suggestions(answers, _hospital(), physician_count=0)}
    assert items["physicians"]["details"] == ["홍길동 대표원장 · 졸업: ○○의대 · 전문의: 정형외과 · 약력: 학력: ○○대 졸업"]
    assert items["treatments"]["details"] == ["충격파 — 비수술 치료"]
