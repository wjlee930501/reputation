"""
이미지 생성 엔진
- 기본: OpenAI **gpt-image-2** (editorial 의료 일러스트, 주제별 프롬프트로 다양성 확보)
- 폴백: Google Imagen 3 (Vertex AI) — OPENAI 키 미설정/IMAGE_PROVIDER=imagen 일 때
- 생성물은 GCS에 저장 후 gs:// 경로 반환 (공개 표면은 안정 프록시로 서빙)

설계 메모: 콘텐츠 카드 이미지가 유형별 고정 프롬프트라 "파란 빈 방"이 반복되던 슬롭 문제를
없애기 위해, 각 항목의 제목(topic)을 프롬프트에 주입해 항목마다 다른 그림이 나오게 한다.
의료광고법/이미지 정책 준수: 텍스트·로고 금지, 실존/식별 가능한 인물·얼굴 금지, 자극적(피·수술
장면) 묘사 금지, 실제 병원 사진을 가장하지 않는 '일러스트'임을 명시.
"""
import base64
import logging
import uuid
from io import BytesIO

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentType

logger = logging.getLogger(__name__)

_vertexai_initialized = False


def _ensure_vertexai_initialized():
    global _vertexai_initialized
    if not _vertexai_initialized and settings.GCP_PROJECT_ID:
        import vertexai
        vertexai.init(project=settings.GCP_PROJECT_ID, location=settings.GCP_LOCATION)
        _vertexai_initialized = True


# ── gpt-image-2 프롬프트 (유형별 개념 + 항목 주제 주입) ───────────────────
_OPENAI_TYPE_SUBJECT = {
    ContentType.FAQ: (
        "Concept: a clear, reassuring visual metaphor that answers a common patient health question."
    ),
    ContentType.DISEASE: (
        "Concept: a clean, educational anatomical or conceptual illustration that explains a medical condition."
    ),
    ContentType.TREATMENT: (
        "Concept: a calm, conceptual illustration of a medical examination or treatment process "
        "with abstracted, non-graphic instruments."
    ),
    ContentType.COLUMN: (
        "Concept: a warm, thoughtful editorial scene evoking a doctor's clinical perspective and patient care."
    ),
    ContentType.HEALTH: (
        "Concept: a bright, optimistic illustration about healthy living, daily habits and prevention."
    ),
    ContentType.LOCAL: (
        "Concept: a welcoming illustration of neighborhood healthcare and a local community clinic."
    ),
    ContentType.NOTICE: (
        "Concept: a clean, modern motif for a clinic notice or information update."
    ),
}


def _build_openai_image_prompt(content_type: ContentType, topic: str | None) -> str:
    subject = _OPENAI_TYPE_SUBJECT.get(content_type, _OPENAI_TYPE_SUBJECT[ContentType.FAQ])
    topic_line = f" The specific subject of this illustration is: {topic.strip()}." if topic else ""
    return (
        "Create a refined, premium editorial illustration for a Korean medical information hub. "
        f"{subject}{topic_line} "
        "Style: clean modern semi-flat vector with subtle depth, generous negative space, "
        "a calm and trustworthy palette of soft clinical blues and warm neutrals with a single "
        "restrained accent color, gentle even lighting, and balanced composition. "
        "Tasteful and strictly non-graphic: no blood, no surgical gore, no needles in flesh, "
        "no distressing or clinical-procedure imagery. "
        "Absolutely NO text, letters, numbers, words, captions, labels, logos, or watermarks anywhere. "
        "Do NOT depict real, identifiable, or named people and do NOT show recognizable faces. "
        "This is an original illustration — not a photograph of a real clinic or a real person. "
        "Avoid generic stock-photo clichés and empty blue rooms. 16:9 banner composition."
    )


# ── 유형별 이미지 프롬프트 (Imagen 폴백용) ───────────────────────────────
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


async def generate_image(
    content_type: ContentType, hospital_name: str, *, topic: str | None = None
) -> tuple[str, str]:
    """
    대표 이미지 생성 후 GCS에 저장.
    - 기본 gpt-image-2 (topic으로 항목별 다양성 확보)
    - gpt-image-2 실패(접근권한/일시오류) 또는 IMAGE_PROVIDER=imagen → Imagen 3 폴백
    - 둘 다 불가하면 ("", "") — 이미지 실패가 텍스트 콘텐츠를 막지 않게 한다.
    Returns: (gcs_path, prompt_used)  — gs://bucket/path 형태
    """
    import asyncio

    loop = asyncio.get_running_loop()
    provider = (settings.IMAGE_PROVIDER or "").lower()

    if provider == "openai" and settings.OPENAI_API_KEY:
        prompt = _build_openai_image_prompt(content_type, topic)
        try:
            url = await loop.run_in_executor(
                None, lambda: _openai_generate_and_upload(prompt, hospital_name)
            )
            if url:
                return url, prompt
        except Exception as e:  # noqa: BLE001 — gpt-image-2 불가 시 Imagen으로 폴백
            logger.error("gpt-image-2 path failed, falling back to Imagen: %s", e)

    # ── Imagen 3 (명시 선택 또는 폴백) ──
    if not settings.GCP_PROJECT_ID:
        logger.warning("No usable image provider (OPENAI_API_KEY/GCP_PROJECT_ID) — skipping")
        return ("", "")

    prompt = IMAGE_PROMPTS.get(content_type, IMAGE_PROMPTS[ContentType.FAQ])
    try:
        url = await loop.run_in_executor(None, lambda: _generate_and_upload(prompt, hospital_name))
        return url, prompt
    except Exception as e:  # noqa: BLE001
        logger.error("Imagen fallback failed: %s", e)
        return ("", "")


def _upload_png_to_gcs(image_bytes: bytes, hospital_name: str) -> str:
    """PNG 바이트를 GCS content/{hospital}/{uuid}.png 로 업로드하고 gs:// 경로 반환."""
    from app.services.gcs_utils import _get_gcs_client

    gcs_client = _get_gcs_client()
    bucket = gcs_client.bucket(settings.GCP_STORAGE_BUCKET)
    filename = f"content/{hospital_name}/{uuid.uuid4().hex}.png"
    blob = bucket.blob(filename)
    blob.upload_from_file(BytesIO(image_bytes), content_type="image/png")
    gcs_path = f"gs://{settings.GCP_STORAGE_BUCKET}/{filename}"
    logger.info("Image uploaded: %s", gcs_path)
    return gcs_path


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _openai_generate_and_upload(prompt: str, hospital_name: str) -> str:
    """동기 — gpt-image-2 이미지 생성 + GCS 업로드 (실패 시 raise → 호출부에서 폴백)."""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=180.0)
        # response_format은 gpt-image 계열에서 기본 b64_json이며 일부 버전이 명시 전달을
        # 거부하므로 전달하지 않는다(기본값 사용).
        result = client.images.generate(
            model=settings.OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size=settings.OPENAI_IMAGE_SIZE,
            quality=settings.OPENAI_IMAGE_QUALITY,
            n=1,
        )
        if not result.data:
            raise ValueError("gpt-image-2 returned no data")
        b64 = result.data[0].b64_json
        if not b64:
            raise ValueError("gpt-image-2 returned no b64_json payload")
        image_bytes = base64.b64decode(b64)
        return _upload_png_to_gcs(image_bytes, hospital_name)
    except ImportError:
        logger.error("openai SDK not installed")
        return ""
    except Exception as e:
        logger.error("gpt-image-2 generation failed: %s", e)
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _generate_and_upload(prompt: str, hospital_name: str) -> str:
    """동기 — Vertex AI Imagen 3 이미지 생성 + GCS 업로드 (폴백)."""
    try:
        from vertexai.preview.vision_models import ImageGenerationModel

        _ensure_vertexai_initialized()
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
        return _upload_png_to_gcs(image_bytes, hospital_name)

    except ImportError:
        logger.error("Vertex AI or GCS SDK not installed")
        return ""
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        raise
