import pytest

from app.services.content_engine import FORBIDDEN_CHECK_FIELDS
from app.utils.authority_sources import infer_source_type, is_whitelisted_url
from app.utils.medical_filter import (
    FORBIDDEN_EXPRESSIONS,
    check_forbidden,
    check_forbidden_content_fields,
    check_forbidden_markdown,
    forbidden_vocabulary_for_prompt,
    markdown_visible_text,
)


def test_check_forbidden_catches_common_variants():
    text = "최고의 치료와 부작용 제로, 성공 확률 100%를 보장합니다."

    violations = check_forbidden(text)

    assert "최고" in violations
    assert "부작용 없는" in violations
    assert "성공률" in violations
    assert "100%" in violations


def test_check_forbidden_catches_2025_review_patterns():
    cases = [
        ("저희만의 노하우로 시술합니다.", "노하우"),
        ("효과를 보장하는 진료.", "효과 보장"),
        ("전국 유일의 진료 시스템", "유일"),
        ("최첨단 장비 도입", "최첨단"),
        ("흉터 없는 시술", "흉터 없는"),
        ("통증 없이 마무리되는 수술", "통증 없는"),
    ]
    for text, expected in cases:
        violations = check_forbidden(text)
        assert expected in violations, f"missed `{expected}` for {text!r}: {violations}"


def test_check_forbidden_catches_fullwidth_and_zero_width_evasion():
    # 전각 숫자/기호 + zero-width 삽입으로 정규식을 회피하려는 우회 (MED-1).
    cases = [
        ("성공 확률 １００％ 달성", "100%"),  # full-width digits + percent
        ("１등 진료", "1등"),  # full-width digit
        ("완​치 가능", "완치"),  # zero-width space inside 완치
        ("부작용‍ 제로", "부작용 없는"),  # ZWJ
    ]
    for text, expected in cases:
        violations = check_forbidden(text)
        assert expected in violations, f"missed `{expected}` for {text!r}: {violations}"


def test_check_forbidden_allows_neutral_medical_text():
    text = "수술 후 회복기에는 무리한 운동을 피하는 것이 좋습니다."

    violations = check_forbidden(text)

    assert violations == []


@pytest.mark.parametrize(
    "text",
    [
        "최고혈압과 최저혈압을 함께 기록합니다.",
        "검증 가능한 출처를 본문에 명시합니다.",
        "유일한 치료법은 아닙니다.",
        "유일무이한 치료법은 아닙니다.",
        "유일한 방법은 아닙니다.",
        "오늘은 상처가 아프지 않은 날입니다.",
    ],
)
def test_check_forbidden_allows_medical_terms_evidence_and_negative_hedges(text):
    assert check_forbidden(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("최고의 병원이라고 홍보합니다.", "최고"),
        ("저희 병원에서 검증된 치료입니다.", "검증된"),
        ("전국 유일한 치료법입니다.", "유일"),
        ("아프지 않은 시술을 보장합니다.", "통증 없는"),
        ("수술은 아프지 않습니다.", "통증 없는"),
    ],
)
def test_check_forbidden_keeps_real_promotional_claims_blocked(text, expected):
    assert expected in check_forbidden(text)


def test_unrelated_negative_language_does_not_hedge_a_unique_treatment_claim():
    assert "유일" in check_forbidden("전국 유일한 치료법이며 예약이 필요 없습니다")


# ── 마크다운 렌더 기준 검사 ────────────────────────────────────────────
# 공개 표면(site/app/[slug]/contents/[contentId]/page.tsx)이 ReactMarkdown + remarkGfm로
# 렌더하므로, 검사 시점 텍스트와 환자에게 보이는 텍스트가 달라지면 발행 게이트가 무의미해진다.


def test_markdown_emphasis_cannot_hide_a_forbidden_expression():
    """강조 구분자를 끼워 넣어 필터를 우회하는 것을 차단한다.

    `최**고**의`는 `최<strong>고</strong>의`로 렌더되어 환자에게는 "최고의"로 보인다.
    """
    cases = [
        ("최**고**의 진료", "최고"),
        ("최*고*의 진료", "최고"),
        ("***최고***의 진료", "최고"),
        ("완**치**를 약속합니다", "완치"),
        ("1**등** 병원", "1등"),
        ("독**보**적 기술", "독보적"),
        # 여는 구분자가 공백 뒤에 오는 형태도 렌더되면 마찬가지로 보인다.
        ("부작용 **없는** 시술", "부작용 없는"),
        # `_`는 단어 경계에서 강조로 동작한다.
        ("부작용 _없는_ 시술", "부작용 없는"),
        ("__최고__의 진료", "최고"),
        # remarkGfm 취소선 — singleTilde:false는 홑물결만 끄고 `~~`는 유효하다.
        ("최~~고~~의 진료", "최고"),
        ("1~~등~~ 병원", "1등"),
    ]
    for text, expected in cases:
        violations = check_forbidden_markdown(text)
        assert expected in violations, f"우회 통과: {text!r} -> {violations}"


def test_markdown_check_does_not_invent_violations_that_are_not_rendered():
    """렌더러가 리터럴로 보여주는 문자는 제거하면 안 된다.

    지우면 화면에 없는 위반을 발명하게 되고, 같은 필터가 공개 표면 직렬화에도
    쓰이므로 오탐은 병원 정보를 지우는 결과로 이어진다.
    """
    literal_cases = [
        "최_고의 진료",  # `_`는 intraword 강조가 아니라 밑줄이 그대로 보인다
        "안전한~시술",  # remarkGfm singleTilde:false → 리터럴
        "최<span>고</span>",  # rehypeRaw 미설치 → 이스케이프되어 보인다
        "5*3 곱셈",  # 짝 없는 별표 → 리터럴
        "최*고 짝없음",
        "* 목록 항목",  # 목록 표지
        # `_`가 단어 내부에 있으면 강조가 아니라 밑줄이 그대로 보인다.
        "최_고_의 진료",
        "snake_case_이름",
    ]
    for text in literal_cases:
        assert markdown_visible_text(text) == text, f"렌더되지 않는 변형 발생: {text!r}"


def test_code_delimiters_are_invisible_but_their_contents_are_not():
    """백틱·코드펜스는 화면에 없고 내용만 보인다.

    구분자를 남기면 `최`고`의`(화면엔 "최고의")를 놓치고, 내용의 별표를 지우면
    코드 안의 리터럴 별표가 사라져 없던 위반이 생긴다. 둘 다 틀린다.
    """
    # 구분자는 사라진다 → 우회가 잡힌다
    assert check_forbidden_markdown("최`고`의 진료") == ["최고"]
    # 내용의 별표는 화면에 보이므로 강조로 해석하지 않는다 → 오탐이 없다
    assert check_forbidden_markdown("`최**고**` 라는 표기") == []
    assert check_forbidden_markdown("```\n최**고**\n```") == []


def test_link_destinations_are_not_scanned_but_link_text_is():
    """링크 목적지는 화면에 보이지 않는다 — 표시 텍스트만 검사 대상이다."""
    # 목적지에만 있는 표현은 환자에게 보이지 않는다 → 오탐이면 안 된다
    assert check_forbidden_markdown("[안내](https://example.test/최고-병원)") == []
    # 표시 텍스트를 링크로 쪼개 숨기는 우회는 잡혀야 한다
    assert check_forbidden_markdown("최[고](https://example.test)의 진료") == ["최고"]


def test_markdown_emphasis_does_not_join_across_lines():
    """줄(블록) 경계를 넘어 단어가 붙지 않는다."""
    assert "최고" not in markdown_visible_text("- 최\n- 고")
    assert check_forbidden_markdown("- 최\n- 고") == []


def test_infer_source_type_maps_korean_and_global_authority_domains():
    assert infer_source_type("https://www.hira.or.kr/foo") == "GOV_KR"
    assert infer_source_type("https://kams.or.kr/policy") == "ACADEMIC_KR"
    assert infer_source_type("https://www.nih.gov/article") == "GOV_GLOBAL"
    assert infer_source_type("https://www.mayoclinic.org/diseases/x") == "CLINIC_REFERENCE"
    assert infer_source_type("https://ko.wikipedia.org/wiki/X") == "ENCYCLOPEDIA"
    assert infer_source_type("https://random-blog.com/x") is None
    assert infer_source_type("https://kdca.go.kr.attacker.example/x") is None
    assert infer_source_type("") is None


def test_is_whitelisted_url_blocks_non_authority_domains():
    assert is_whitelisted_url("https://www.kdca.go.kr/x") is True
    assert is_whitelisted_url("https://pubmed.ncbi.nlm.nih.gov/12345") is True
    assert is_whitelisted_url("https://ad-blog.example.com/promo") is False
    assert is_whitelisted_url("https://kdca.go.kr.attacker.example/promo") is False
    assert is_whitelisted_url("") is False


def test_fields_are_checked_with_the_right_renderer_for_each_field():
    """본문만 마크다운이고 제목·메타·FAQ는 리터럴 렌더된다.

    합쳐서 한 번에 마크다운으로 해석하면 양방향으로 틀린다 — 아래 4가지가 그 증거다.
    """
    fields = FORBIDDEN_CHECK_FIELDS

    # 1) 제목의 별표는 화면에 그대로 보인다 → 위반이 아니다
    assert check_forbidden_content_fields({"title": "최*고*", "body": "일반 본문"}, fields) == []

    # 2) 필드 경계를 넘어 강조 쌍이 합성되면 안 된다
    assert check_forbidden_content_fields({"title": "최*고", "body": "의*"}, fields) == []

    # 3) 필드 경계를 넘어 코드 스팬이 합성되어 본문 위반을 숨기면 안 된다
    hidden = check_forbidden_content_fields(
        {"title": "안내`", "body": "최**고**의 진료", "meta_description": "`요약"}, fields
    )
    assert "최고" in hidden, f"필드 합성으로 본문 위반이 은폐됐다: {hidden}"

    # 4) 본문의 강조 우회는 잡힌다
    assert "최고" in check_forbidden_content_fields(
        {"title": "정상 제목", "body": "최**고**의 진료"}, fields
    )


def test_link_syntax_that_is_not_a_link_stays_visible_and_is_checked():
    """목적지에 공백이 있으면 CommonMark 링크가 아니다 — 화면에 그대로 보인다.

    링크로 오인해 지우면 금지 표현이 검사를 빠져나가 공개된다(미탐).
    """
    assert check_forbidden_markdown("회복 기간[개인차 있음](통증 없는 경우도 있습니다)") == [
        "통증 없는"
    ]
    # 반대로 진짜 링크의 목적지는 화면에 없으므로 검사하지 않는다.
    assert check_forbidden_markdown("[안내](https://example.test/최고-병원)") == []
    assert check_forbidden_markdown('[안내](https://example.test/a "최고 자료")') == []


# ── 문맥 예외 (2026-09-12 수율 계획 WP-2) ────────────────────────────────────
# 광고가 아닌 임상·통계·부정 문장까지 막으면 정상 글이 통째로 버려진다. 아래 예외는
# **문장이 광고 주장을 부정하거나 수치 단위를 말하는** 좁은 문맥만 통과시킨다.
# 각 항목은 음성(임상 문장 통과)과 양성(광고 문형 계속 차단)을 쌍으로 확인한다.


@pytest.mark.parametrize(
    "text",
    [
        "1등급 화상은 표피에만 손상이 있습니다.",
        "대장암은 국내 암 사망원인 1위입니다.",
        "국내 발병 원인 1위로 꼽히는 질환입니다.",
        "발생률 1위 질환의 초기 증상을 정리했습니다.",
        "이 질환은 완치가 어렵고 꾸준한 관리가 중요합니다.",
        "약을 계속 먹어도 완치되지 않습니다.",
        "검사가 완치를 보장할 수 없습니다.",
        "음성 결과가 완치를 의미하지 않습니다.",
        "내시경 정확도가 100%는 아닙니다.",
        "백신으로 100%로 예방할 수 없습니다.",
        "부작용 없는 약은 없습니다.",
        "성공률은 개인차가 커 단정할 수 없습니다.",
        "성공 확률은 환자 상태에 따라 다릅니다.",
        "해열제를 먹어도 최고 39도까지 오를 수 있습니다.",
        "하루 최고 40mg까지 처방합니다.",
        "탁월한 효과를 기대하기 어렵습니다.",
        "첨단 기술이 결과를 보장하지 않습니다.",
        "시술이 아프지 않을까 걱정하는 분이 많습니다.",
    ],
)
def test_clinical_negation_and_statistics_are_not_medical_ads(text):
    assert check_forbidden(text) == [], text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("국내 1위 병원입니다.", "1등"),
        ("지역 1등 의원으로 뽑혔습니다.", "1등"),
        # 앞쪽의 역학 통계가 뒤쪽 광고 주장의 면죄부가 되면 안 된다.
        ("발생률 1위 질환을 가장 많이 치료하는 국내 1위 병원입니다.", "1등"),
        ("완치율이 높은 치료입니다.", "완치"),
        ("꾸준히 받으면 완치 가능합니다.", "완치"),
        ("이 시술로 완치됩니다.", "완치"),
        ("100% 안전하게 끝납니다.", "100%"),
        ("성공률 99%를 기록했습니다.", "성공률"),
        ("부작용 없는 시술입니다.", "부작용 없는"),
        ("최고의 진료를 약속합니다.", "최고"),
        ("탁월한 효과가 있습니다.", "최우수"),
        ("첨단 기술로 시술합니다.", "최첨단"),
        ("이 시술은 아프지 않습니다.", "통증 없는"),
    ],
)
def test_advertising_phrasings_stay_blocked_after_the_exemptions(text, expected):
    assert expected in check_forbidden(text), text


def test_exemptions_apply_to_markdown_body_and_plain_fields_alike():
    """발행 게이트가 실제로 쓰는 진입점(check_forbidden_content_fields)으로 확인한다.

    본문은 마크다운 렌더 기준, 제목·메타·FAQ는 평문 기준으로 검사되므로 두 경로가
    같은 문맥 예외를 갖는지 직접 확인해야 한다.
    """
    clinical = {
        "title": "완치가 어려운 질환을 관리하는 방법",
        "body": (
            "## 경과\n"
            "이 질환은 **완치가 어렵**고 꾸준한 관리가 필요합니다.\n"
            "검사 정확도가 100%는 아니며, 성공률은 개인차가 큽니다.\n"
        ),
        "meta_description": "국내 사망원인 1위 질환의 관리 기준을 정리했습니다.",
        "faq_question": "시술이 아프지 않을까요?",
        "faq_answer_summary": "부작용 없는 약은 없으므로 상태에 맞춰 조절합니다.",
    }
    assert check_forbidden_content_fields(clinical, FORBIDDEN_CHECK_FIELDS) == []

    promotional = dict(clinical, body="## 안내\n저희는 완치율 100%를 달성했습니다.")
    violations = check_forbidden_content_fields(promotional, FORBIDDEN_CHECK_FIELDS)
    assert "완치" in violations and "100%" in violations


def test_forbidden_vocabulary_for_prompt_covers_pattern_only_expressions():
    """작가에게 보여 주는 목록이 실제로 글을 버리는 어휘를 포함해야 한다.

    표시용 21개만 보여 주던 동안 "1위"·"탁월"·"완치율"·"비교 불가"는 작가가 모르는
    금지어였고, 그 어휘 하나로 완성된 글이 폐기됐다.
    """
    vocabulary = forbidden_vocabulary_for_prompt()

    assert set(FORBIDDEN_EXPRESSIONS).issubset(set(vocabulary))
    for expression in (
        "1위",
        "일등",
        "최상",
        "으뜸",
        "탁월",
        "가장 우수",
        "가장 잘",
        "가장 뛰어",
        "완치율",
        "완전 치료",
        "완전 회복",
        "백 퍼센트",
        "성공 확률",
        "비교 불가",
        "첨단 기술",
        "첨단 장비",
        "유일무이",
        "확인된 효과",
        "아프지 않은 시술",
        "흉터 남지 않",
    ):
        assert expression in vocabulary, expression
    # 정적 시스템 블록(프롬프트 캐시 접두어)에 들어가므로 호출마다 동일해야 한다.
    assert forbidden_vocabulary_for_prompt() == vocabulary
    assert len(set(vocabulary)) == len(vocabulary)
