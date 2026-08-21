"""Orchestrate grounded generation, policy review, and gated persistence."""

import logging
import uuid
from io import BytesIO
from typing import assert_never

import anyio

from app.core.config import settings
from app.models.content import ContentType
from app.services.image_direction import HospitalImageDirection
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    image_is_publishable,
)
from app.services.image_provider import (
    PIPELINE_FAILURES as _PIPELINE_FAILURES,
)
from app.services.image_provider import (
    ImageGenerationSpec,
    ImagePolicyUnavailableError,
    NoImagePayloadError,
)
from app.services.image_provider import (
    google_generate as _google_generate,
)
from app.services.image_provider import (
    openai_generate as _openai_generate,
)
from app.services.image_provider import (
    validate_generated_image as _validate_generated_image,
)
from app.services.image_scene import (
    build_scene_plan,
    render_google_prompt,
    render_openai_prompt,
)
from app.services.photo_upload import InvalidPhotoUpload, NormalizedPhoto, normalize_photo_upload

logger = logging.getLogger(__name__)


class _CallCounter:
    """Mutable counter because provider retries run inside a worker thread."""

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def tick(self) -> None:
        self.count += 1


async def _record_image_calls(counter: _CallCounter) -> None:
    if counter.count <= 0:
        return
    from app.services import cost_guard

    await cost_guard.record_provider_call("image", count=counter.count)


def _upload_generated_raster(raster: NormalizedPhoto, hospital_slug: str) -> str:
    """Persist a canonical raster with matching extension and MIME metadata."""
    from app.services.gcs_utils import _get_gcs_client

    client = _get_gcs_client()
    extension = raster.filename.rsplit(".", maxsplit=1)[-1]
    filename = f"content/{hospital_slug}/{uuid.uuid4().hex}.{extension}"
    blob = client.bucket(settings.GCP_STORAGE_BUCKET).blob(filename)
    blob.upload_from_file(BytesIO(raster.data), content_type=raster.mime_type)
    return f"gs://{settings.GCP_STORAGE_BUCKET}/{filename}"


async def _generate_reviewed(
    spec: ImageGenerationSpec,
    *,
    openai: bool,
    counter: _CallCounter,
) -> NormalizedPhoto | ImagePolicyRejectedError:
    generator = _openai_generate if openai else _google_generate
    image_bytes = await anyio.to_thread.run_sync(lambda: generator(spec, counter=counter))
    normalized = await anyio.to_thread.run_sync(
        lambda: normalize_photo_upload(
            image_bytes,
            lossless=True,
            target_aspect_ratio=(16, 9),
        )
    )
    assessment = await anyio.to_thread.run_sync(
        lambda: _validate_generated_image(normalized, spec.scene, counter=counter)
    )
    if image_is_publishable(assessment):
        return normalized
    return ImagePolicyRejectedError(assessment=assessment)


async def _generate_checked(
    spec: ImageGenerationSpec,
    *,
    openai: bool,
    counter: _CallCounter,
) -> tuple[str, str]:
    first = await _generate_reviewed(spec, openai=openai, counter=counter)
    match first:
        case NormalizedPhoto() as raster:
            url = await anyio.to_thread.run_sync(
                lambda: _upload_generated_raster(raster, spec.hospital_slug)
            )
            return url, spec.prompt
        case ImagePolicyRejectedError(assessment=assessment):
            logger.warning("Generated image rejected by policy gate: %s", assessment.model_dump())
        case unreachable:
            assert_never(unreachable)

    second = await _generate_reviewed(spec, openai=openai, counter=counter)
    match second:
        case NormalizedPhoto() as raster:
            url = await anyio.to_thread.run_sync(
                lambda: _upload_generated_raster(raster, spec.hospital_slug)
            )
            return url, spec.prompt
        case ImagePolicyRejectedError(assessment=assessment):
            logger.warning("Regenerated image rejected by policy gate: %s", assessment.model_dump())
            raise second
        case unreachable:
            assert_never(unreachable)


async def generate_image(
    content_type: ContentType,
    hospital_slug: str,
    *,
    topic: str | None = None,
    direction: HospitalImageDirection | None = None,
) -> tuple[str, str]:
    """Generate a grounded image, fail closed on generation or policy uncertainty."""
    from app.services import cost_guard

    decision = await cost_guard.check_and_increment("image")
    if not decision.allowed:
        return "", ""

    scene = build_scene_plan(content_type, topic, direction or HospitalImageDirection.default())
    counter = _CallCounter()
    provider = (settings.IMAGE_PROVIDER or "google").lower()
    try:
        if provider == "openai" and settings.OPENAI_API_KEY:
            try:
                spec = ImageGenerationSpec(
                    hospital_slug=hospital_slug,
                    scene=scene,
                    prompt=render_openai_prompt(scene),
                )
                return await _generate_checked(
                    spec, openai=True, counter=counter
                )
            except (
                *_PIPELINE_FAILURES,
                NoImagePayloadError,
                InvalidPhotoUpload,
            ) as exc:
                logger.warning("OpenAI image path failed; using grounded Google path: %s", exc)
        if not settings.GCP_PROJECT_ID:
            return "", ""
        spec = ImageGenerationSpec(
            hospital_slug=hospital_slug,
            scene=scene,
            prompt=render_google_prompt(scene),
        )
        return await _generate_checked(
            spec, openai=False, counter=counter
        )
    except (
        *_PIPELINE_FAILURES,
        NoImagePayloadError,
        ImagePolicyUnavailableError,
        ImagePolicyRejectedError,
        InvalidPhotoUpload,
    ) as exc:
        logger.error("Image generation or validation failed closed: %s", exc)
        return "", ""
    finally:
        await _record_image_calls(counter)


__all__ = [
    "ImageGenerationSpec",
    "ImagePolicyAssessment",
    "ImagePolicyRejectedError",
    "NoImagePayloadError",
    "build_scene_plan",
    "generate_image",
    "image_is_publishable",
    "render_google_prompt",
    "render_openai_prompt",
]
