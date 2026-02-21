"""
이미지 생성 엔진 — Google Imagen 3 (Vertex AI)
- 콘텐츠 유형별 프롬프트 자동 선택
- GCS에 저장 후 public URL 반환
"""
import logging
import uuid
from io import BytesIO

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentType

logger = logging.getLogger(__name__)

# ── 유형별 이미지 프롬프트 ────────────────────────────────────────
IMAGE_PROMPTS = {
    ContentType.FAQ: (
        "Clean medical infographic, soft blue and white color palette, Korean hospital setting, "
        "professional healthcare illustration, minimalist design, no text, no people"
    ),
    ContentType.DISEASE: (
        "Medical anatomy illustration, clean educational diagram, soft blue tones, "
        "professional healthcare visual, no text, minimalist"
    ),
    ContentType.TREATMENT: (
        "Modern Korean hospital treatment room, clean white aesthetic, medical equipment, "
        "soft lighting, professional photography style, empty room, no people"
    ),
    ContentType.COLUMN: (
        "Professional Korean doctor consultation setting, warm clinic atmosphere, "
        "trustworthy medical environment, soft natural lighting, no people visible"
    ),
    ContentType.HEALTH: (
        "Healthy lifestyle illustration, Korean context, clean bright design, "
        "prevention healthcare theme, soft green and blue tones, no text"
    ),
    ContentType.LOCAL: (
        "Korean local clinic exterior, neighborhood healthcare building, welcoming entrance, "
        "daytime, clean architecture, soft warm tones"
    ),
    ContentType.NOTICE: (
        "Modern Korean hospital interior, clean white and blue color scheme, "
        "professional medical environment, contemporary clinic design"
    ),
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
async def generate_image(content_type: ContentType, hospital_name: str) -> tuple[str, str]:
    """
    Imagen 3로 이미지 생성 후 GCS에 저장.
    Returns: (gcs_public_url, prompt_used)
    """
    import asyncio

    if not settings.GCP_PROJECT_ID:
        logger.warning("GCP_PROJECT_ID not set — skipping image generation")
        return ("", "")

    prompt = IMAGE_PROMPTS.get(content_type, IMAGE_PROMPTS[ContentType.FAQ])

    loop = asyncio.get_running_loop()
    url = await loop.run_in_executor(None, lambda: _generate_and_upload(prompt, hospital_name))
    return url, prompt


def _generate_and_upload(prompt: str, hospital_name: str) -> str:
    """
    동기 함수 — Vertex AI 이미지 생성 + GCS 업로드
    """
    try:
        import vertexai
        from vertexai.preview.vision_models import ImageGenerationModel
        from google.cloud import storage

        vertexai.init(project=settings.GCP_PROJECT_ID, location=settings.GCP_LOCATION)
        model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-002")

        images = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="16:9",
            safety_filter_level="block_some",
            person_generation="dont_allow",  # 병원 콘텐츠: 실제 인물 생성 금지
        )

        if not images or not images.images:
            raise ValueError("Imagen 3 returned no images")

        image_bytes = images.images[0]._image_bytes

        # GCS 업로드
        gcs_client = storage.Client()
        bucket = gcs_client.bucket(settings.GCP_STORAGE_BUCKET)

        filename = f"content/{hospital_name}/{uuid.uuid4().hex}.png"
        blob = bucket.blob(filename)
        blob.upload_from_file(BytesIO(image_bytes), content_type="image/png")
        blob.make_public()

        url = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{filename}"
        logger.info(f"Image uploaded: {url}")
        return url

    except ImportError:
        logger.error("Vertex AI or GCS SDK not installed")
        return ""
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        raise
