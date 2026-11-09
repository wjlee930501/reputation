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


def test_monthly_identity_comes_from_the_manifest_and_never_writes_it():
    """측정표는 만든 뒤 고칠 수 없다(DB 트리거) — 만들 때 고정한 신원을 읽기만 한다."""
    frozen = tasks._judgment_identity(_hospital(), "장편한외과의원", None).material()
    provenance = {"measurement_protocol": {"x": 1}, "judgment_identity": frozen}
    manifest = SimpleNamespace(platform_provenance=provenance)

    # 측정 도중 병원 전화가 바뀌어도 그 달의 판정은 측정표에 고정한 신원을 쓴다.
    identity = tasks._frozen_manifest_identity(manifest, _hospital(phone="02-000-0000"))

    assert identity.material() == frozen
    assert manifest.platform_provenance is provenance and provenance["judgment_identity"] == frozen


def test_manifest_without_frozen_identity_uses_the_live_profile_without_writing():
    manifest = SimpleNamespace(platform_provenance={"measurement_protocol": {"x": 1}})

    identity = tasks._frozen_manifest_identity(manifest, _hospital())

    assert identity.phone == "031-8067-8114"
    assert "judgment_identity" not in manifest.platform_provenance


def test_new_manifests_store_the_identity_at_creation():
    from app.services import monthly_manifest

    session = SimpleNamespace(add=lambda _row: None, flush=lambda: None)
    spec = monthly_manifest.ManifestCellSpec(
        query_key="query:1",
        query_text="수원 치질 병원",
        platform="chatgpt",
        query_matrix_id=None,
        query_target_id=None,
        query_variant_id=None,
        query_intent="LOCAL",
    )
    identity = tasks._judgment_identity(_hospital(), "장편한외과의원", None).material()

    manifest = monthly_manifest.freeze_monthly_manifest(
        session,
        "00000000-0000-0000-0000-000000000001",
        2026,
        11,
        [spec],
        gemini_configured=True,
        judgment_identity=identity,
    )

    assert manifest.platform_provenance["judgment_identity"] == identity


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
