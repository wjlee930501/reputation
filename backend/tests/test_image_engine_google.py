from types import SimpleNamespace

import pytest
import tenacity
from google import genai

from app.services import image_engine


def test_google_image_generation_uses_current_vertex_model_and_uploads_payload(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(data=b"png-bytes")
                                )
                            ]
                        )
                    )
                ]
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(image_engine.settings, "GOOGLE_IMAGE_LOCATION", "global")
    monkeypatch.setattr(
        image_engine.settings,
        "GOOGLE_IMAGE_MODEL",
        "gemini-3.1-flash-image",
    )
    monkeypatch.setattr(
        image_engine,
        "_upload_png_to_gcs",
        lambda payload, hospital: f"gs://bucket/{hospital}/{payload.decode()}.png",
    )

    result = image_engine._generate_and_upload("medical prompt", "hospital-slug")

    assert result == "gs://bucket/hospital-slug/png-bytes.png"
    assert captured["client"]["vertexai"] is True
    assert captured["client"]["location"] == "global"
    assert captured["request"]["model"] == "gemini-3.1-flash-image"
    config = captured["request"]["config"]
    assert config.image_config.aspect_ratio == "16:9"
    assert config.image_config.person_generation == "ALLOW_NONE"


def _patch_google_client(monkeypatch, generate_content):
    class FakeModels:
        def generate_content(self, **kwargs):
            return generate_content(**kwargs)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(image_engine.settings, "GOOGLE_IMAGE_LOCATION", "global")
    monkeypatch.setattr(image_engine._generate_and_upload.retry, "sleep", lambda _s: None)


def test_google_safety_block_is_not_retried_on_the_same_prompt(monkeypatch):
    """IMAGE_SAFETY는 같은 프롬프트에 항상 같은 결과다 — 3회 유료 재시도 금지."""
    calls = {"n": 0}

    def blocked(**_kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(content=None, finish_reason="FinishReason.IMAGE_SAFETY")
            ]
        )

    _patch_google_client(monkeypatch, blocked)

    with pytest.raises(image_engine.ImageSafetyBlockedError):
        image_engine._generate_and_upload("blocked prompt", "hospital-slug")

    assert calls["n"] == 1


def test_google_transient_empty_payload_still_retries(monkeypatch):
    calls = {"n": 0}

    def empty(**_kwargs):
        calls["n"] += 1
        return SimpleNamespace(candidates=[SimpleNamespace(content=None, finish_reason="STOP")])

    _patch_google_client(monkeypatch, empty)

    with pytest.raises(tenacity.RetryError):
        image_engine._generate_and_upload("prompt", "hospital-slug")

    assert calls["n"] == 3


def test_google_client_is_created_once_and_reused(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.models = None

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")

    assert image_engine._get_google_client() is image_engine._get_google_client()
    assert len(created) == 1


def test_google_visual_scene_does_not_echo_sensitive_medical_title():
    scene = image_engine._safe_google_visual_scene(
        "수원에서 치루나 항문농양을 진료할 병원은 어떻게 선택하나요?"
    )

    assert "치루" not in scene
    assert "항문" not in scene
    assert "glass of water" in scene


def test_google_visual_scene_preserves_safe_topic_variety():
    assert "thermometer" in image_engine._safe_google_visual_scene("소아 발열 치료")
    assert "ultrasound monitor" in image_engine._safe_google_visual_scene("유방초음파 비용")
    assert "clipboard" in image_engine._safe_google_visual_scene("건강검진 준비")
