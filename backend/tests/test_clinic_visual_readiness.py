"""목록·상세·점검 스크립트가 시각 승인에 대해 같은 답을 내는가.

판정이 화면마다 갈리면 운영자는 목록에서 `✓`를, 상세에서 `7/8 진행 필요`를 본다.
실제로 그런 상태였고, 목록만 보는 운영자에게는 5개 병원의 할 일이 아예 보이지
않았다(O-2).
"""

from types import SimpleNamespace

import pytest

from app.services.clinic_visual_readiness import evaluate_visual_readiness

APPROVED = {
    "logo_url": "gs://reputation-images/assets/abc/logo.png",
    "brand_primary_color": "#17365D",
    "hero_headline": "증상을 정확히 확인합니다",
    "hero_description": None,
    "site_access_mode": "specialist",
}


def test_a_fully_approved_profile_has_nothing_outstanding():
    result = evaluate_visual_readiness(APPROVED)

    assert result.approved is True
    assert result.missing == ()
    assert result.missing_labels == ()


def test_orm_objects_and_dicts_are_judged_the_same_way():
    # 목록 API는 ORM 객체를, 점검 경로는 dict를 넘긴다. 둘이 갈리면 안 된다.
    assert evaluate_visual_readiness(SimpleNamespace(**APPROVED)).missing == ()


def test_an_external_logo_url_is_stored_but_not_approved():
    """공개 표면이 그리지 못하는 주소를 승인으로 세면 로고 없는 사이트가 정상으로 보인다."""
    result = evaluate_visual_readiness({**APPROVED, "logo_url": "https://cdn.imweb.me/logo.png"})

    assert result.missing == ("logo",)
    assert result.missing_labels == ("공식 로고",)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("logo_url", None, "logo"),
        ("brand_primary_color", None, "primary_color"),
        ("brand_primary_color", "17365D", "primary_color"),
        ("site_access_mode", None, "access_mode"),
        ("site_access_mode", "unknown", "access_mode"),
    ],
)
def test_each_missing_field_is_named(field, value, expected):
    result = evaluate_visual_readiness({**APPROVED, field: value})

    assert expected in result.missing


def test_either_hero_line_counts_as_approved_copy():
    headline_only = evaluate_visual_readiness({**APPROVED, "hero_description": None})
    description_only = evaluate_visual_readiness(
        {**APPROVED, "hero_headline": None, "hero_description": "방문 전 진료시간을 확인하세요"}
    )
    neither = evaluate_visual_readiness(
        {**APPROVED, "hero_headline": None, "hero_description": "   "}
    )

    assert "hero_copy" not in headline_only.missing
    assert "hero_copy" not in description_only.missing
    assert "hero_copy" in neither.missing


def test_photos_never_gate_visual_approval():
    """실사진이 없는 병원도 정상 운영 대상이다 — 필수로 만들면 공개가 막힌다."""
    result = evaluate_visual_readiness({**APPROVED, "photo_count": 0})

    assert result.approved is True
