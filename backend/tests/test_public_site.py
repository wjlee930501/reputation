from datetime import date, datetime
from types import SimpleNamespace

from app.api.public.site import (
    _is_public_safe_content,
    _reading_minutes,
    _serialize_hospital,
    _serialize_hospital_summary,
    _serialize_item,
    _vetted_public_about,
)
from app.models.content import ContentStatus
from app.models.essence import PhilosophyStatus, SourceType
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

    # 요금제는 내부 계약 정보 — 공개 응답에 포함하지 않는다.
    assert "plan" not in serialized
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


def test_serialize_hospital_summary_exposes_llms_index_fields_only():
    hospital = SimpleNamespace(
        slug="test-hospital",
        name="테스트병원",
        aeo_domain="info.example.com",
        region=["서울", "강남"],
        specialties=["피부과"],
        director_name="홍길동",
        address="서울시 강남구",
        phone="02-123-4567",
        website_url="https://example.com",
        plan="PLAN_16",
        director_philosophy="비공개 메모",
        updated_at=datetime(2026, 6, 1, 12, 0, 0),
        created_at=datetime(2026, 5, 1, 12, 0, 0),
    )

    summary = _serialize_hospital_summary(hospital)

    assert summary["slug"] == "test-hospital"
    assert summary["name"] == "테스트병원"
    assert summary["region"] == ["서울", "강남"]
    assert summary["specialties"] == ["피부과"]
    assert summary["director_name"] == "홍길동"
    assert summary["address"] == "서울시 강남구"
    assert summary["phone"] == "02-123-4567"
    assert summary["website_url"] == "https://example.com"
    assert summary["updated_at"] == "2026-06-01T12:00:00"
    # 내부 전용 필드는 목록에도 노출하지 않는다.
    assert "plan" not in summary
    assert "director_philosophy" not in summary


def test_serialize_hospital_summary_sanitizes_website_url():
    hospital = SimpleNamespace(
        slug="test-hospital",
        name="테스트병원",
        aeo_domain=None,
        region=[],
        specialties=[],
        director_name=None,
        address=None,
        phone=None,
        website_url="javascript:alert(1)",
        updated_at=None,
        created_at=None,
    )

    summary = _serialize_hospital_summary(hospital)

    assert summary["website_url"] is None
    assert summary["updated_at"] is None


def _hospital_with_photo(director_photo_url):
    return SimpleNamespace(
        id="hospital-id",
        name="테스트병원",
        slug="test-hospital",
        address=None,
        phone=None,
        business_hours=None,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
        aeo_domain=None,
        latitude=None,
        longitude=None,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        region=[],
        specialties=[],
        keywords=[],
        director_name="홍길동",
        director_career=None,
        director_philosophy=None,
        director_photo_url=director_photo_url,
        director_credentials=None,
        treatments=[],
    )


def _doctor_photo_asset():
    return SimpleNamespace(
        id="asset-id",
        source_type=SourceType.PHOTO_DOCTOR,
        title="원장 사진",
        file_url="gs://bucket/doctor.png",
    )


def test_serialize_hospital_invalid_director_photo_falls_back_to_approved_asset():
    """비 http(s) director_photo_url은 sanitize되고, 승인된 PHOTO_DOCTOR 자산으로 폴백한다."""
    serialized = _serialize_hospital(
        _hospital_with_photo("local://internal/path.png"), [_doctor_photo_asset()]
    )

    assert serialized["director_photo_url"] == (
        "/api/v1/public/hospitals/test-hospital/assets/asset-id"
    )


def test_serialize_hospital_invalid_director_photo_without_asset_is_null():
    serialized = _serialize_hospital(_hospital_with_photo("javascript:alert(1)"), [])

    assert serialized["director_photo_url"] is None


def test_serialize_hospital_valid_director_photo_takes_priority_over_asset():
    serialized = _serialize_hospital(
        _hospital_with_photo("https://cdn.example.com/doctor.png"), [_doctor_photo_asset()]
    )

    assert serialized["director_photo_url"] == "https://cdn.example.com/doctor.png"


def test_serialize_item_list_response_includes_reading_minutes_without_body():
    item = SimpleNamespace(
        id="content-id",
        content_type="FAQ",
        title="무릎이 아플 때",
        meta_description="요약",
        image_url=None,
        scheduled_date=date(2026, 6, 1),
        published_at=datetime(2026, 6, 1, 8, 0, 0),
        body_updated_at=None,
        references_list=[],
        faq_question=None,
        faq_answer_summary=None,
        body="가" * 1200,
    )

    serialized = _serialize_item(item, "test-slug")

    assert serialized["reading_minutes"] == 2
    assert "body" not in serialized

    full = _serialize_item(item, "test-slug", full=True)
    assert full["body"] == "가" * 1200
    assert full["reading_minutes"] == 2


def test_serialize_item_uses_stable_content_image_proxy_url():
    # 콘텐츠 대표 이미지는 만료되는 signed GCS URL이 아니라 안정 프록시 경로로 노출해야
    # SSG/CDN 캐시 HTML이 만료 URL을 박아 이미지가 깨지는 일을 막는다.
    item = SimpleNamespace(
        id="abc-123",
        content_type="FAQ",
        title="t",
        meta_description="m",
        image_url="gs://reputation-images/content/x/y.png",
        scheduled_date=date(2026, 6, 1),
        published_at=datetime(2026, 6, 1, 8, 0, 0),
        body_updated_at=None,
        references_list=[],
        faq_question=None,
        faq_answer_summary=None,
        body="가" * 100,
    )
    serialized = _serialize_item(item, "jangpyeonhanoegwayiweon")
    assert (
        serialized["image_url"]
        == "/api/v1/public/hospitals/jangpyeonhanoegwayiweon/contents/abc-123/image"
    )
    assert "storage.googleapis.com" not in (serialized["image_url"] or "")


def test_serialize_item_passes_through_non_gcs_image_url():
    # gs:// 가 아닌 이미 사용 가능한 URL(레거시 상대 public asset 경로/http)은 /contents/{id}/image
    # 프록시로 감싸면 _asset_response가 처리 못 해 404 → 그대로 통과시켜야 한다.
    def _item(image_url):
        return SimpleNamespace(
            id="abc-123", content_type="DISEASE", title="t", meta_description="m",
            image_url=image_url, scheduled_date=date(2026, 6, 1),
            published_at=datetime(2026, 6, 1, 8, 0, 0), body_updated_at=None,
            references_list=[], faq_question=None, faq_answer_summary=None, body="가" * 50,
        )
    legacy = "/api/v1/public/hospitals/jangpyeonhanoegwayiweon/assets/asset-1"
    assert _serialize_item(_item(legacy), "jangpyeonhanoegwayiweon")["image_url"] == legacy
    absolute = "https://cdn.example.com/x.png"
    assert _serialize_item(_item(absolute), "jangpyeonhanoegwayiweon")["image_url"] == absolute
    assert _serialize_item(_item(None), "jangpyeonhanoegwayiweon")["image_url"] is None


def test_reading_minutes_handles_empty_and_markdown_noise():
    assert _reading_minutes(None) == 1
    assert _reading_minutes("") == 1
    assert _reading_minutes("## 제목\n- 항목\nhttps://example.com/path") == 1
    assert _reading_minutes("가" * 1800) == 3


def _approved_philosophy(positioning_statement, patient_promise=None):
    return SimpleNamespace(
        status=PhilosophyStatus.APPROVED,
        positioning_statement=positioning_statement,
        patient_promise=patient_promise,
    )


def test_vetted_public_about_none_without_philosophy():
    assert _vetted_public_about(None) is None


def test_vetted_public_about_ignores_non_approved_status():
    draft = SimpleNamespace(
        status=PhilosophyStatus.DRAFT,
        positioning_statement="근거 중심으로 충분히 설명하는 진료를 지향합니다.",
        patient_promise=None,
    )
    assert _vetted_public_about(draft) is None


def test_vetted_public_about_joins_positioning_and_promise():
    philosophy = _approved_philosophy(
        "근거 중심으로 충분히 설명하는 진료를 지향합니다.",
        "확인된 정보만 환자에게 안내합니다.",
    )
    assert _vetted_public_about(philosophy) == (
        "근거 중심으로 충분히 설명하는 진료를 지향합니다. 확인된 정보만 환자에게 안내합니다."
    )


def test_vetted_public_about_drops_medical_ad_violating_sentence():
    # positioning_statement에 금지 표현("완치")이 섞이면 그 단편은 통째로 폐기되고,
    # 검수를 통과한 patient_promise만 남는다.
    philosophy = _approved_philosophy(
        "저희는 100% 완치를 보장하는 국내 유일의 병원입니다.",
        "확인된 정보만 환자에게 안내합니다.",
    )
    assert _vetted_public_about(philosophy) == "확인된 정보만 환자에게 안내합니다."


def test_vetted_public_about_none_when_all_sentences_violate():
    philosophy = _approved_philosophy("국내 최초이자 유일한 완치 보장 병원", "부작용 없는 시술")
    assert _vetted_public_about(philosophy) is None


def test_serialize_hospital_exposes_public_about_only_when_approved_and_filtered():
    hospital = _hospital_with_photo(None)

    # 승인된 운영 기준이 없으면 public_about는 None (필드는 존재하되 값 없음).
    no_philosophy = _serialize_hospital(hospital, [])
    assert no_philosophy["public_about"] is None
    # 자유 입력 director_philosophy는 여전히 비공개.
    assert no_philosophy["director_philosophy"] is None

    # 승인 + 검수 통과 시에만 값이 채워진다.
    approved = _serialize_hospital(
        hospital,
        [],
        _approved_philosophy("근거 중심으로 충분히 설명하는 진료를 지향합니다."),
    )
    assert approved["public_about"] == "근거 중심으로 충분히 설명하는 진료를 지향합니다."
