"""무료 진단 질의 생성 (설계 T-3 · T-4 · T-16).

고정하는 것은 문자열이 아니라 **불변 규칙**이다:
지역이 항상 들어간다 / 병원명이 절대 안 들어간다 / 항상 3개다 / 과장 표현이 없다.
"""
import pytest

from app.services.keyword_analysis import KeywordClass, analyze_keyword
from app.services.query_mapper import (
    QUERY_SLOT_COUNT,
    QueryMappingError,
    build_lead_diagnosis_queries,
    object_particle,
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

    def test_hospital_name_shaped_keyword_never_reaches_a_query(self):
        """병원명 형태의 키워드는 질의에 실리지 않는다.

        '장편한외과의원'은 진료과 접미사를 가진 기관명이라 검색어 형태로 분류되어
        슬롯에서 빠진다. 실제 차단은 접수 검증(PRD F1-4)이 원시 키워드 단계에서
        먼저 하므로 여기까지 오지 않지만, 방어가 한 겹 더 있는 편이 맞다.
        """
        queries = build_lead_diagnosis_queries(
            region="수서역", specialty="외과", keywords=["장편한외과의원"]
        )
        assert not any("장편한외과의원" in q["text"] for q in queries)
        assert len(queries) == QUERY_SLOT_COUNT


class TestNoUngrammaticalQueries:
    """2026-08-14 회귀 방지 — 실제 신청자에게 나갔던 비문들.

    이전 분류기는 실측 키워드 16개 중 13개를 unknown으로 떨어뜨렸고, unknown 폴백이
    "{키워드} 받으려는데"였다. 그래서 이런 문장이 리포트에 그대로 공개됐다:

        "척추 받으려는데 보라매역 근처 병원 어디가 좋아?"
        "우울증 받으려는데 용산역 근처 병원 어디가 좋아?"

    리포트는 질의 원문을 공개하므로, 원장이 이걸 보면 측정 전체의 신뢰가 무너진다.
    """

    # (지역, 진료과, 키워드) — 전부 프로덕션에서 실제로 접수된 조합이다.
    PRODUCTION_CASES = [
        ("보라매역", "재활의학과", ["척추", "관절", "통증", "비수술"]),
        ("용산역", "정신과", ["우울증", "adhd", "불안증", "불면증"]),
        ("팔달구", "항문외과", ["대장내시경", "치질"]),
        ("마포역", "정형외과", ["피지낭종", "지방종", "내성발톱", "PRP주사"]),
        ("군자역", "정형외과", ["군자역 정형외과", "광진구 정형외과"]),
    ]

    @pytest.mark.parametrize("region,specialty,keywords", PRODUCTION_CASES)
    def test_no_receiving_verb_on_non_procedure_keywords(self, region, specialty, keywords):
        """'받으려는데'는 시술에만 붙는다. 질환·증상·부위에 붙으면 비문이다."""
        queries = build_lead_diagnosis_queries(
            region=region, specialty=specialty, keywords=keywords
        )
        for item in queries:
            term = item.get("canonical_term")
            if not term or item.get("keyword_class") not in ("DISEASE", "SYMPTOM", "BODY_PART"):
                continue
            # 질환·증상·부위를 **직접** 받다의 목적어로 쓰면 비문이다.
            # ("피지낭종 진료를 받으려는데"는 목적어가 '진료'라 정문이다.)
            for verb in ("받으려는데", "받을 수 있는", "받으러"):
                assert f"{term} {verb}" not in item["text"], item
                assert f"{term}{verb}" not in item["text"], item

    def test_symptom_and_body_part_templates_actually_fire(self):
        """증상형 템플릿은 코드에 있었지만 실전에서 **한 번도 발화되지 않았다.**"""
        queries = build_lead_diagnosis_queries(
            region="보라매역", specialty="재활의학과",
            keywords=["척추", "관절", "통증", "비수술"],
        )
        kinds = {q["kind"] for q in queries}
        assert "부위형" in kinds
        assert "증상형" in kinds

    def test_disease_keywords_are_not_labelled_as_procedures(self):
        """이전 버전은 질환의 대체 템플릿 1순위가 시술형이라 분류와 문장이 뒤집혔다."""
        queries = build_lead_diagnosis_queries(
            region="용산역", specialty="정신과", keywords=["우울증"]
        )
        for item in queries:
            if item.get("keyword_class") == "DISEASE":
                assert item["kind"] == "질환형", item


class TestClassification:
    @pytest.mark.parametrize(
        "keyword,expected",
        [
            ("대장내시경", KeywordClass.PROCEDURE),
            ("PRP주사", KeywordClass.PROCEDURE),
            ("우울증", KeywordClass.DISEASE),
            ("adhd", KeywordClass.DISEASE),
            ("치질", KeywordClass.DISEASE),
            ("피지낭종", KeywordClass.DISEASE),
            ("통증", KeywordClass.SYMPTOM),
            ("척추", KeywordClass.BODY_PART),
            ("비수술", KeywordClass.CARE_SERVICE),
            ("군자역 정형외과", KeywordClass.SEARCH_PHRASE),
            ("영양수액", KeywordClass.UNKNOWN),
        ],
    )
    def test_real_keywords_are_classified(self, keyword, expected):
        assert analyze_keyword(keyword).keyword_class is expected

    def test_case_and_width_variants_share_one_semantic_key(self):
        """정규화가 없으면 'adhd'와 'ADHD'가 슬롯을 각각 차지한다."""
        keys = {analyze_keyword(v).semantic_key for v in ("adhd", "ADHD", "ＡＤＨＤ")}
        assert len(keys) == 1

    def test_unknown_keywords_get_a_grammatical_fallback(self):
        """분류 실패는 품질 저하일 뿐 비문이 되어서는 안 된다 (PRD F2-4)."""
        queries = build_lead_diagnosis_queries(
            region="망원동", specialty="가정의학과", keywords=["영양수액"]
        )
        keyword_queries = [q for q in queries if "영양수액" in q["text"]]
        assert keyword_queries
        assert keyword_queries[0]["kind"] == "탐색형"
        assert "영양수액 받으려는데" not in keyword_queries[0]["text"]

    @pytest.mark.parametrize(
        "word,expected",
        [("허리디스크", "가"), ("복통", "이"), ("여드름", "이"), ("눈", "이"), ("코", "가")],
    )
    def test_subject_particle_follows_the_final_consonant(self, word, expected):
        """받침 계산이 틀리면 '허리디스크이 계속되는데'처럼 환자가 쓰지 않는 문장이 된다 (F2-1)."""
        assert subject_particle(word) == expected

    @pytest.mark.parametrize(
        "word,expected",
        [("대장내시경", "을"), ("PRP주사", "를"), ("건강검진", "을"), ("도수치료", "를")],
    )
    def test_object_particle_follows_the_final_consonant(self, word, expected):
        assert object_particle(word) == expected


class TestKeywordSelection:
    def test_confident_keywords_win_over_input_order(self):
        """이전에는 앞 2개만 잘라 써서, 제대로 분류되는 키워드가 뒤에 있으면 버려졌다.

        실제로 '통증'(증상)과 'PRP주사'(시술)가 그렇게 탈락하고 미분류 키워드가 쓰였다.
        """
        queries = build_lead_diagnosis_queries(
            region="마포역", specialty="정형외과",
            keywords=["피지낭종", "지방종", "내성발톱", "PRP주사"],
        )
        used = {q.get("canonical_term") for q in queries}
        # 4번째지만 시술로 확실히 분류되므로 채택되어야 한다.
        assert "PRP주사" in used

    def test_class_diversity_is_preferred(self):
        queries = build_lead_diagnosis_queries(
            region="보라매역", specialty="재활의학과",
            keywords=["척추", "관절", "통증", "비수술"],
        )
        classes = [q.get("keyword_class") for q in queries if q.get("keyword_class")]
        assert len(set(classes)) == len(classes), "같은 클래스로만 채우면 다양성이 없다"


class TestSemanticDeduplication:
    def test_search_phrase_keywords_do_not_duplicate_the_specialty_anchor(self):
        """'군자역 정형외과'는 문자열이 달라도 슬롯 1과 같은 질문이다.

        이전에는 3개 질의가 사실상 1개인 병원이 있었고, 그 병원의 언급률이 100%였다.
        """
        queries = build_lead_diagnosis_queries(
            region="군자역", specialty="정형외과",
            keywords=["군자역 정형외과", "광진구 정형외과"],
        )
        assert len({q["text"] for q in queries}) == QUERY_SLOT_COUNT
        # 지역이 두 번 들어간 문장("군자역 정형외과 … 군자역 근처")이 나오면 안 된다.
        for item in queries:
            assert item["text"].count("군자역") == 1, item

    def test_low_diversity_is_recorded_not_hidden(self):
        """키워드를 하나도 쓰지 못한 진단은 그 사실이 메타데이터에 남아야 한다."""
        queries = build_lead_diagnosis_queries(
            region="군자역", specialty="정형외과", keywords=["군자역 정형외과"]
        )
        assert any(q.get("classifier_source") == "specialty_fallback" for q in queries)

    def test_embedded_region_is_stripped_and_residue_reclassified(self):
        """'마포역 PRP주사'는 지역을 떼어내고 남은 임상 개념으로 분류해야 한다."""
        analysis = analyze_keyword("마포역 PRP주사")
        assert analysis.keyword_class is KeywordClass.PROCEDURE
        assert analysis.canonical_term == "PRP주사"
        assert analysis.embedded_region == "마포역"


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
