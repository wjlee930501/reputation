from types import SimpleNamespace

from app.api.public.site import _serialize_hospital


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
        aeo_domain="info.example.com",
        region=["서울", "강남"],
        specialties=["피부과"],
        keywords=["여드름", "리프팅"],
        director_name="홍길동",
        director_career="전문의",
        director_philosophy="근거 중심 진료",
        director_photo_url=None,
        treatments=[{"name": "리프팅", "description": "안면 리프팅"}],
    )

    serialized = _serialize_hospital(hospital)

    assert serialized["plan"] == "PLAN_16"
    assert serialized["keywords"] == ["여드름", "리프팅"]
    assert serialized["director_career"] == "전문의"
