"""F-1: 병원 중복 판정은 모든 생성 경로에서 같은 규칙이어야 한다."""

from types import SimpleNamespace

from app.services.hospital_duplicates import (
    matches_hospital_name,
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


def test_only_a_name_match_is_strong_enough_to_link_without_asking():
    """전화·도메인 일치는 후보 신호일 뿐이다 — 자동 연결은 이름이 같을 때만."""
    same_name = SimpleNamespace(name="장편한 외과의원", phone=None)
    other_name = SimpleNamespace(name="전혀 다른 의원", phone="02-123-4567")

    assert matches_hospital_name(same_name, "장편한외과의원") is True
    assert matches_hospital_name(other_name, "장편한외과의원") is False
    # 비교할 이름이 없으면 일치로 볼 근거도 없다.
    assert matches_hospital_name(same_name, None) is False
    assert matches_hospital_name(same_name, "   ") is False
    assert matches_hospital_name(SimpleNamespace(name=None), "장편한외과의원") is False


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
