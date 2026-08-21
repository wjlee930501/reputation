from io import BytesIO
from types import SimpleNamespace

import pytest
from google import genai
from PIL import Image

from app.models.content import ContentType
from app.services import image_engine
from app.services.image_direction import HospitalImageDirection
from app.services.photo_upload import NormalizedPhoto


def _raster_bytes(image_format: str = "JPEG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 6), color=(12, 34, 56)).save(output, format=image_format)
    return output.getvalue()


def test_google_image_generation_returns_bytes_without_persisting(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"png-bytes"))]
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
    plan = image_engine.build_scene_plan(
        ContentType.FAQ, "무릎 통증", HospitalImageDirection.default()
    )
    request = image_engine.ImageGenerationSpec(
        hospital_slug="hospital-slug",
        scene=plan,
        prompt=image_engine.render_google_prompt(plan),
    )

    result = image_engine._google_generate(request)

    assert result == b"png-bytes"
    assert captured["client"]["vertexai"] is True
    assert captured["client"]["location"] == "global"
    assert captured["request"]["model"] == "gemini-3.1-flash-image"
    config = captured["request"]["config"]
    assert config.image_config.aspect_ratio == "16:9"
    assert config.image_config.person_generation == "ALLOW_NONE"


def test_policy_validator_parses_typed_bounded_multimodal_verdict(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text=(
                    '{"has_text":false,"has_logo":false,'
                    '"has_recognizable_people":false,'
                    '"impersonates_real_clinic":false,"topic_relevant":true}'
                )
            )

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    scene = image_engine.build_scene_plan(
        ContentType.HEALTH, "소아 발열", HospitalImageDirection.default()
    )

    raster = NormalizedPhoto(data=b"generated-webp")
    assessment = image_engine._validate_generated_image(raster, scene)

    assert assessment.topic_relevant is True
    assert captured["contents"][1].inline_data.data == b"generated-webp"
    assert captured["contents"][1].inline_data.mime_type == "image/webp"
    assert captured["config"].max_output_tokens == 512
    assert captured["config"].thinking_config.thinking_budget == 0
    assert captured["config"].response_schema is image_engine.ImagePolicyAssessment


def test_canonical_generated_raster_upload_uses_matching_webp_metadata(monkeypatch):
    captured = {}

    class Blob:
        def upload_from_file(self, stream, *, content_type):
            captured["payload"] = stream.read()
            captured["content_type"] = content_type

    class Bucket:
        def blob(self, filename):
            captured["filename"] = filename
            return Blob()

    class Client:
        def bucket(self, name):
            captured["bucket"] = name
            return Bucket()

    monkeypatch.setattr("app.services.gcs_utils._get_gcs_client", lambda: Client())
    monkeypatch.setattr(image_engine.settings, "GCP_STORAGE_BUCKET", "generated-images")
    raster = NormalizedPhoto(data=b"RIFF-canonical-webp")

    url = image_engine._upload_generated_raster(raster, "hospital-slug")

    assert url.endswith(".webp")
    assert captured["filename"].endswith(".webp")
    assert captured["content_type"] == "image/webp"
    assert captured["payload"] == raster.data


def test_rejected_generated_image_is_never_uploaded(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    generated_scenes = []

    def generate(spec, **_kwargs):
        generated_scenes.append(spec.scene)
        return _raster_bytes()

    monkeypatch.setattr(image_engine, "_google_generate", generate)
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: image_engine.ImagePolicyAssessment(
            has_text=True,
            has_logo=False,
            has_recognizable_people=False,
            impersonates_real_clinic=False,
            topic_relevant=True,
        ),
    )
    uploads: list[bytes] = []
    monkeypatch.setattr(
        image_engine,
        "_upload_generated_raster",
        lambda raster, _hospital: uploads.append(raster.data) or "gs://bucket/unsafe.webp",
    )

    result = __import__("asyncio").run(
        image_engine.generate_image(
            ContentType.FAQ,
            "hospital-slug",
            topic="무릎 통증",
            direction=HospitalImageDirection.default(),
        )
    )

    assert result == ("", "")
    assert uploads == []
    assert len(generated_scenes) == 2
    assert generated_scenes[0] == generated_scenes[1]


def test_repeated_openai_policy_rejection_fails_closed_without_google_fallback(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "openai")
    monkeypatch.setattr(image_engine.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    openai_calls = []
    monkeypatch.setattr(
        image_engine,
        "_openai_generate",
        lambda spec, **_kwargs: openai_calls.append(spec.scene) or _raster_bytes(),
    )
    monkeypatch.setattr(
        image_engine,
        "_google_generate",
        lambda *_args, **_kwargs: pytest.fail("policy rejection must not change providers"),
    )
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: image_engine.ImagePolicyAssessment(
            has_text=False,
            has_logo=True,
            has_recognizable_people=False,
            impersonates_real_clinic=False,
            topic_relevant=True,
        ),
    )
    monkeypatch.setattr(
        image_engine,
        "_upload_generated_raster",
        lambda *_args, **_kwargs: pytest.fail("rejected image must not be uploaded"),
    )

    result = __import__("asyncio").run(
        image_engine.generate_image(ContentType.FAQ, "hospital-slug", topic="건강검진")
    )

    assert result == ("", "")
    assert len(openai_calls) == 2
    assert openai_calls[0] == openai_calls[1]


def test_second_same_scene_generation_uploads_only_after_policy_acceptance(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    scenes = []
    reviewed_rasters = []
    verdicts = iter((False, True))

    def generate(spec, **_kwargs):
        scenes.append(spec.scene)
        return _raster_bytes("WEBP")

    def validate(raster, _scene, **_kwargs):
        reviewed_rasters.append(raster)
        return image_engine.ImagePolicyAssessment(
            has_text=False,
            has_logo=False,
            has_recognizable_people=False,
            impersonates_real_clinic=False,
            topic_relevant=next(verdicts),
        )

    uploaded = []
    monkeypatch.setattr(image_engine, "_google_generate", generate)
    monkeypatch.setattr(image_engine, "_validate_generated_image", validate)
    monkeypatch.setattr(
        image_engine,
        "_upload_generated_raster",
        lambda raster, _slug: uploaded.append(raster) or "gs://bucket/accepted.webp",
    )

    result = __import__("asyncio").run(
        image_engine.generate_image(
            ContentType.DISEASE,
            "hospital-slug",
            topic="무릎 관절 통증",
            direction=HospitalImageDirection.default(),
        )
    )

    assert result[0] == "gs://bucket/accepted.webp"
    assert scenes[0] == scenes[1]
    assert len(reviewed_rasters) == 2
    assert all(raster.data.startswith(b"RIFF") for raster in reviewed_rasters)
    assert all(raster.mime_type == "image/webp" for raster in reviewed_rasters)
    assert uploaded == [reviewed_rasters[1]]


def test_invalid_generated_raster_fails_closed_before_policy_or_upload(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(image_engine, "_google_generate", lambda *_args, **_kwargs: b"not-raster")
    calls = []
    monkeypatch.setattr(
        image_engine,
        "_validate_generated_image",
        lambda *_args, **_kwargs: calls.append("validate"),
    )
    monkeypatch.setattr(
        image_engine,
        "_upload_generated_raster",
        lambda *_args, **_kwargs: calls.append("upload"),
    )

    result = __import__("asyncio").run(
        image_engine.generate_image(ContentType.FAQ, "hospital-slug", topic="건강검진")
    )

    assert result == ("", "")
    assert calls == []


@pytest.mark.parametrize(
    ("override",),
    [
        ({"has_text": True},),
        ({"has_logo": True},),
        ({"has_recognizable_people": True},),
        ({"impersonates_real_clinic": True},),
        ({"topic_relevant": False},),
    ],
)
def test_policy_decision_rejects_each_blocking_visual_condition(override) -> None:
    values = {
        "has_text": False,
        "has_logo": False,
        "has_recognizable_people": False,
        "impersonates_real_clinic": False,
        "topic_relevant": True,
    }
    values.update(override)
    assessment = image_engine.ImagePolicyAssessment(**values)

    assert image_engine.image_is_publishable(assessment) is False
