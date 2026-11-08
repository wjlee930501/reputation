"""판정 신원 고정과 판정 정책이 바뀐 달의 경쟁 병원 반영 차단."""

from types import SimpleNamespace

from app.services import competitor_rejudge, sov_engine
from app.services.v0_measurement_snapshot import parse_v0_judgment_context, v0_judgment_context
from app.workers import tasks


def _hospital(phone="031-8067-8114"):
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        name="장편한외과의원",
        address="경기 수원시 팔달구 중부대로 247",
        phone=phone,
        region=["수원시 팔달구"],
        competitors=["경쟁병원"],
    )


def test_monthly_identity_is_frozen_on_first_use_and_reused_after_profile_edits():
    manifest = SimpleNamespace(platform_provenance={"measurement_protocol": {"x": 1}})

    first = tasks._frozen_manifest_identity(manifest, _hospital())
    # 측정 도중 병원 전화가 바뀌어도 그 달의 판정은 처음 고정한 신원을 쓴다.
    second = tasks._frozen_manifest_identity(manifest, _hospital(phone="02-000-0000"))

    assert manifest.platform_provenance["measurement_protocol"] == {"x": 1}
    assert manifest.platform_provenance["judgment_identity"]["phone"] == "03180678114"
    assert first.material() == second.material()


def test_v0_context_freezes_identity_and_parses_it_back():
    hospital = _hospital()
    context = v0_judgment_context(hospital)

    parsed = parse_v0_judgment_context(context, hospital_id=hospital.id)

    assert parsed["identity"] == context["identity"]
    assert parsed["identity"]["address"] == hospital.address
    # 신원이 없는 옛 문맥도 읽힌다(그런 실행은 측정 기준 변경 검사가 막는다).
    legacy = {key: value for key, value in context.items() if key != "identity"}
    assert "identity" not in parse_v0_judgment_context(legacy, hospital_id=hospital.id)


def _manifest(judge_fingerprint):
    return SimpleNamespace(
        platform_provenance={"measurement_protocol": {"judge_prompt_fingerprint": judge_fingerprint}}
    )


def test_competitor_refresh_is_only_for_months_judged_with_the_current_policy():
    current = sov_engine.measurement_protocol()["judge_prompt_fingerprint"]

    assert competitor_rejudge.month_judge_policy_is_current(_manifest(current))
    assert not competitor_rejudge.month_judge_policy_is_current(_manifest("old-gpt-4o-mini-prompt"))
    assert not competitor_rejudge.month_judge_policy_is_current(None)
