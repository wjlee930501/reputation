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
    # 예시는 계속 막히는 문형이어야 한다 — 무증상 설명("통증 없는 경우")은 E3 뒤로 예외다.
    assert check_forbidden_markdown("회복 기간[개인차 있음](통증 없이 시술하는 경우도 있습니다)") == [
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


# ── D24(2026-10-13 대표 결정): 가격 우위·할인·이벤트·무료 유인은 모든 경로에서 하드 차단 ──────


@pytest.mark.parametrize(
    "text",
    [
        "지역 최저가로 임플란트를 제공합니다.",
        "업계 최저 비용으로 모십니다.",
        "타 병원보다 30% 저렴한 비용으로 시술합니다.",
        "가장 저렴한 비용으로 받을 수 있습니다.",
        "이번 달 20% 할인 이벤트를 진행합니다.",
        "할인가로 제공합니다.",
        "１０％ 할인",  # 전각 우회
        "개원 이벤트 특가로 모십니다.",
        "이벤트 기간에만 드리는 혜택입니다.",
        "첫 방문 무료 상담을 드립니다.",
        "도수치료 시술 전액 무료입니다.",
        "본인부담금 면제 혜택을 드립니다.",
        "선착순 10명 사은품 증정",
        "체험단을 모집합니다.",
        "페이백 혜택을 드립니다.",
        # 공적 검진 문맥이 가까이 있어도 병원 자기 주장이면 계속 막는다.
        "본원은 국가 검진과 함께 무료 검진을 해 드립니다.",
        # 공적 제도 말이 앞에 있어도 병원이 해 주겠다는 말이 같은 문장에 있으면 막는다.
        "국가 암검진 대상자가 아니어도 무료 검진을 해 드립니다.",
        "저희 병원은 이번 달 무료 검진 이벤트를 합니다.",
        "최저가 보장",
        "최저가로 모십니다.",
        "타 병원보다 싸게 해 드립니다.",
        "본원에서 본인부담금 면제",
        "본인부담금 면제로 모십니다.",
        "본인부담금 할인",
        "본인부담금 대납",
        # 2026-10-13 교차 검토 — 공적 근거 없는 면제·'대상'만 붙은 무료, 혜택 제공의 활용형.
        "본인부담금 면제! 지금 예약하세요.",
        "신규 고객 대상 무료 검사 예약을 받습니다.",
        "할인해 드립니다.",
        "할인을 제공합니다.",
        "할인 적용됩니다.",
        "할인이 적용됩니다.",
        "할인을 받으실 수 있습니다.",
        "무료로 상담해 드립니다.",
        "무료로 제공합니다.",
        "무료 상담을 드립니다.",
        "무료 검사를 제공합니다.",
        "보건소 무료 검진은 미리 예약하세요.",
        # 공적 기관과 함께여도 병원이 스스로 제공한다고 하면 유인이다.
        "국가 무료 검진을 본원에서 제공합니다.",
        "본원은 보건소와 함께 무료 검사를 제공합니다.",
        "1.5% 할인",
        # 자격(대상) 문장이어도 병원 자기 지칭·혜택·예약 권유가 붙으면 유인이다.
        "본인부담금 면제 대상으로 모십니다.",
        "저희 병원은 본인부담금 면제 대상을 늘렸습니다.",
        "본인부담금 면제 대상이니 지금 예약하세요.",
    ],
)
def test_price_discount_and_inducement_claims_are_blocked(text):
    assert "할인·유인" in check_forbidden(text)


@pytest.mark.parametrize(
    "text",
    [
        "최저혈압이 90 이상이면 진료가 필요합니다.",
        "최저 기온이 영하로 내려가면 혈압이 오릅니다.",
        "심혈관 이벤트가 발생할 위험이 커집니다.",
        "심혈관 이벤트 중 가장 흔한 것은 심근경색입니다.",
        "국가건강검진은 무료로 받을 수 있습니다.",
        "국가에서 시행하는 무료 검진 대상인지 확인하세요.",
        "의료급여 수급자는 본인부담금이 면제되는 경우가 있습니다.",
        "비용은 의료기관마다 다릅니다.",
        "무료 주차가 가능합니다.",
        # 비용 글(D23 이후 참고자료 없이 공개)에 흔한 제도·비교 문장 — 유인이 아니다.
        "본인부담상한제에 따라 본인부담금 환급을 받을 수 있습니다.",
        "차상위 계층은 본인부담금 면제 대상입니다.",
        "본인부담금 0원인 산정특례 항목이 있습니다.",
        "보건소에서 무료 검사를 받을 수 있습니다.",
        "만 40세 이상은 2년마다 무료 검진 대상입니다.",
        "금연 상담 무료 (보건소)",
        "혈당이 최저가 되는 새벽",
        "타 병원보다 비싸다고 느낄 수 있습니다.",
        # '제공'의 주체가 공적 기관이면 유인이 아니다.
        "보건소에서 무료 검사를 제공합니다.",
        "보건소 무료 검사 대상입니다.",
        "무료 검사는 보건소에서 받을 수 있습니다.",
        "건강보험 보장성 강화로 본인부담금 0원인 산정특례 항목이 있습니다.",
        "무료 주차가 가능하니 예약하세요.",
        # 본인부담 면제·환급 '대상'은 자격 안내다(2026-10-13 결정).
        "본인부담금 환급 대상입니다.",
        "본인부담금 면제 대상입니다.",
    ],
)
def test_public_programs_and_clinical_terms_are_not_inducement(text):
    assert check_forbidden(text) == []


def test_inducement_label_is_enforced_by_every_publication_field_and_the_markdown_body():
    """생성·발행 게이트·공개 가시성·PATCH가 쓰는 같은 필드 검사기에서 걸린다."""

    fields = {
        "title": "허리 통증 안내",
        "body": "## 안내\n이번 달 **무료 상담**을 드립니다.",
        "meta_description": "허리 통증 안내",
        "faq_question": None,
        "faq_answer_summary": None,
    }
    assert "할인·유인" in check_forbidden_content_fields(fields, FORBIDDEN_CHECK_FIELDS)
    title_only = dict(fields, body="## 안내\n본문", title="개원 이벤트 — 허리 통증 안내")
    assert "할인·유인" in check_forbidden_content_fields(title_only, FORBIDDEN_CHECK_FIELDS)


def test_writer_sees_the_inducement_vocabulary_but_the_essence_safety_text_is_unchanged():
    """작가 프롬프트에는 새 어휘를 보이되, Essence 필수 안전 문구의 원문은 바꾸지 않는다."""

    from app.services.essence_engine import MANDATORY_AVOID_MESSAGES

    vocabulary = forbidden_vocabulary_for_prompt()
    for expression in ("최저가", "무료 상담", "할인 이벤트", "본인부담금 면제", "선착순"):
        assert expression in vocabulary
    assert "최저가" not in FORBIDDEN_EXPRESSIONS
    assert MANDATORY_AVOID_MESSAGES == (
        "의료광고 공통 금지 표현을 홍보 문구로 사용하지 않습니다: " + ", ".join(FORBIDDEN_EXPRESSIONS),
    )
    assert len(FORBIDDEN_EXPRESSIONS) == 21


# ── E3(2026-10-13): 확인된 오탐을 필터 안의 문맥 예외로 푼다(양방향) ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "고혈압은 초기에 통증 없이 진행합니다.",
        "갑상선 결절은 대부분 통증 없는 혹으로 발견됩니다.",
        "수술 후 완전 회복까지 3개월이 걸립니다.",
        "완전 회복까지 약 6~8주가 필요합니다.",
        "완전 회복까지는 보통 3개월이 걸립니다.",
        "완전 회복까지 수개월이 걸립니다.",
        "증상이 최고조에 이릅니다.",
        "식후 혈당이 최고치를 기록합니다.",
        "비급여 항목은 100% 본인부담입니다.",
        "임상시험에서 확인된 효과는 아직 제한적입니다.",
        "첨단 장비로 정밀하게 촬영합니다.",
    ],
)
def test_confirmed_false_positives_are_not_medical_ads(text):
    assert check_forbidden(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("통증 없이 시술합니다.", "통증 없는"),
        ("시술 후 통증 없이 회복합니다.", "통증 없는"),
        ("저희 병원은 통증 없는 진료를 합니다.", "통증 없는"),
        ("수술 후 완전 회복됩니다.", "완치"),
        ("완전 회복을 보장합니다.", "완치"),
        ("최고치료법으로 진료합니다.", "최고"),
        ("100% 효과가 있습니다.", "100%"),
        ("저희 병원 임상에서 확인된 효과입니다.", "검증된"),
        ("확인된 효과로 통증을 줄입니다.", "검증된"),
        ("본원은 첨단 장비를 갖췄습니다.", "최첨단"),
        ("첨단 장비를 갖춘 본원에서 검사합니다.", "최첨단"),
        ("첨단 시술로 치료합니다.", "최첨단"),
        # 부재형 예외가 새던 문장 — 양성 근거(질환 주어·촬영 과정)가 없으면 막는다.
        ("통증 없이 편안하고 안전하게 받으실 수 있습니다.", "통증 없는"),
        ("통증 없이 하루 만에 일상으로 복귀할 수 있는 레이저입니다.", "통증 없는"),
        ("첨단 장비를 갖추고 있습니다.", "최첨단"),
        # 병원 자기 주장은 12자 창 밖이어도 같은 문장이면 막는다.
        ("저희 병원에서 진행한 임상 연구에서 확인된 효과입니다.", "검증된"),
    ],
)
def test_the_same_words_stay_blocked_as_claims(text, expected):
    assert expected in check_forbidden(text)


# ── 결과 보장은 어떤 문맥 예외보다 먼저 본다(2026-10-13 교차 검토) ──────────────────────
# 예외의 양성 근거(기간·질환 주어·공적 제도)를 갖춰도 같은 문장이 결과를 보장하면 광고다.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("완전 회복까지 1주를 보장합니다.", "완치"),
        ("증상은 통증 없이 사라짐을 보장합니다.", "통증 없는"),
        ("완전 회복까지 2주면 된다고 약속드립니다.", "완치"),
        ("완전 회복까지 3개월이면 무조건 됩니다.", "완치"),
        ("완전 회복까지 1주라고 장담합니다.", "완치"),
        ("고혈압은 초기에 통증 없이 반드시 낫습니다.", "통증 없는"),
        ("결절은 대부분 통증 없는 혹으로 확실히 사라집니다.", "통증 없는"),
        ("임상시험에서 확인된 효과가 틀림없이 나타납니다.", "검증된"),
        ("보건소에서 무료 검사를 받으면 100% 낫습니다.", "할인·유인"),
    ],
)
def test_an_outcome_guarantee_overrides_every_context_exemption(text, expected):
    assert expected in check_forbidden(text)


@pytest.mark.parametrize(
    "text",
    [
        # 보장을 부정하는 말, 보험 용어 '보장성', 권고형 '반드시 …하세요'는 보장 표지가 아니다.
        "완치를 보장할 수 없습니다.",
        "완전 회복까지 3개월이 걸리며 결과를 보장할 수는 없습니다.",
        "원인이 확실하지 않은 경우 완전 회복까지 수개월이 걸립니다.",
        "고혈압은 초기에 통증 없이 진행하므로 반드시 정기 검진을 받으세요.",
        "비급여 항목은 100% 본인부담입니다.",
    ],
)
def test_negated_guarantees_and_advice_do_not_revoke_an_exemption(text):
    assert check_forbidden(text) == []


# 예외마다 통과해야 하는 표본 하나. 새 예외는 여기에 표본을 더해야 이 검사를 통과한다.
_EXEMPTION_SAMPLES = {
    "역학 통계 문맥의 순위 인용": ("1등", "위암은 국내 발생률 1위 암입니다."),
    "정점을 뜻하는 최고조·최고치": ("최고", "증상이 최고조에 이릅니다."),
    "수치 상한(체온·용량·연령·횟수)": ("최고", "체온이 최고 39도까지 오릅니다."),
    "연구·문헌 결과의 인용": ("검증된", "임상시험에서 확인된 효과는 아직 제한적입니다."),
    "우월 주장을 부정하는 문장": ("최우수", "탁월한 효과를 기대하기 어렵습니다."),
    "완전 회복까지 걸리는 기간 안내": ("완치", "수술 후 완전 회복까지 3개월이 걸립니다."),
    "완치 주장을 바로 부정·한정": ("완치", "완치가 어렵습니다."),
    "본인부담 비율 안내": ("100%", "비급여 항목은 100% 본인부담입니다."),
    "100% 주장을 바로 부정": ("100%", "정확도가 100%는 아닙니다."),
    "성공률을 개인차·불확실성으로 한정": ("성공률", "성공률은 개인차가 커 단정할 수 없습니다."),
    "무부작용 주장을 존재 부정으로 반박": ("부작용 없는", "부작용 없는 약은 없습니다."),
    "촬영·판독 과정의 장비 설명": ("최첨단", "첨단 장비로 정밀하게 촬영합니다."),
    "첨단 기술 주장을 부정": ("최첨단", "첨단 기술이 효과를 기대하기 어렵습니다."),
    "공적 제도의 무료 검진·검사·상담 안내": ("할인·유인", "보건소에서 무료 검사를 받을 수 있습니다."),
    "공적 제도의 무료 검진·검사·상담 안내(제도가 뒤에 오는 문장)": (
        "할인·유인",
        "무료 검사는 보건소에서 받을 수 있습니다.",
    ),
    "연령·주기로 정한 정기 검진 대상 안내": ("할인·유인", "만 40세 이상은 2년마다 무료 검진 대상입니다."),
    "건강보험 제도의 본인부담 면제·환급 안내": ("할인·유인", "차상위 계층은 본인부담금 면제 대상입니다."),
    "본인부담 면제·환급 자격(대상) 안내": ("할인·유인", "본인부담금 환급 대상입니다."),
    "무증상 질환의 경과 설명": ("통증 없는", "고혈압은 초기에 통증 없이 진행합니다."),
    "환자의 의문·걱정 표현": ("통증 없는", "시술이 아프지 않을까 걱정하는 분이 많습니다."),
}

# 기준 커밋(7a1e2174)의 필터가 '… 보장합니다' 변형을 막던 표본 — 이 표본들은 회귀하면 안 된다.
_BLOCKED_BY_BASE_WITH_GUARANTEE = frozenset(
    {
        "정점을 뜻하는 최고조·최고치",
        "연구·문헌 결과의 인용",
        "완전 회복까지 걸리는 기간 안내",
        "본인부담 비율 안내",
        "촬영·판독 과정의 장비 설명",
        "무증상 질환의 경과 설명",
    }
)


def test_every_context_exemption_has_a_sample():
    from app.utils.medical_filter import _ALLOWED_CONTEXTS

    reasons = {exemption.reason for group in _ALLOWED_CONTEXTS.values() for exemption in group}
    assert reasons == set(_EXEMPTION_SAMPLES)
    assert _BLOCKED_BY_BASE_WITH_GUARANTEE <= reasons


@pytest.mark.parametrize(("reason", "label_and_sample"), sorted(_EXEMPTION_SAMPLES.items()))
def test_every_exemption_sample_passes_but_its_guarantee_variant_is_blocked(reason, label_and_sample):
    label, sample = label_and_sample
    assert check_forbidden(sample) == [], reason
    variant = sample.rstrip(".") + " 보장합니다."
    assert label in check_forbidden(variant), reason


# ── 정규식 실행 시간(2026-10-13 교차 검토) ───────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "9" * 16000 + " 할인",
        "9" * 16000 + "% 할인",
        "9." * 8000 + "% 할인",
        "무료 " * 5000,
        "면제 " * 5000,
        "할인을 " * 5000,
        "보건소 무료 검사 " * 2000,
        "다른 병원보다 " * 3000,
        "최고 39도 " * 3000,
        "완전 회복까지 3개월 " * 1500 + "보장",
        "병원" * 8000,
        "저희" * 8000,
    ],
)
def test_long_adversarial_inputs_finish_quickly(text):
    import time

    started = time.perf_counter()
    check_forbidden(text)
    assert time.perf_counter() - started < 0.1
