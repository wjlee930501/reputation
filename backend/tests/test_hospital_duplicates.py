"""F-1: 병원 중복 판정은 모든 생성 경로에서 같은 규칙이어야 한다."""

from app.services.hospital_duplicates import (
    normalize_hospital_name,
    normalize_phone_digits,
    usable_phone_digits,
)


def test_name_normalization_ignores_spacing_and_case():
    assert normalize_hospital_name("  장편한 외과의원 ") == "장편한 외과의원"
    assert normalize_hospital_name("장편한\t외과의원") == "장편한 외과의원"
    assert normalize_hospital_name("Jang Clinic") == "jang clinic"
    assert normalize_hospital_name(None) == ""


def test_phone_normalization_compares_digits_only():
    assert normalize_phone_digits("02-123-4567") == "021234567"
    assert normalize_phone_digits("+82 2 123 4567") == "8221234567"
    assert normalize_phone_digits(None) == ""


def test_only_values_that_can_be_a_phone_number_are_matched():
    assert usable_phone_digits("02-123-4567") == "021234567"
    assert usable_phone_digits("01011112222") == "01011112222"
    # 이메일이나 짧은 문자열로 다른 병원과 엮이면 안 된다.
    assert usable_phone_digits("owner@example.com") is None
    assert usable_phone_digits("123") is None
    assert usable_phone_digits("") is None
    assert usable_phone_digits(None) is None


def test_hospital_create_and_lead_conversion_share_one_matcher():
    """두 경로가 서로 다른 함수를 쓰면 한쪽에서 막은 병원이 다른 쪽에서 만들어진다."""
    from app.api.admin import hospitals as hospitals_api
    from app.api.admin import leads as leads_api
    from app.services import hospital_duplicates

    assert hospitals_api.find_duplicate_hospitals is hospital_duplicates.find_duplicate_hospitals
    assert leads_api.find_duplicate_hospitals is hospital_duplicates.find_duplicate_hospitals
    assert hospitals_api._normalized_hospital_name("장편한  외과") == normalize_hospital_name(
        "장편한  외과"
    )
