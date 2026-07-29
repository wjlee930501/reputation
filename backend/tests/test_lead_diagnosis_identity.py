"""무료 진단 1회 제한의 신원 정규화·해시 (설계 T-6 일부).

이 테스트가 고정하는 것은 "표기를 바꿔 재신청을 통과시킬 수 없다"이다.
`assert normalize_phone("02-1") == "021"` 같은 자기참조가 아니라, **서로 다른 입력이
같은 잠금 키가 되는지**를 본다 — 그것이 실제 제약이기 때문이다.
"""
import pytest

from app.services.lead_diagnosis_identity import (
    InvalidEmail,
    InvalidPhoneNumber,
    email_lock_hash,
    normalize_email,
    normalize_phone,
    phone_lock_hash,
)


class TestPhoneNormalization:
    @pytest.mark.parametrize(
        "variant",
        [
            "021234567",
            "02-123-4567",
            "02 123 4567",
            "(02) 123-4567",
            "+82 2-123-4567",
            "+82-2-123-4567",
            "0082 2 123 4567",
            "  02.123.4567  ",
        ],
    )
    def test_same_number_written_differently_yields_one_lock(self, variant):
        """표기만 바꾼 재신청은 같은 잠금에 걸려야 한다."""
        assert phone_lock_hash(variant) == phone_lock_hash("021234567")

    def test_different_numbers_do_not_collide(self):
        assert phone_lock_hash("021234567") != phone_lock_hash("021234568")

    def test_mobile_and_landline_stay_distinct(self):
        assert phone_lock_hash("01012345678") != phone_lock_hash("021234567")

    @pytest.mark.parametrize("bad", ["", "   ", "abc", "12345678", "-", "0" * 16])
    def test_unusable_numbers_are_rejected(self, bad):
        with pytest.raises(InvalidPhoneNumber):
            normalize_phone(bad)

    def test_leading_zero_after_country_code_is_not_doubled(self):
        """'+82 02-...'처럼 국가번호와 0을 같이 적어도 0이 두 번 붙지 않는다."""
        assert normalize_phone("+82 02-123-4567") == "021234567"


class TestEmailNormalization:
    @pytest.mark.parametrize(
        "variant",
        ["hong@example.com", "Hong@Example.COM", "  hong@example.com ", "hong+tag@example.com"],
    )
    def test_case_whitespace_and_plus_tag_share_one_lock(self, variant):
        assert email_lock_hash(variant) == email_lock_hash("hong@example.com")

    def test_different_local_parts_do_not_collide(self):
        assert email_lock_hash("hong@example.com") != email_lock_hash("hong2@example.com")

    def test_same_local_part_on_another_domain_is_a_different_person(self):
        assert email_lock_hash("hong@example.com") != email_lock_hash("hong@example.net")

    @pytest.mark.parametrize(
        "bad", ["", "hong", "hong@", "@example.com", "hong@@example.com", "hong@example", "+@a.com"]
    )
    def test_malformed_addresses_are_rejected(self, bad):
        with pytest.raises(InvalidEmail):
            normalize_email(bad)


class TestLockNamespacing:
    def test_phone_and_email_hashes_live_in_separate_namespaces(self):
        """같은 문자열이 두 잠금에서 같은 해시가 되면 한쪽 잠금이 다른 쪽을 막는다.

        전화번호와 이메일은 별개의 부분 유니크 인덱스를 쓰므로 충돌 자체가 드물지만,
        namespace가 빠지면 '전화번호로 이메일 잠금을 소진'시키는 우회가 생긴다.
        """
        shared = "010@1234.5678"
        assert phone_lock_hash("01012345678") != email_lock_hash(shared)

    def test_lock_hash_depends_on_the_pepper(self, monkeypatch):
        """pepper 없이 sha256만 쓰면 번호 공간이 좁아 전수 대입으로 역산된다.

        pepper를 바꿨을 때 해시가 그대로면 pepper가 실제로 섞이지 않고 있다는 뜻이다.
        """
        from app.core.config import settings

        before = phone_lock_hash("021234567")
        monkeypatch.setattr(settings, "LEAD_LOCK_HASH_PEPPER", "a-different-pepper")
        assert phone_lock_hash("021234567") != before
