"""의료진 행 → 병원 단위 director_* 파생 규칙."""

import pytest

from app.services.hospital_physicians import (
    PhysicianNameRequired,
    derive_director_fields,
    normalized_physicians,
)


def _physician(**overrides):
    base = {
        "name": "김성열",
        "title": "대표원장",
        "career": None,
        "credentials": None,
        "display_order": 0,
        "is_representative": False,
    }
    base.update(overrides)
    return base


def test_blank_names_are_dropped_and_the_first_row_becomes_representative():
    result = normalized_physicians(
        [_physician(name="  "), _physician(name="김성열"), _physician(name="전상훈")]
    )

    assert [item["name"] for item in result] == ["김성열", "전상훈"]
    assert [item["is_representative"] for item in result] == [True, False]


def test_explicit_representative_is_preserved():
    result = normalized_physicians(
        [_physician(name="김성열"), _physician(name="전상훈", is_representative=True)]
    )

    assert [item["is_representative"] for item in result] == [False, True]


def test_list_with_only_blank_names_is_rejected():
    with pytest.raises(PhysicianNameRequired):
        normalized_physicians([_physician(name=""), _physician(name="   ")])


def test_empty_list_is_allowed_and_derives_nothing():
    assert normalized_physicians([]) == []
    # 의료진을 비우는 것과 원장 이름을 지우는 것은 다른 결정이다 — 조용히 비우지 않는다.
    assert derive_director_fields([]) == {}


def test_single_representative_syncs_its_own_career_and_credentials():
    credentials = {"medical_school": "서울대학교 의과대학"}
    fields = derive_director_fields(
        [
            _physician(
                name="김성열",
                title="대표원장",
                career="외과 전문의",
                credentials=credentials,
                is_representative=True,
            ),
            _physician(name="박과장", title="과장", career="내과 전문의", display_order=1),
        ]
    )

    assert fields["director_name"] == "김성열"
    assert fields["director_career"] == "외과 전문의"
    assert fields["director_credentials"] == credentials


def test_co_directors_join_names_and_label_each_career_block():
    fields = derive_director_fields(
        [
            _physician(
                name="전상훈",
                title="대표원장",
                career="흉부외과 전문의",
                display_order=1,
                is_representative=True,
                credentials={"medical_school": "연세대학교 의과대학"},
            ),
            _physician(
                name="김성열",
                title="대표원장",
                career="외과 전문의",
                display_order=0,
                is_representative=True,
                credentials={"medical_school": "서울대학교 의과대학"},
            ),
        ]
    )

    # display_order 순서로 묶는다 — 입력 순서가 아니라 화면이 정한 순서다.
    assert fields["director_name"] == "김성열 · 전상훈"
    assert fields["director_career"] == (
        "[대표원장 김성열] 외과 전문의\n[대표원장 전상훈] 흉부외과 전문의"
    )
    assert fields["director_credentials"] == {"medical_school": "서울대학교 의과대학"}


def test_co_directors_without_any_career_leave_the_stored_career_untouched():
    """파생값이 비면 그 컬럼은 아예 내보내지 않는다 — 빈 입력이 기존 값을 지우면 안 된다."""
    fields = derive_director_fields(
        [
            _physician(name="김성열", is_representative=True),
            _physician(name="전상훈", is_representative=True, display_order=1),
        ]
    )

    assert fields["director_name"] == "김성열 · 전상훈"
    assert "director_career" not in fields
    assert "director_credentials" not in fields


def test_single_representative_without_career_or_credentials_clears_nothing():
    fields = derive_director_fields(
        [_physician(name="김성열", career=None, credentials=None, is_representative=True)]
    )

    assert fields == {"director_name": "김성열"}


def test_career_block_without_title_uses_the_name_only():
    fields = derive_director_fields(
        [
            _physician(name="김성열", title=None, career="외과 전문의", is_representative=True),
            _physician(
                name="전상훈", title=None, career="흉부외과 전문의", is_representative=True,
                display_order=1,
            ),
        ]
    )

    assert fields["director_career"] == "[김성열] 외과 전문의\n[전상훈] 흉부외과 전문의"


def test_joined_name_is_truncated_to_the_column_length():
    physicians = [
        _physician(name=f"원장{index:02d}" * 5, is_representative=True, display_order=index)
        for index in range(10)
    ]

    assert len(derive_director_fields(physicians)["director_name"]) == 100
