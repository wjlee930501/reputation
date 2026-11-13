"""레이아웃 고르는 화면의 '같은 시·도 공개 병원 분포' 계산."""

import uuid

from app.models.hospital import Hospital, HospitalStatus
from app.services.site_layout import layout_distribution, province_of


def _public(**overrides) -> Hospital:
    values = {
        "id": uuid.uuid4(),
        "name": "장보고연합내과의원",
        "slug": f"h-{uuid.uuid4().hex[:8]}",
        "status": HospitalStatus.ACTIVE,
        "site_live": True,
        "site_built": True,
        "address": "대구광역시 수성구 달구벌대로 3218",
        "phone": "053-791-7585",
        "region": ["대구 수성구"],
        "specialties": ["내과"],
        "treatments": [{"name": "위내시경", "description": "식도·위를 관찰합니다."}],
        "site_layout": None,
    }
    values.update(overrides)
    hospital = Hospital()
    for key, value in values.items():
        setattr(hospital, key, value)
    return hospital


def test_province_comes_from_the_first_address_token():
    assert province_of("대구광역시 수성구 달구벌대로 3218") == "대구"
    assert province_of("경상북도 경산시 백자로 77") == "경북"
    assert province_of("서울 마포구 큰우물로 76") == "서울"
    assert province_of("세종특별자치시 한누리대로 273") == "세종"


def test_province_falls_back_to_region_only_without_an_address():
    assert province_of(None, ["경산, 사동, 대구"]) == "대구"
    assert province_of("", ["수성구"]) is None
    assert province_of(None, None) is None


def test_distribution_counts_public_hospitals_in_the_same_province():
    rows = [
        _public(site_layout="A"),
        _public(site_layout="A"),
        _public(site_layout="C"),
        _public(),  # 아직 고르지 않은 병원
        _public(site_layout="B", address="경상북도 경산시 백자로 77"),  # 다른 시·도
        _public(site_layout="B", site_live=False),  # 공개 전
    ]

    result = layout_distribution(rows, province="대구").as_dict()

    assert result == {
        "province": "대구",
        "counts": {"A": 2, "B": 0, "C": 1, "unset": 1},
        "total": 4,
    }


def test_distribution_excludes_the_hospital_being_edited():
    me = _public(site_layout="A")
    other = _public(site_layout="A")

    result = layout_distribution([me, other], province="대구", exclude_hospital_id=me.id)

    assert result.counts["A"] == 1 and result.total == 1


def test_unknown_province_reports_nothing_rather_than_national_counts():
    result = layout_distribution([_public(site_layout="A")], province=None)

    assert result.province is None and result.total == 0
    assert set(result.counts.values()) == {0}
