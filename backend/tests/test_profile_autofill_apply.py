"""자동 입력 적용 계획(P4-6, D30) — 빈 칸만, 마케터 칸은 절대 덮지 않고, 공개 텍스트는 의료광고 필터."""

from types import SimpleNamespace

import pytest

from app.services import profile_autofill_apply as apply_mod


def _hospital(**overrides):
    values = dict(
        profile_field_provenance={},
        director_name=None, director_career=None, director_philosophy=None,
        address=None, phone=None, business_hours=None,
        website_url=None, blog_url=None, kakao_channel_url=None,
        naver_place_url=None, naver_place_id=None,
        region=[], specialties=[], keywords=[], competitors=[], treatments=[],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _summary(draft, *, identity="VERIFIED_SEARCH", meta=None):
    field_meta = {key: {"source": "naver", "verified": True} for key in draft}
    field_meta.update(meta or {})
    return {"draft": draft, "field_meta": field_meta, "naver_identity": identity}


def test_only_empty_fields_are_filled_and_differences_become_conflicts() -> None:
    hospital = _hospital(phone="02-000-0000")
    plan = apply_mod.plan_autofill(
        hospital,
        _summary({"phone": "02-999-9999", "address": "서울 성동구 왕십리로 1"}),
        has_physicians=False,
    )
    assert plan.updates == {"address": "서울 성동구 왕십리로 1"}
    assert plan.skipped_filled_fields == ["phone"]
    assert plan.profile_conflicts == [{"field": "phone", "source": "naver", "value": "02-999-9999"}]


def test_operator_fields_are_never_filled_even_when_empty() -> None:
    hospital = _hospital(
        profile_field_provenance={"business_hours": {"origin": "OPERATOR"}},
    )
    plan = apply_mod.plan_autofill(
        hospital, _summary({"business_hours": {"mon": "10:00-19:00"}}), has_physicians=False
    )
    assert plan.updates == {}
    assert plan.skipped_operator_fields == ["business_hours"]
    # 마케터가 비운 칸이라도 네이버 값과의 차이는 충돌로만 남는다.
    assert plan.profile_conflicts[0]["field"] == "business_hours"


def test_medical_ad_filter_drops_fields_items_and_descriptions() -> None:
    plan = apply_mod.plan_autofill(
        _hospital(),
        _summary(
            {
                "director_philosophy": "국내 최초 완치 보장",
                "keywords": ["치질", "최고 치질"],
                "treatments": [
                    {"name": "치질 수술", "description": "부작용 없는 시술"},
                    {"name": "100% 완치 클리닉"},
                ],
            }
        ),
        has_physicians=False,
    )
    assert "director_philosophy" not in plan.updates
    assert plan.updates["keywords"] == ["치질"]
    assert plan.updates["treatments"] == [{"name": "치질 수술", "description": None}]
    reasons = {(item["field"], item["reason"]) for item in plan.dropped_fields}
    assert ("director_philosophy", "MEDICAL_AD_FILTER") in reasons
    assert ("keywords", "MEDICAL_AD_FILTER") in reasons
    assert ("treatments.description", "MEDICAL_AD_FILTER") in reasons
    assert ("treatments", "MEDICAL_AD_FILTER") in reasons


def test_region_mixed_keywords_are_left_out() -> None:
    plan = apply_mod.plan_autofill(
        _hospital(region=["강남구"]),
        _summary({"keywords": ["강남구 치질", "치루"]}),
        has_physicians=False,
    )
    assert plan.updates["keywords"] == ["치루"]
    assert {"field": "keywords", "item": "강남구 치질", "reason": "REGION_KEYWORD_MIXED"} in (
        plan.dropped_fields
    )


def test_unverified_naver_values_are_draft_only() -> None:
    plan = apply_mod.plan_autofill(
        _hospital(),
        _summary(
            {"address": "서울 성동구", "website_url": "https://clinic.example"},
            identity="UNVERIFIED",
            meta={
                "address": {"source": "naver", "verified": False},
                "website_url": {"source": "homepage", "verified": True},
            },
        ),
        has_physicians=False,
    )
    assert plan.updates == {"website_url": "https://clinic.example"}
    assert plan.skipped_unverified_fields == ["address"]


@pytest.mark.parametrize(
    ("stored_id", "expected"),
    [(None, {}), ("1595672233", {"address": "서울 성동구"})],
)
def test_legacy_drafts_trust_naver_only_when_the_stored_place_id_matches(stored_id, expected) -> None:
    summary = {
        "draft": {"address": "서울 성동구", "naver_place_id": "1595672233"},
        "field_meta": {"address": {"source": "naver", "confidence": 0.9}},
    }
    plan = apply_mod.plan_autofill(
        _hospital(naver_place_id=stored_id), summary, has_physicians=False
    )
    assert {k: v for k, v in plan.updates.items() if k == "address"} == expected


def test_director_fields_wait_for_marketers_when_physicians_exist() -> None:
    draft = {"director_name": "김원장", "director_career": "외과 전문의"}
    with_rows = apply_mod.plan_autofill(_hospital(), _summary(draft), has_physicians=True)
    assert with_rows.updates == {}
    without_rows = apply_mod.plan_autofill(_hospital(), _summary(draft), has_physicians=False)
    assert without_rows.updates == draft
    assert without_rows.create_representative is True


def test_bad_urls_and_place_ids_are_dropped() -> None:
    plan = apply_mod.plan_autofill(
        _hospital(),
        _summary({"website_url": "clinic.example", "naver_place_id": "abc"}),
        has_physicians=False,
    )
    assert plan.updates == {}
    assert {item["reason"] for item in plan.dropped_fields} == {"INVALID_URL", "INVALID_VALUE"}


def test_business_hours_keep_only_known_days() -> None:
    plan = apply_mod.plan_autofill(
        _hospital(),
        _summary({"business_hours": {"mon": "09:00-18:00", "holiday": "휴진", "tue": " "}}),
        has_physicians=False,
    )
    assert plan.updates["business_hours"] == {"mon": "09:00-18:00"}
