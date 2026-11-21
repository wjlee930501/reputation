"""병원명 질문(그룹 A) 답변의 병원 정보 정확도 — 결정적 규칙(설계 §5)."""

from __future__ import annotations

from app.services.brand_answer_accuracy import BrandFacts, _times, check_answer, summarize


def _facts(**overrides):
    values = {
        "name": "행복드림의원",
        "address": "서울특별시 마포구 월드컵북로 123 2층",
        "business_hours": {"mon": "09:00-18:00", "sat": "09:00-13:00", "holiday": "휴진"},
        "treatments": [{"name": "위내시경"}, {"name": "대장내시경", "description": "…"}],
        "keywords": ["갑상선초음파"],
    }
    values.update(overrides)
    return BrandFacts.from_hospital(**values)


def test_facts_extract_road_hours_and_treatment_names():
    facts = _facts()

    assert facts.road == ("월드컵북로", "123")
    assert facts.hours_minutes == frozenset({9 * 60, 18 * 60, 13 * 60})
    assert facts.treatment_names == ("위내시경", "대장내시경", "갑상선초음파")


def test_korean_times_and_durations():
    assert _times("오전 9시부터 오후 6시 반까지") == {9 * 60, 18 * 60 + 30}
    assert _times("13:00~14:00 점심시간") == {13 * 60, 14 * 60}
    # '2시간'은 시각이 아니다.
    assert _times("대기는 2시간 정도") == set()
    assert _times("저녁 7시 20분") == {19 * 60 + 20}


def test_matching_answer():
    verdicts = check_answer(
        _facts(),
        "행복드림의원은 서울 마포구 월드컵북로 123에 있어요. 평일 오전 9시~오후 6시, "
        "토요일 9:00~13:00 진료하고 위내시경도 합니다.",
    )

    assert verdicts == {"address": "MATCH", "hours": "MATCH", "treatments": "MATCH"}


def test_wrong_answer_and_silent_answer():
    assert check_answer(_facts(), "행복드림의원 주소는 성미산로 10, 평일 08:30~19:00입니다.") == {
        "address": "MISMATCH",
        "hours": "MISMATCH",
        "treatments": "ABSENT",
    }
    assert check_answer(_facts(), "그 병원 정보는 찾지 못했습니다.") == {
        "address": "ABSENT",
        "hours": "ABSENT",
        "treatments": "ABSENT",
    }


def test_other_clinic_before_our_name_is_not_read_as_ours():
    text = (
        "근처 다른 내과는 성미산로 10에 있고 08:00에 엽니다.\n\n"
        "행복드림의원은 월드컵북로 123에 있습니다."
    )

    assert check_answer(_facts(), text)["address"] == "MATCH"
    assert check_answer(_facts(), text)["hours"] == "ABSENT"


def test_fields_without_hospital_basis_are_not_judged():
    facts = _facts(address="마포구 어딘가", business_hours=None, treatments=[], keywords=[])

    assert check_answer(facts, "행복드림의원 월드컵북로 123, 9시 오픈") == {}


def test_summary_counts_and_keeps_mismatch_excerpts():
    summary = summarize(
        _facts(),
        [
            ("행복드림의원 어때?", "행복드림의원은 월드컵북로 123에 있어요."),
            ("행복드림의원 진료 시간 알려줘", "행복드림의원은 평일 10:00~20:00 진료합니다."),
            ("행복드림의원에서 위내시경 해?", "행복드림의원에서 위내시경 검사를 받을 수 있어요."),
        ],
    )

    assert summary["answers"] == 3
    assert summary["fields"]["address"] == {
        "label": "주소",
        "match": 1,
        "mismatch": 0,
        "absent": 2,
        "checked": 1,
        "mismatch_excerpts": [],
    }
    assert summary["fields"]["hours"]["mismatch"] == 1
    assert summary["fields"]["hours"]["mismatch_excerpts"][0].startswith(
        "행복드림의원 진료 시간 알려줘 → 행복드림의원은 평일 10:00~20:00"
    )
    assert summary["fields"]["treatments"]["match"] == 1


def test_monthly_report_summarizes_only_brand_answers():
    from types import SimpleNamespace

    from app.workers.tasks import _brand_answer_accuracy

    hospital = SimpleNamespace(
        name="행복드림의원",
        address="서울특별시 마포구 월드컵북로 123",
        business_hours={"mon": "09:00-18:00"},
        treatments=[{"name": "위내시경"}],
        keywords=[],
    )
    loaded = SimpleNamespace(
        scored_records=(
            SimpleNamespace(
                report_query_text="행복드림의원 어때?",
                report_query_intent="BRAND",
                raw_response="행복드림의원은 월드컵북로 123에 있습니다.",
            ),
            SimpleNamespace(
                report_query_text="마포 내과 추천해줘",
                report_query_intent="LOCAL",
                raw_response="행복드림의원은 성미산로 1에 있습니다.",
            ),
        )
    )

    summary = _brand_answer_accuracy(hospital, loaded)

    assert summary["answers"] == 1
    assert summary["fields"]["address"]["match"] == 1
    assert _brand_answer_accuracy(hospital, None) is None
    assert _brand_answer_accuracy(hospital, SimpleNamespace(scored_records=())) is None


def test_codex_review_edge_cases():
    facts = _facts()

    # 전화번호는 주소가 아니다.
    assert check_answer(facts, "행복드림의원 예약은 전화로 02-1234-5678")["address"] == "ABSENT"
    # '…로 123번길 4'는 다른 도로다.
    assert check_answer(facts, "행복드림의원은 월드컵북로 123번길 4에 있어요")["address"] == "MISMATCH"
    # 주소 뒤 마침표.
    assert check_answer(facts, "행복드림의원은 월드컵북로 123. 2층입니다.")["address"] == "MATCH"
    # 콜론 시각의 오전·오후.
    assert check_answer(facts, "행복드림의원은 오전 9:00~오후 6:00 진료합니다.")["hours"] == "MATCH"
    # '시'를 같이 쓰는 범위의 앞쪽 시각.
    assert check_answer(facts, "행복드림의원은 평일 8~18시 진료합니다.")["hours"] == "MISMATCH"
    assert check_answer(facts, "행복드림의원은 평일 9~18시 진료합니다.")["hours"] == "MATCH"
    # 1:1 시술·날짜 12.1 시행은 시각이 아니다.
    assert check_answer(facts, "행복드림의원은 1:1 시술 상담을 제공합니다.")["hours"] == "ABSENT"
    assert check_answer(facts, "행복드림의원은 12.1 시행되는 검진을 안내합니다.")["hours"] == "ABSENT"
    # '24시간 진료' 주장은 진료 시간표와 다르다.
    assert check_answer(facts, "행복드림의원은 24시간 진료합니다.")["hours"] == "MISMATCH"
