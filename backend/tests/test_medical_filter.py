from app.utils.medical_filter import check_forbidden


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


def test_check_forbidden_allows_neutral_medical_text():
    text = "수술 후 회복기에는 무리한 운동을 피하는 것이 좋습니다."

    violations = check_forbidden(text)

    assert violations == []
