"""무료 진단 질의 생성 (설계 T-3 · T-4 · T-16).

고정하는 것은 문자열이 아니라 **불변 규칙**이다:
지역이 항상 들어간다 / 병원명이 절대 안 들어간다 / 항상 3개다 / 과장 표현이 없다.
"""
import pytest

from app.services.query_mapper import (
    QUERY_SLOT_COUNT,
    QueryMappingError,
    build_lead_diagnosis_queries,
    classify_keyword,
    subject_particle,
)

# 실제 신청에서 나올 법한 조합 — 키워드 1개부터 4개까지, 분류되는/안 되는 것 섞음.
CASES = [
    ("수서역", "내과", ["대장내시경"]),
    ("성수동", "외과", ["치질 수술"]),
    ("정자동", "정형외과", ["허리디스크"]),
    ("수서역", "내과", ["대장내시경", "위내시경", "역류성식도염", "복통"]),
    ("강남구", "치과", ["임플란트", "치아교정"]),
    ("일산동구", "피부과", ["여드름"]),
    # 사전에 없는 키워드 — 분류 실패 → 탐색형 폴백 (PRD F2-4)
    ("망원동", "가정의학과", ["영양수액"]),
    ("부산 해운대", "안과", ["눈"]),
]


@pytest.mark.parametrize("region,specialty,keywords", CASES)
class TestInvariants:
    def test_every_query_contains_the_region(self, region, specialty, keywords):
        """지역 없는 질문은 AI가 특정 의원을 댈 이유가 없어 병원이 무엇을 하든 0이다 (PRD F2-2)."""
        queries = build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=keywords
        )
        for item in queries:
            assert region in item["text"], item

    def test_always_exactly_three_slots_numbered_from_one(self, region, specialty, keywords):
        """계획 측정 수가 3 × 2 × 3 = 18로 고정되어야 원가와 SLA가 계산 가능하다."""
        queries = build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=keywords
        )
        assert len(queries) == QUERY_SLOT_COUNT
        assert [q["slot"] for q in queries] == list(range(1, QUERY_SLOT_COUNT + 1))

    def test_queries_are_distinct(self, region, specialty, keywords):
        """같은 문장을 두 번 던지면 표본이 늘어난 것처럼 보이지만 실제로는 반복일 뿐이다."""
        queries = build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=keywords
        )
        assert len({q["text"] for q in queries}) == QUERY_SLOT_COUNT

    def test_no_superlatives(self, region, specialty, keywords):
        """의료광고법 위험이자, 실측에서 과장 변형은 커버리지가 아니라 분산만 키웠다 (PRD F2-3)."""
        forbidden = ["잘하는", "제일", "1등", "최고", "유명한", "소문난", "명의"]
        queries = build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=keywords
        )
        for item in queries:
            for word in forbidden:
                assert word not in item["text"], (word, item)


class TestHospitalNameNeverLeaksIntoQueries:
    """PRD F1-1 — 자기 이름을 질의에 넣으면 언급은 보장되고 측정은 무의미해진다.

    시그니처에 병원명 인자가 아예 없다는 것으로 구조적으로 막지만, 호출부가 병원명을
    키워드에 실어 보내는 실수는 남는다. 그 경우도 탐지되어야 한다.
    """

    def test_builder_accepts_no_hospital_name_argument(self):
        import inspect

        params = set(inspect.signature(build_lead_diagnosis_queries).parameters)
        assert params == {"region", "specialty", "keywords"}

    def test_hospital_name_in_keywords_would_be_visible_to_the_caller(self):
        """키워드에 병원명을 넣으면 질의에 그대로 나타난다 — 접수 API가 이것을 거부해야 한다.

        여기서는 '조용히 섞여 들어가지 않는다'는 사실만 고정한다. 실제 차단은
        접수 검증(PRD F1-4)의 책임이다.
        """
        queries = build_lead_diagnosis_queries(
            region="수서역", specialty="외과", keywords=["장편한외과의원"]
        )
        assert any("장편한외과의원" in q["text"] for q in queries)


class TestClassification:
    def test_unknown_keywords_fall_back_to_exploratory_not_procedure(self):
        """프로토타입은 판단이 안 서면 무조건 시술형으로 접어 어색한 문장을 만들었다 (PRD F2-4)."""
        assert classify_keyword("영양수액") == "unknown"
        queries = build_lead_diagnosis_queries(
            region="망원동", specialty="가정의학과", keywords=["영양수액"]
        )
        keyword_queries = [q for q in queries if "영양수액" in q["text"]]
        assert keyword_queries
        assert keyword_queries[0]["kind"] == "탐색형"

    def test_condition_keywords_use_patient_phrasing(self):
        queries = build_lead_diagnosis_queries(
            region="정자동", specialty="정형외과", keywords=["허리디스크"]
        )
        first_keyword_query = next(q for q in queries if "허리디스크" in q["text"])
        assert first_keyword_query["kind"] == "증상형"
        assert "있는데" in first_keyword_query["text"]

    @pytest.mark.parametrize(
        "word,expected",
        [("허리디스크", "가"), ("복통", "이"), ("여드름", "이"), ("눈", "이"), ("코", "가")],
    )
    def test_subject_particle_follows_the_final_consonant(self, word, expected):
        """받침 계산이 틀리면 '허리디스크이 있는데'처럼 환자가 쓰지 않는 문장이 된다 (PRD F2-1)."""
        assert subject_particle(word) == expected


class TestSpecialtyComesFromTheForm:
    def test_specialty_query_is_always_slot_one(self):
        """진료과형이 1순위인 이유: 키워드가 빗나가도 지역 신호를 잡고, 조합이 제한적이라
        질의 공유 캐시의 적중률이 가장 높다."""
        queries = build_lead_diagnosis_queries(
            region="수서역", specialty="내과", keywords=["대장내시경"]
        )
        assert queries[0]["kind"] == "진료과형"
        assert "내과" in queries[0]["text"]

    def test_specialty_is_used_even_when_no_keyword_maps_to_it(self):
        """프로토타입은 사전으로 진료과를 유추해, 사전에 없으면 진료과형이 사라졌다."""
        queries = build_lead_diagnosis_queries(
            region="망원동", specialty="가정의학과", keywords=["영양수액"]
        )
        assert "가정의학과" in queries[0]["text"]

    def test_keyword_equal_to_specialty_still_produces_three_queries(self):
        """후보가 겹쳐도 슬롯을 채운다 — 진단마다 측정 수가 달라지면 원가가 흔들린다."""
        queries = build_lead_diagnosis_queries(region="수서역", specialty="내과", keywords=["내과"])
        assert len(queries) == QUERY_SLOT_COUNT
        assert len({q["text"] for q in queries}) == QUERY_SLOT_COUNT


class TestRejectedInput:
    @pytest.mark.parametrize(
        "region,specialty,keywords",
        [("", "내과", ["대장내시경"]), ("수서역", "", ["대장내시경"]), ("수서역", "내과", []),
         ("수서역", "내과", ["  "])],
    )
    def test_missing_required_input_raises(self, region, specialty, keywords):
        """조용히 빈 목록을 돌려주면 0% 언급률이 '측정 결과'로 리포트에 실린다."""
        with pytest.raises(QueryMappingError):
            build_lead_diagnosis_queries(region=region, specialty=specialty, keywords=keywords)
