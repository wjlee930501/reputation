"""무료 진단 1회 제한의 신원 정규화·해시 (설계 T-6 일부).

이 테스트가 고정하는 것은 "표기를 바꿔 재신청을 통과시킬 수 없다"이다.
`assert normalize_phone("02-1") == "021"` 같은 자기참조가 아니라, **서로 다른 입력이
같은 잠금 키가 되는지**를 본다 — 그것이 실제 제약이기 때문이다.
"""
from datetime import date, datetime

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


class TestSlotDayBoundary:
    """자리 날짜 경계 — 08:00 KST.

    자정이 아니라 아침인 이유는 병원이 문을 여는 시간에 자리가 열려야 하기 때문이다.
    자정 리셋은 새벽에 자리가 소진되어, 원장님이 출근해 들어오면 이미 마감된 상태를 만든다.
    """

    def _at(self, y, m, d, hh, mm=0):
        from zoneinfo import ZoneInfo

        return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Seoul"))

    def test_just_before_reset_still_belongs_to_yesterday(self):
        from app.api.public.diagnosis import _slot_day

        assert _slot_day(self._at(2026, 7, 31, 7, 59)) == date(2026, 7, 30)

    def test_at_reset_the_new_day_opens(self):
        from app.api.public.diagnosis import _slot_day

        assert _slot_day(self._at(2026, 7, 31, 8, 0)) == date(2026, 7, 31)

    def test_midnight_is_no_longer_a_boundary(self):
        """자정 리셋이던 시절의 회귀 방지 — 00:00은 전날 자리여야 한다."""
        from app.api.public.diagnosis import _slot_day

        assert _slot_day(self._at(2026, 7, 31, 0, 0)) == date(2026, 7, 30)

    def test_a_full_slot_day_maps_to_one_date(self):
        """08:00 ~ 다음날 07:59가 하나의 자리 날짜다."""
        from app.api.public.diagnosis import _slot_day

        start = self._at(2026, 7, 31, 8, 0)
        end = self._at(2026, 8, 1, 7, 59)
        assert _slot_day(start) == _slot_day(end) == date(2026, 7, 31)

    def test_the_reset_hour_matches_what_the_landing_promises(self):
        """랜딩 각주가 안내하는 시각과 코드가 갈라지면 신청자가 마감 화면을 본다.

        문구는 site/lib/landing-copy.ts의 heroScarcity.note에 있고, 그쪽 테스트가
        '자정' 같은 다른 시각을 쓰지 못하게 막는다. 여기서는 코드 쪽 값을 고정한다.
        """
        from app.api.public.diagnosis import SLOT_RESET_HOUR_KST

        assert SLOT_RESET_HOUR_KST == 8
