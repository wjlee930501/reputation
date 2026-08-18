from types import SimpleNamespace

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
        "gemini-2.5-flash-image",
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
    assert captured["request"]["model"] == "gemini-2.5-flash-image"
    config = captured["request"]["config"]
    assert config.image_config.aspect_ratio == "16:9"
    assert config.image_config.person_generation == "ALLOW_NONE"
