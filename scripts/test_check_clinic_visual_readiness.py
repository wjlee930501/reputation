from __future__ import annotations

from scripts.check_clinic_visual_readiness import (
    AUDITED_HOSPITALS,
    evaluate_all,
    evaluate_hospital,
    format_report,
    missing_audited_hospitals,
)

APPROVED = {
    "name": "장편한외과의원",
    "slug": "jangpyeonhanoegwayiweon",
    # 업로드된 자산 참조. 외부 주소는 공개 화면이 쓰지 못해 승인으로 치지 않는다(O-5).
    "logo_url": "gs://reputation-images/assets/abc/logo.png",
    "brand_primary_color": "#17365D",
    "hero_headline": "증상을 정확히 확인합니다",
    "hero_description": None,
    "site_access_mode": "specialist",
    "photo_count": 8,
}


def test_a_fully_approved_hospital_has_nothing_outstanding():
    result = evaluate_hospital(APPROVED)

    assert result.approved is True
    assert result.missing == ()


def test_an_untouched_hospital_names_every_outstanding_visual_field():
    result = evaluate_hospital({"name": "강심장내과의원", "slug": "gangsimjangnaegwayiweon"})

    assert result.missing == ("logo", "primary_color", "hero_copy", "access_mode")


def test_photos_never_decide_visual_approval():
    without_photos = evaluate_hospital({**APPROVED, "photo_count": 0})
    with_photos = evaluate_hospital({**APPROVED, "photo_count": 22})

    assert without_photos.approved is True
    assert with_photos.approved is True
    assert "photo" not in " ".join(without_photos.missing)


def test_a_malformed_brand_color_is_not_treated_as_approved():
    assert "primary_color" in evaluate_hospital({**APPROVED, "brand_primary_color": "navy"}).missing
    assert "primary_color" in evaluate_hospital({**APPROVED, "brand_primary_color": "#173"}).missing
    assert "primary_color" not in evaluate_hospital(
        {**APPROVED, "brand_primary_color": "#d6a72c"}
    ).missing


def test_an_unsupported_access_mode_is_not_treated_as_approved():
    assert "access_mode" in evaluate_hospital({**APPROVED, "site_access_mode": "walk-in"}).missing
    assert "access_mode" in evaluate_hospital({**APPROVED, "site_access_mode": ""}).missing


def test_either_hero_line_counts_as_approved_copy():
    headline_only = evaluate_hospital({**APPROVED, "hero_description": None})
    description_only = evaluate_hospital(
        {**APPROVED, "hero_headline": None, "hero_description": "방문 전에 확인하세요."}
    )
    neither = evaluate_hospital({**APPROVED, "hero_headline": "  ", "hero_description": ""})

    assert headline_only.approved is True
    assert description_only.approved is True
    assert "hero_copy" in neither.missing


def test_the_report_separates_approved_from_outstanding_hospitals():
    results = evaluate_all(
        [
            APPROVED,
            {"name": "노원탑365의원", "slug": "noweontab365yiweon", "photo_count": 22},
        ]
    )
    report = format_report(results)

    assert "[OK]   장편한외과의원" in report
    assert "[TODO] 노원탑365의원" in report
    assert "승인 완료 1곳 / 전체 2곳" in report


def test_audited_hospitals_missing_from_the_query_are_reported():
    results = evaluate_all([APPROVED])

    not_found = missing_audited_hospitals(results)

    assert "노원탑365의원" in not_found
    assert "장편한외과의원" not in not_found
    assert set(not_found) <= set(AUDITED_HOSPITALS)


def test_the_audited_set_covers_the_six_live_clinics_plus_nowon():
    assert len(AUDITED_HOSPITALS) == 6
    assert "노원탑365의원" in AUDITED_HOSPITALS


def test_an_external_logo_url_is_not_an_approved_logo():
    """저장은 됐지만 공개 화면이 그리지 못하는 주소 — 승인으로 세면 로고 없는 사이트가
    정상으로 보고된다(O-5)."""
    row = dict(APPROVED, logo_url="https://cdn.imweb.me/thumbnail/logo.png")

    result = evaluate_hospital(row)

    assert result.approved is False
    assert result.missing == ("logo",)
