"""병원 기본 정보 완료 게이트의 보강 항목 플래그(기본 꺼짐)."""

from types import SimpleNamespace

from app.core.config import settings
from app.services.hospital_lifecycle import missing_profile_requirement_keys


def _hospital(**overrides):
    values = dict(
        director_name="김원장", director_career=None, director_philosophy=None,
        address="서울", phone="02-1", business_hours={"mon": "09:00-18:00"},
        website_url="https://a.example", blog_url=None,
        naver_place_url="https://naver.me/x", google_maps_url=None,
        google_business_profile_url=None, latitude=37.5, longitude=127.0,
        region=["강남"], specialties=["외과"], keywords=["치질"],
        treatments=[{"name": "치질 수술"}],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_default_gate_still_requires_career_philosophy_and_google(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROFILE_GATE_OPTIONAL_ENRICHMENT", False)
    assert set(missing_profile_requirement_keys(_hospital())) == {
        "director_basic", "director_philosophy", "ai_channels",
    }


def test_optional_enrichment_flag_drops_only_those_three(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROFILE_GATE_OPTIONAL_ENRICHMENT", True)
    assert missing_profile_requirement_keys(_hospital()) == []
    # 원장명·네이버 플레이스는 여전히 필수다.
    assert set(
        missing_profile_requirement_keys(_hospital(director_name=None, naver_place_url=None))
    ) == {"director_basic", "ai_channels"}
