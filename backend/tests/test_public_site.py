from types import SimpleNamespace

from app.api.public.site import _is_public_safe_content, _serialize_hospital
from app.models.content import ContentStatus
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, ESSENCE_STATUS_NEEDS_REVIEW
from app.services.site_builder import build_site


def test_serialize_hospital_includes_public_profile_fields():
    hospital = SimpleNamespace(
        id="hospital-id",
        name="테스트병원",
        slug="test-hospital",
        plan="PLAN_16",
        address="서울시 강남구",
        phone="02-123-4567",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://example.com",
        blog_url=None,
        kakao_channel_url="https://pf.kakao.com/_example",
        google_business_profile_url="https://business.google.com/example",
        google_maps_url="https://maps.google.com/?cid=123",
        naver_place_url="https://naver.me/example",
        aeo_domain="info.example.com",
        latitude=37.5,
        longitude=127.0,
        wikidata_qid="Q123456",
        gbp_place_id="ChIJxxxxxxxxxxxx",
        naver_place_id="38758880",
        kakao_place_id="26678017",
        hira_org_id="11000000",
        region=["서울", "강남"],
        specialties=["피부과"],
        keywords=["여드름", "리프팅"],
        director_name="홍길동",
        director_career="전문의",
        director_philosophy="근거 중심 진료",
        director_photo_url=None,
        director_credentials={
            "medical_school": "서울대학교 의과대학",
            "board_certifications": ["피부과 전문의"],
            "society_memberships": ["대한피부과학회"],
            "license_number": "12345",
        },
        treatments=[{"name": "리프팅", "description": "안면 리프팅"}],
    )

    serialized = _serialize_hospital(hospital)

    assert serialized["plan"] == "PLAN_16"
    assert serialized["keywords"] == ["여드름", "리프팅"]
    assert serialized["director_career"] == "전문의"
    assert serialized["director_philosophy"] is None
    assert serialized["google_maps_url"] == "https://maps.google.com/?cid=123"
    assert serialized["latitude"] == 37.5
    assert serialized["wikidata_qid"] == "Q123456"
    assert serialized["naver_place_id"] == "38758880"
    # license_number는 내부 보관 전용 — 공개 응답에서 제거됨.
    assert "license_number" not in serialized["director_credentials"]
    assert serialized["director_credentials"]["medical_school"] == "서울대학교 의과대학"


def test_legacy_site_builder_does_not_publish_director_philosophy(tmp_path, monkeypatch):
    legacy_note = "레거시 진료 철학은 승인된 콘텐츠 운영 기준이 아닙니다"
    hospital = SimpleNamespace(
        id="hospital-id",
        name="테스트병원",
        slug="test-hospital",
        address="서울시 강남구",
        phone="02-123-4567",
        business_hours={"mon": "09:00-18:00"},
        region=["서울", "강남"],
        specialties=["피부과"],
        director_name="홍길동",
        director_career="전문의",
        director_philosophy=legacy_note,
        treatments=[{"name": "리프팅", "description": "안면 리프팅"}],
    )

    monkeypatch.setattr("app.services.site_builder.SITE_BUILD_DIR", tmp_path)

    build_path = build_site(hospital, "info.example.com")

    assert legacy_note not in (tmp_path / "test-hospital" / "index.html").read_text(encoding="utf-8")
    assert legacy_note not in (tmp_path / "test-hospital" / "director" / "index.html").read_text(encoding="utf-8")
    assert legacy_note not in (tmp_path / "test-hospital" / "llms.txt").read_text(encoding="utf-8")
    assert build_path == str(tmp_path / "test-hospital")


def test_public_content_policy_requires_published_and_essence_aligned():
    aligned = SimpleNamespace(status=ContentStatus.PUBLISHED, essence_status=ESSENCE_STATUS_ALIGNED)
    draft = SimpleNamespace(status=ContentStatus.DRAFT, essence_status=ESSENCE_STATUS_ALIGNED)
    needs_review = SimpleNamespace(status=ContentStatus.PUBLISHED, essence_status=ESSENCE_STATUS_NEEDS_REVIEW)
    legacy_without_screening = SimpleNamespace(status=ContentStatus.PUBLISHED, essence_status=None)

    assert _is_public_safe_content(aligned) is True
    assert _is_public_safe_content(draft) is False
    assert _is_public_safe_content(needs_review) is False
    assert _is_public_safe_content(legacy_without_screening) is False
