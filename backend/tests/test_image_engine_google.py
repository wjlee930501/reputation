from types import SimpleNamespace

import httpx
import pytest
import tenacity

from app.models.content import ContentType
from app.services import image_engine, openrouter
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    ImagePolicyUnavailableError,
)


def _allow_cost(monkeypatch):
    """비용 가드를 통과시키되 정산 호출은 그대로 받아 준다 (Redis 없이 돈다)."""

    from app.services import cost_guard

    async def _reserve(_category):
        return SimpleNamespace(allowed=True, receipt=None, reason=None)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cost_guard, "reserve", _reserve)
    monkeypatch.setattr(cost_guard, "settle_reservation", _noop)
    monkeypatch.setattr(cost_guard, "record_provider_call", _noop)


def _ok_assessment():
    return ImagePolicyAssessment(
        has_text=False,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )


def test_google_image_generation_uses_openrouter_images_endpoint_and_uploads_payload(
    monkeypatch,
):
    """생성은 OpenRouter /images로 나간다 — 설정된 슬러그·비율·해상도가 요청에 실린다."""
    captured = {}

    def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return [b"png-bytes"], {
            "data": [{"media_type": "image/png"}],
            "usage": None,
            "id": "gen_1",
        }

    monkeypatch.setattr(image_engine.openrouter, "generate_image", fake_generate_image)
    monkeypatch.setattr(
        image_engine.settings,
        "GOOGLE_IMAGE_MODEL",
        "google/gemini-3.1-flash-image",
    )
    monkeypatch.setattr(image_engine.settings, "IMAGE_ASPECT_RATIO", "16:9")
    monkeypatch.setattr(image_engine.settings, "GOOGLE_IMAGE_RESOLUTION", "1K")
    monkeypatch.setattr(
        image_engine,
        "_upload_png_to_gcs",
        lambda payload, hospital: f"gs://bucket/{hospital}/{payload.decode()}.png",
    )
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: _ok_assessment(),
    )

    result = image_engine._generate_and_upload("medical prompt", "hospital-slug")

    assert result == "gs://bucket/hospital-slug/png-bytes.png"
    assert captured["model"] == "google/gemini-3.1-flash-image"
    assert captured["aspect_ratio"] == "16:9"
    assert captured["resolution"] == "1K"
    assert captured["prompt"] == "medical prompt"


def _patch_generate_image(monkeypatch, generate):
    monkeypatch.setattr(image_engine.openrouter, "generate_image", generate)
    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(image_engine._generate_and_upload.retry, "sleep", lambda _s: None)


def test_google_safety_block_is_not_retried_on_the_same_prompt(monkeypatch):
    """IMAGE_SAFETY는 같은 프롬프트에 항상 같은 결과다 — 3회 유료 재시도 금지."""
    calls = {"n": 0}

    def blocked(**_kwargs):
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "openrouter images 400: IMAGE_SAFETY",
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            response=httpx.Response(
                400,
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            ),
        )

    _patch_generate_image(monkeypatch, blocked)

    with pytest.raises(httpx.HTTPStatusError):
        image_engine._generate_and_upload("blocked prompt", "hospital-slug")

    assert calls["n"] == 1


def test_google_transient_empty_payload_still_retries(monkeypatch):
    calls = {"n": 0}

    def empty(**_kwargs):
        calls["n"] += 1
        return [], {"data": []}

    _patch_generate_image(monkeypatch, empty)

    with pytest.raises(tenacity.RetryError):
        image_engine._generate_and_upload("prompt", "hospital-slug")

    assert calls["n"] == 3


def test_openrouter_client_is_created_once_and_reused(monkeypatch):
    """검수·생성이 공유하는 게이트웨이 클라이언트는 timeout별로 한 번만 만든다."""
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(image_engine.openrouter, "OpenAI", FakeClient)
    image_engine._reset_clients_for_tests()

    first = image_engine.openrouter.sync_client(timeout=60.0)
    second = image_engine.openrouter.sync_client(timeout=60.0)

    assert first is second
    assert len(created) == 1
    assert created[0]["max_retries"] == 0
    image_engine._reset_clients_for_tests()


def test_semantic_policy_rejection_prevents_upload_and_same_prompt_retry(monkeypatch):
    calls = {"generation": 0, "upload": 0}

    def generated(**_kwargs):
        calls["generation"] += 1
        return [b"unsafe"], {"data": [{"media_type": "image/png"}]}

    _patch_generate_image(monkeypatch, generated)
    rejected = ImagePolicyAssessment(
        has_text=True,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ImagePolicyRejectedError(rejected)),
    )
    monkeypatch.setattr(
        image_engine,
        "_upload_png_to_gcs",
        lambda *_args: calls.__setitem__("upload", calls["upload"] + 1),
    )

    with pytest.raises(ImagePolicyRejectedError):
        image_engine._generate_and_upload("prompt", "hospital-slug")

    assert calls == {"generation": 1, "upload": 0}


def test_policy_rejection_diagnostic_preserves_stage_and_typed_assessment():
    assessment = ImagePolicyAssessment(
        has_text=True,
        has_logo=False,
        has_recognizable_people=False,
        impersonates_real_clinic=False,
        topic_relevant=True,
    )
    diagnostics = {}

    image_engine._record_policy_rejection(
        diagnostics,
        ImagePolicyRejectedError(assessment),
        stage=image_engine.ImagePolicyStage.GOOGLE_FALLBACK,
        prompt_version=image_engine.IMAGE_POLICY_REPAIR_PROMPT_VERSION,
        prior_failure="IMAGE_SAFETY",
    )

    assert diagnostics == {
        "reason": "POLICY_REJECTED",
        "policy_rejection": {
            "reason": "POLICY_REJECTED",
            "stage": "GOOGLE_FALLBACK",
            "prompt_version": "topical-no-text-repair-v3",
            "has_text": True,
            "has_logo": False,
            "has_recognizable_people": False,
            "impersonates_real_clinic": False,
            "topic_relevant": True,
            "prior_failure": "IMAGE_SAFETY",
        },
    }


def test_transient_upload_retry_reuses_the_verified_candidate(monkeypatch):
    calls = {"generation": 0, "review": 0, "upload": 0}

    def generated(**_kwargs):
        calls["generation"] += 1
        return [b"one-candidate"], {"data": [{"media_type": "image/png"}]}

    _patch_generate_image(monkeypatch, generated)

    def review(*_args, **_kwargs):
        calls["review"] += 1

    def upload(*_args):
        calls["upload"] += 1
        if calls["upload"] < 3:
            raise RuntimeError("temporary storage failure")
        return "gs://bucket/final.png"

    monkeypatch.setattr(image_engine, "_validate_generated_image", review)
    monkeypatch.setattr(image_engine, "_upload_png_to_gcs", upload)
    monkeypatch.setattr(image_engine._upload_verified_png.retry, "sleep", lambda _s: None)

    assert image_engine._generate_and_upload("prompt", "hospital") == "gs://bucket/final.png"
    assert calls == {"generation": 1, "review": 1, "upload": 3}


def test_policy_review_sends_strict_typed_schema_via_openrouter(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"has_text":false,"has_logo":false,'
                                '"has_recognizable_people":false,'
                                '"impersonates_real_clinic":false,"topic_relevant":true}'
                            )
                        )
                    )
                ]
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        image_engine.openrouter, "sync_client", lambda **_kwargs: FakeClient()
    )

    assessment = image_engine._validate_generated_image(
        b"png",
        mime_type="image/png",
        prompt="editorial prompt",
        expected_topic="복통 진료",
    )

    assert assessment.topic_relevant is True
    assert captured["response_format"]["type"] == "json_schema"
    schema = captured["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {
        "has_text",
        "has_logo",
        "has_recognizable_people",
        "impersonates_real_clinic",
        "topic_relevant",
    }


def test_google_visual_scene_does_not_echo_sensitive_medical_title():
    scene = image_engine._safe_google_visual_scene(
        "수원에서 치루나 항문농양을 진료할 병원은 어떻게 선택하나요?"
    )

    assert "치루" not in scene
    assert "항문" not in scene
    assert "glass of water" in scene


def test_google_visual_scene_preserves_safe_topic_variety():
    assert "cool pack" in image_engine._safe_google_visual_scene("소아 발열 치료")
    assert "unpowered ultrasound probe" in image_engine._safe_google_visual_scene("유방초음파 비용")
    assert "plain wooden blocks" in image_engine._safe_google_visual_scene("건강검진 준비")


def test_policy_review_failure_keeps_the_raw_provider_error(monkeypatch):
    """검수 실패의 원인은 공급자 원문에만 있다 — 요약으로 덮어쓰면 운영에서 특정할 수 없다.

    종전에는 어떤 원인이든 로그에 "image policy review failed"만 남아, 모델 404·권한
    없음·429를 구분할 방법이 없었다.
    """

    class _ProviderError(Exception):
        status_code = 404

    class FakeCompletions:
        def create(self, **_kwargs):
            raise _ProviderError(
                "No endpoints found for `google/gemini-3.6-flash`"
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        image_engine.openrouter, "sync_client", lambda **_kwargs: FakeClient()
    )

    with pytest.raises(ImagePolicyUnavailableError) as raised:
        image_engine._validate_generated_image(
            b"png-bytes", mime_type="image/png", prompt="p", expected_topic="t"
        )

    message = str(raised.value)
    assert "image policy review failed" in message
    assert "_ProviderError" in message
    assert "gemini-3.6-flash" in message


async def test_policy_unavailable_carries_the_raw_error_into_diagnostics(monkeypatch):
    """`_image_failure_class`가 진단 값 전체를 훑어 quota를 찾는다 — 원문을 남겨야 분류된다."""

    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    _allow_cost(monkeypatch)

    def _unavailable(*_args, **_kwargs):
        raise ImagePolicyUnavailableError(
            "image policy review failed: ClientError: 429 RESOURCE_EXHAUSTED"
        )

    monkeypatch.setattr(image_engine, "_generate_and_upload", _unavailable)
    diagnostics: dict[str, object] = {}

    url, _prompt = await image_engine.generate_image(
        ContentType.HEALTH, "테스트병원", topic="여름 탈수", diagnostics=diagnostics
    )

    assert url == ""
    assert diagnostics["reason"] == "POLICY_UNAVAILABLE"
    assert "429 RESOURCE_EXHAUSTED" in str(diagnostics["policy_error"])
    from app.workers.tasks import _image_failure_class

    assert _image_failure_class(diagnostics) == "PROVIDER_QUOTA"


async def test_missing_provider_configuration_is_not_a_silent_empty_result(monkeypatch):
    """공급자 설정 누락만 종전에 reason 없이 ("","")를 돌려줘 공급자 오류처럼 보고됐다."""

    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "")
    _allow_cost(monkeypatch)
    diagnostics: dict[str, object] = {}

    url, prompt = await image_engine.generate_image(
        ContentType.HEALTH, "테스트병원", topic="여름 탈수", diagnostics=diagnostics
    )

    assert (url, prompt) == ("", "")
    assert diagnostics["reason"] == "PROVIDER_NOT_CONFIGURED"


# ── 게이트웨이가 이미지 응답으로 읽을 수 없는 본문을 줬을 때 ────────────────────


def _unparsable_images_response(body: bytes, content_type: str) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/images")
    return httpx.Response(
        200, request=request, content=body, headers={"content-type": content_type}
    )


def test_unparsable_image_body_says_what_the_gateway_returned(monkeypatch):
    """빈 200을 `json.JSONDecodeError`로 올리면 운영에 남는 것은 "Expecting value"뿐이다.

    상태·content-type·본문 크기를 실어야 게이트웨이가 빈 응답을 줬는지 HTML 오류
    페이지를 줬는지 사후에 좁힐 수 있다.
    """
    monkeypatch.setattr(openrouter.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        openrouter.httpx,
        "post",
        lambda *_args, **_kwargs: _unparsable_images_response(b"", "text/html"),
    )

    with pytest.raises(openrouter.ImageResponseError) as raised:
        openrouter.generate_image(model="google/gemini-3.1-flash-image", prompt="p")

    message = str(raised.value)
    assert "openrouter images 200" in message
    assert "text/html" in message
    assert "bytes=0" in message
    assert "(empty body)" in message


def test_unparsable_image_body_keeps_a_bounded_snippet(monkeypatch):
    monkeypatch.setattr(openrouter.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        openrouter.httpx,
        "post",
        lambda *_args, **_kwargs: _unparsable_images_response(
            b"<html><body>upstream connect error</body></html>" + b"x" * 500,
            "text/html",
        ),
    )

    with pytest.raises(openrouter.ImageResponseError) as raised:
        openrouter.generate_image(model="google/gemini-3.1-flash-image", prompt="p")

    message = str(raised.value)
    assert "upstream connect error" in message
    assert len(message) < 500


def test_unparsable_image_body_is_retried_and_is_not_a_policy_block(monkeypatch):
    """해석할 수 없는 본문은 공급자의 정책 판정이 아니다.

    진단용 본문 조각에 우연히 섞인 단어(SAFETY 등)를 차단 신호로 읽으면 재시도 없이
    안전 폴백 프롬프트로 넘어가 버린다.
    """
    calls = {"n": 0}
    error = openrouter.ImageResponseError(
        "openrouter images 200 returned an unparsable body "
        "(content-type=text/html, bytes=64): Expecting value: line 1 column 1 "
        ":: <html>gateway error: safety check service unavailable</html>"
    )
    assert image_engine._looks_like_policy_block(error), (
        "본문 조각에 차단 표지가 섞인 경우를 골라야 이 테스트가 무언가를 지킨다"
    )
    assert image_engine._is_transient_google_image_error(error)

    def unparsable(**_kwargs):
        calls["n"] += 1
        raise error

    _patch_generate_image(monkeypatch, unparsable)

    with pytest.raises(tenacity.RetryError):
        image_engine._generate_and_upload("prompt", "hospital-slug")

    assert calls["n"] == 3


# ── 검수 모델이 빈 답을 줬을 때 ────────────────────────────────────────────────


_OK_REVIEW_JSON = (
    '{"has_text":false,"has_logo":false,"has_recognizable_people":false,'
    '"impersonates_real_clinic":false,"topic_relevant":true}'
)


def _review_client(monkeypatch, answers: list[tuple[str, str | None]]) -> list[str]:
    """호출 순서대로 (content, finish_reason)을 돌려주는 가짜 검수 클라이언트."""
    models: list[str] = []

    class FakeCompletions:
        def create(self, **kwargs):
            content, finish_reason = answers[len(models)]
            models.append(str(kwargs["model"]))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason=finish_reason,
                    )
                ]
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(image_engine.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(image_engine.settings, "GEMINI_MODEL", "google/review-vision")
    monkeypatch.setattr(image_engine.settings, "OPENAI_MODEL_PARSE", "openai/review-vision")
    monkeypatch.setattr(
        image_engine.openrouter, "sync_client", lambda **_kwargs: FakeClient()
    )
    return models


def test_empty_review_answer_lets_the_other_vision_model_run(monkeypatch):
    """빈 답은 검수 결과가 아니라 그 호출의 실패다.

    종전에는 `first_text`가 조용히 ""를 돌려줘 `model_validate_json("")`이 pydantic의
    "EOF while parsing"으로 터졌고, 예외를 조건으로 삼는 다른 비전 모델 재검수는 한 번도
    실행되지 못한 채 이미지 1건이 그대로 끝났다.
    """
    models = _review_client(
        monkeypatch, [("", "length"), (_OK_REVIEW_JSON, "stop")]
    )

    assessment = image_engine._validate_generated_image(
        b"png", mime_type="image/png", prompt="editorial prompt", expected_topic="복통 진료"
    )

    assert assessment.topic_relevant is True
    assert models == ["google/review-vision", "openai/review-vision"]


def test_blank_review_answer_is_treated_as_a_failed_call(monkeypatch):
    models = _review_client(
        monkeypatch, [("   \n", None), (_OK_REVIEW_JSON, "stop")]
    )

    image_engine._validate_generated_image(
        b"png", mime_type="image/png", prompt="p", expected_topic="t"
    )

    assert models == ["google/review-vision", "openai/review-vision"]


def test_two_empty_review_answers_name_the_empty_answer_not_a_json_eof(monkeypatch):
    models = _review_client(monkeypatch, [("", "length"), ("", "refusal")])

    with pytest.raises(ImagePolicyUnavailableError) as raised:
        image_engine._validate_generated_image(
            b"png", mime_type="image/png", prompt="p", expected_topic="t"
        )

    message = str(raised.value)
    assert "returned no assessment" in message
    assert "openai/review-vision" in message
    assert "finish_reason=refusal" in message
    assert "EOF while parsing" not in message
    assert models == ["google/review-vision", "openai/review-vision"]
