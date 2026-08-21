"""Provider adapters for byte-only image generation and semantic review."""

import base64
import binascii
from dataclasses import dataclass
from typing import Final, Protocol

from google import genai
from google.api_core.exceptions import GoogleAPIError
from google.genai import types
from google.genai.errors import APIError as GoogleGenAIError
from openai import APIStatusError, OpenAIError
from pydantic import ValidationError
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.image_policy import ImagePolicyAssessment
from app.services.image_scene import ImageScenePlan
from app.services.photo_upload import NormalizedPhoto

PIPELINE_FAILURES: Final = (
    OpenAIError,
    GoogleGenAIError,
    GoogleAPIError,
    RetryError,
    binascii.Error,
)


class CallCounter(Protocol):
    def tick(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ImageGenerationSpec:
    hospital_slug: str
    scene: ImageScenePlan
    prompt: str


@dataclass(frozen=True, slots=True)
class NoImagePayloadError(Exception):
    provider: str

    def __str__(self) -> str:
        return f"{self.provider} returned no image payload"


@dataclass(frozen=True, slots=True)
class ImagePolicyUnavailableError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _is_transient_openai_error(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError):
        return (exc.status_code or 500) >= 500
    return True


def _vertex_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=settings.GCP_PROJECT_ID,
        location=settings.GOOGLE_IMAGE_LOCATION,
        http_options=types.HttpOptions(api_version="v1"),
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=15),
    retry=retry_if_exception(_is_transient_openai_error),
)
def openai_generate(
    spec: ImageGenerationSpec,
    *,
    counter: CallCounter | None = None,
) -> bytes:
    """Generate bytes with OpenAI without persisting them."""
    from openai import OpenAI

    if counter is not None:
        counter.tick()
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=180.0, max_retries=0)
    result = client.images.generate(
        model=settings.OPENAI_IMAGE_MODEL,
        prompt=spec.prompt,
        size=settings.OPENAI_IMAGE_SIZE,
        quality=settings.OPENAI_IMAGE_QUALITY,
        n=1,
    )
    if not result.data or not result.data[0].b64_json:
        raise NoImagePayloadError(provider="openai")
    return base64.b64decode(result.data[0].b64_json, validate=True)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def google_generate(
    spec: ImageGenerationSpec,
    *,
    counter: CallCounter | None = None,
) -> bytes:
    """Generate bytes with Vertex Gemini without persisting them."""
    if counter is not None:
        counter.tick()
    client = _vertex_client()
    response = client.models.generate_content(
        model=settings.GOOGLE_IMAGE_MODEL,
        contents=spec.prompt,
        config=types.GenerateContentConfig(
            response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
            candidate_count=1,
            image_config=types.ImageConfig(
                aspect_ratio="16:9",
                image_size="1K",
                person_generation="ALLOW_NONE",
            ),
        ),
    )
    parts = (
        response.candidates[0].content.parts
        if response.candidates and response.candidates[0].content
        else []
    ) or []
    payload = next(
        (part.inline_data.data for part in parts if part.inline_data and part.inline_data.data),
        None,
    )
    if payload is None:
        raise NoImagePayloadError(provider="google")
    return payload


def validate_generated_image(
    raster: NormalizedPhoto,
    scene: ImageScenePlan,
    *,
    counter: CallCounter | None = None,
) -> ImagePolicyAssessment:
    """Run one bounded multimodal judgment and parse its typed result."""
    if not settings.GCP_PROJECT_ID:
        raise ImagePolicyUnavailableError(reason="GCP project is required for image policy review")
    if counter is not None:
        counter.tick()
    client = _vertex_client()
    rubric = (
        "Inspect this generated editorial image. Return only the requested JSON policy assessment. "
        f"Expected scene subject={scene.subject.value}; expected props={','.join(scene.props)}. "
        "Mark topic_relevant false unless the dominant scene clearly matches that subject. "
        "Text includes any readable letters, numbers, signage, or caption. Logo includes brand marks "
        "or watermarks. Recognizable people includes any identifiable face. Clinic impersonation means "
        "the image appears to document a real clinic, named doctor, or real patient encounter."
    )
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[rubric, types.Part.from_bytes(data=raster.data, mime_type=raster.mime_type)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ImagePolicyAssessment,
            temperature=0,
            max_output_tokens=512,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    try:
        return ImagePolicyAssessment.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise ImagePolicyUnavailableError(reason="invalid image policy response") from exc
