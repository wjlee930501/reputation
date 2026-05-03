from app.utils.medical_filter import check_forbidden


def test_check_forbidden_catches_common_variants():
    text = "최고의 치료와 부작용 제로, 성공 확률 100%를 보장합니다."

    violations = check_forbidden(text)

    assert "최고" in violations
    assert "부작용 없는" in violations
    assert "성공률" in violations
    assert "100%" in violations
