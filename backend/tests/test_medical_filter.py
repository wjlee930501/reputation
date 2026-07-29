from app.utils.authority_sources import infer_source_type, is_whitelisted_url
from app.services.content_engine import FORBIDDEN_CHECK_FIELDS
from app.utils.medical_filter import (
    check_forbidden,
    check_forbidden_content_fields,
    check_forbidden_markdown,
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
