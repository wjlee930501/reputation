"""Google 기본 경로가 끝났을 때의 OpenAI 폴백.

운영에서 Gemini 이미지 모델이 프롬프트를 IMAGE_SAFETY로 막거나, 만든 이미지가 독립 정책
검수에서 거절되는 일이 3일 내내 100%로 반복됐다(2026-09-12~14 worker 로그). 같은 검수
기준을 두고 생성기만 바꾸면 통과하는 후보가 나올 수 있으므로, Google이 안전 차단·정책
거절·공급자 오류로 끝난 뒤에만 OpenAI를 한 번 더 부른다.
"""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.content import ContentType
from app.services import image_engine
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    ImagePolicyUnavailableError,
)


@pytest.fixture
def google_primary_with_openai_fallback(monkeypatch):
    """비용 가드 통과, Google 기본, OpenAI 폴백 켜짐. 실제 호출 수는 image 계수로 본다."""

    recorded: dict[str, int] = {}

    async def allowed(category, **_kwargs):
        return SimpleNamespace(
            allowed=True, reason=None, receipt=SimpleNamespace(category=category)
        )

    async def settle(*_args, **_kwargs):
        return None

    async def record(category, *, count=1, **_kwargs):
        recorded[category] = recorded.get(category, 0) + count

    monkeypatch.setattr("app.services.cost_guard.reserve", allowed)
    monkeypatch.setattr("app.services.cost_guard.settle_reservation", settle)
    monkeypatch.setattr("app.services.cost_guard.record_provider_call", record)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "IMAGE_FALLBACK_PROVIDER", "openai")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    return recorded


def _google_safety_then_reject(assessment: ImagePolicyAssessment):
    calls = {"n": 0}

    def generate(_prompt, _hospital, *, expected_topic=None, counter=None):
        calls["n"] += 1
        if counter is not None:
            counter.tick()
        if calls["n"] == 1:
            raise image_engine.ImageSafetyBlockedError("IMAGE_SAFETY")
        raise ImagePolicyRejectedError(assessment)

    return generate, calls


_NOT_RELEVANT = ImagePolicyAssessment(
    has_text=False,
    has_logo=False,
    has_recognizable_people=False,
    impersonates_real_clinic=False,
    topic_relevant=False,
)


def test_openai_fallback_rescues_google_safety_block_and_policy_rejection(
    monkeypatch, google_primary_with_openai_fallback
):
    """운영에서 본 실패 순서 그대로(IMAGE_SAFETY → 폴백 후보 거절) 뒤 OpenAI가 성공한다."""

    google, google_calls = _google_safety_then_reject(_NOT_RELEVANT)
    openai_prompts = []

    def openai(prompt, _hospital, *, expected_topic=None, counter=None):
        openai_prompts.append((prompt, expected_topic))
        if counter is not None:
            counter.tick()
        return "gs://bucket/openai.png"

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)
    diagnostics: dict[str, object] = {}

    url, prompt = asyncio.run(
        image_engine.generate_image(
            ContentType.DISEASE, "병원", topic="손목 통증", diagnostics=diagnostics
        )
    )

    assert url == "gs://bucket/openai.png"
    assert google_calls["n"] == 2
    assert prompt == image_engine._build_openai_image_prompt(ContentType.DISEASE, "손목 통증")
    assert openai_prompts == [(prompt, "손목 통증")]
    assert diagnostics["fallback_provider"] == "openai"
    # Google 2회 + OpenAI 1회가 전부 실제 호출로 잡힌다.
    assert google_primary_with_openai_fallback["image"] == 3


def test_openai_fallback_failure_keeps_google_cause_and_records_raw_error(
    monkeypatch, google_primary_with_openai_fallback
):
    """폴백까지 실패하면 저장 분류는 Google 원인(POLICY_REJECTED)을 유지하고 OpenAI 원문만 덧붙인다."""

    google, _calls = _google_safety_then_reject(_NOT_RELEVANT)

    def openai(_prompt, _hospital, *, expected_topic=None, counter=None):
        for _ in range(3):  # tenacity가 소진한 시도 수
            if counter is not None:
                counter.tick()
        raise RuntimeError("Error code: 429 - insufficient_quota")

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)
    diagnostics: dict[str, object] = {}

    result = asyncio.run(
        image_engine.generate_image(
            ContentType.DISEASE, "병원", topic="손목 통증", diagnostics=diagnostics
        )
    )

    assert result == ("", "")
    assert diagnostics["reason"] == "POLICY_REJECTED"
    assert diagnostics["policy_rejection"]["stage"] == "GOOGLE_FALLBACK"
    assert diagnostics["policy_rejection"]["prior_failure"] == "IMAGE_SAFETY"
    assert diagnostics["openai_fallback_error"].startswith("RuntimeError: Error code: 429")
    assert "fallback_provider" not in diagnostics
    assert google_primary_with_openai_fallback["image"] == 5
    from app.workers.tasks import _image_failure_class

    # 분류는 reason이 앞선다 — 정책 거절 루프는 repair 프롬프트로 이어져야 한다.
    assert _image_failure_class(diagnostics) == "POLICY_REJECTED"


def test_openai_fallback_policy_rejection_is_recorded_with_google_prior_failure(
    monkeypatch, google_primary_with_openai_fallback
):
    google, _calls = _google_safety_then_reject(_NOT_RELEVANT)
    rejected = ImagePolicyAssessment(
        has_text=True,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )

    def openai(_prompt, _hospital, *, expected_topic=None, counter=None):
        if counter is not None:
            counter.tick()
        raise ImagePolicyRejectedError(rejected)

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)
    diagnostics: dict[str, object] = {}

    result = asyncio.run(
        image_engine.generate_image(
            ContentType.DISEASE, "병원", topic="손목 통증", diagnostics=diagnostics
        )
    )

    assert result == ("", "")
    assert diagnostics["policy_rejection"] == {
        "reason": "POLICY_REJECTED",
        "stage": "OPENAI_FALLBACK",
        "prompt_version": "openai-fallback-v1",
        "has_text": True,
        "has_logo": False,
        "has_recognizable_people": False,
        "impersonates_real_clinic": False,
        "topic_relevant": True,
        "prior_failure": "POLICY_REJECTED",
    }


def test_openai_fallback_is_skipped_when_the_reviewer_itself_is_unavailable(
    monkeypatch, google_primary_with_openai_fallback
):
    """검수 모델이 죽었으면 OpenAI 후보도 같은 검수에서 버려진다 — 유료 호출을 더 만들지 않는다."""

    def google(_prompt, _hospital, *, expected_topic=None, counter=None):
        if counter is not None:
            counter.tick()
        raise ImagePolicyUnavailableError("image policy review failed: 503")

    def openai(*_args, **_kwargs):
        raise AssertionError("OpenAI must not be called")

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)
    diagnostics: dict[str, object] = {}

    result = asyncio.run(
        image_engine.generate_image(
            ContentType.HEALTH, "병원", topic="여름 탈수", diagnostics=diagnostics
        )
    )

    assert result == ("", "")
    assert diagnostics["reason"] == "POLICY_UNAVAILABLE"
    assert google_primary_with_openai_fallback["image"] == 1


def test_openai_fallback_respects_the_off_switch(
    monkeypatch, google_primary_with_openai_fallback
):
    monkeypatch.setattr(image_engine.settings, "IMAGE_FALLBACK_PROVIDER", "")
    google, google_calls = _google_safety_then_reject(_NOT_RELEVANT)

    def openai(*_args, **_kwargs):
        raise AssertionError("OpenAI must not be called")

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)

    result = asyncio.run(
        image_engine.generate_image(ContentType.DISEASE, "병원", topic="손목 통증")
    )

    assert result == ("", "")
    assert google_calls["n"] == 2


def test_openai_fallback_also_runs_for_the_policy_repair_candidate(
    monkeypatch, google_primary_with_openai_fallback
):
    """저장된 거절 진단으로 만드는 repair 회차도 Google 1회 뒤 OpenAI 1회까지 간다."""

    prompts = []

    def google(prompt, _hospital, *, expected_topic=None, counter=None):
        prompts.append(("google", prompt))
        if counter is not None:
            counter.tick()
        raise image_engine.ImageSafetyBlockedError("IMAGE_SAFETY")

    def openai(prompt, _hospital, *, expected_topic=None, counter=None):
        prompts.append(("openai", prompt))
        if counter is not None:
            counter.tick()
        return "gs://bucket/repair.png"

    monkeypatch.setattr(image_engine, "_generate_and_upload", google)
    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", openai)

    url, prompt = asyncio.run(
        image_engine.generate_image(
            ContentType.TREATMENT,
            "병원",
            topic="무릎 재활",
            policy_repair=True,
            prior_policy_rejection={"topic_relevant": False},
        )
    )

    assert url == "gs://bucket/repair.png"
    assert [provider for provider, _ in prompts] == ["google", "openai"]
    assert "single dominant subject" in prompt
    assert google_primary_with_openai_fallback["image"] == 2


def test_openai_image_request_uses_the_configured_openrouter_model(monkeypatch):
    """생성은 OpenRouter /images 엔드포인트 하나로 나간다 — 모델 슬러그·비율·품질을 확인.

    비율은 공통 IMAGE_ASPECT_RATIO가 아니라 OPENAI_IMAGE_ASPECT_RATIO를 쓴다.
    gpt-5-image 계열이 받는 값은 1:1 / 3:2 / 2:3 / auto뿐이라, 기본 경로의 16:9를
    그대로 보내면 폴백이 400으로 죽는다.
    """

    captured = {}

    def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return [b"png"], {"data": [{"media_type": "image/png"}], "usage": None, "id": "r1"}

    monkeypatch.setattr(image_engine.openrouter, "generate_image", fake_generate_image)
    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(image_engine.settings, "OPENAI_IMAGE_MODEL", "openai/gpt-5-image-mini")
    monkeypatch.setattr(image_engine.settings, "IMAGE_ASPECT_RATIO", "16:9")
    monkeypatch.setattr(image_engine.settings, "OPENAI_IMAGE_ASPECT_RATIO", "3:2")
    monkeypatch.setattr(image_engine.settings, "OPENAI_IMAGE_QUALITY", "high")
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: ImagePolicyAssessment(
            has_text=False,
            has_logo=False,
            has_recognizable_people=False,
            impersonates_real_clinic=False,
            topic_relevant=True,
        ),
    )

    image_bytes = image_engine._openai_generate_verified_bytes("prompt", expected_topic="t")

    assert image_bytes == b"png"
    assert captured["model"] == "openai/gpt-5-image-mini"
    assert captured["aspect_ratio"] == "3:2"
    assert captured["quality"] == "high"


def test_openai_image_aspect_ratio_default_is_one_the_model_supports():
    """기본값이 지원 목록 밖이면 폴백은 "기본 경로가 실패했을 때만 항상 실패"가 된다."""

    from app.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.OPENAI_IMAGE_ASPECT_RATIO in {"1:1", "3:2", "2:3", "auto"}

    with pytest.raises(ValidationError, match="OPENAI_IMAGE_ASPECT_RATIO"):
        Settings(_env_file=None, OPENAI_IMAGE_ASPECT_RATIO="16:9")
