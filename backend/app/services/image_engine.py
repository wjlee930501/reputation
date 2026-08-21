"""
이미지 생성 엔진
- 기본: Vertex AI **Gemini 3.1 Flash Image**
- 선택: OpenAI **gpt-image-2**, 실패 시 Google 경로로 폴백
- 생성물은 GCS에 저장 후 gs:// 경로 반환 (공개 표면은 안정 프록시로 서빙)

설계 메모: 콘텐츠 카드 이미지가 유형별 고정 프롬프트라 "파란 빈 방"이 반복되던 슬롭 문제를
없애기 위해, 각 항목의 제목(topic)을 프롬프트에 주입해 항목마다 다른 그림이 나오게 한다.
의료광고법/이미지 정책 준수: 텍스트·로고 금지, 실존/식별 가능한 인물·얼굴 금지, 자극적(피·수술
장면) 묘사 금지, 실존 의료진·실제 병원 사진을 가장하지 않는 비식별 에디토리얼 사진만 허용.
"""
import base64
import logging
import uuid
from io import BytesIO

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentType
from app.services.image_direction import HospitalImageDirection, image_direction_prompt

logger = logging.getLogger(__name__)


def _is_transient_openai_error(exc: BaseException) -> bool:
    """결정적 4xx(예: moderation_blocked)는 재시도해도 항상 실패하므로 재시도 금지 —
    바로 Google 경로로 넘겨 시간/비용 낭비와 Job 타임아웃을 막는다. 5xx/네트워크만 재시도."""
    try:
        from openai import APIStatusError

        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", 500) or 500
            return status >= 500
    except Exception:  # noqa: BLE001 — openai 미설치 등은 재시도 대상으로 둔다
        pass
    return True

# ── gpt-image-2 프롬프트 (유형별 개념 + 항목 주제 주입) ───────────────────
_OPENAI_TYPE_SUBJECT = {
    ContentType.FAQ: (
        "Concept: a clear, reassuring visual metaphor that answers a common patient health question."
    ),
    ContentType.DISEASE: (
        "Concept: a calm symbolic still life or fully clothed anonymous lifestyle scene that "
        "helps explain a medical condition without explicit anatomy."
    ),
    ContentType.TREATMENT: (
        "Concept: a calm editorial illustration of a medical examination setting or preparation "
        "with non-graphic instruments and no active procedure."
    ),
    ContentType.COLUMN: (
        "Concept: a warm, thoughtful editorial scene evoking a doctor's clinical perspective and patient care."
    ),
    ContentType.HEALTH: (
        "Concept: a bright editorial lifestyle illustration about healthy daily habits and prevention."
    ),
    ContentType.LOCAL: (
        "Concept: a welcoming architectural or street-level editorial illustration evoking neighborhood healthcare."
    ),
    ContentType.NOTICE: (
        "Concept: a clean, modern motif for a clinic notice or information update."
    ),
}


def _build_openai_image_prompt(
    content_type: ContentType,
    topic: str | None,
    direction: HospitalImageDirection | None = None,
) -> str:
    subject = _OPENAI_TYPE_SUBJECT.get(content_type, _OPENAI_TYPE_SUBJECT[ContentType.FAQ])
    topic_line = f" The specific subject of this illustration is: {topic.strip()}." if topic else ""
    clinic_direction = image_direction_prompt(direction)
    return (
        "Create a refined editorial illustration for a Korean medical information hub. "
        f"{subject}{topic_line} {clinic_direction} "
        "Style: contemporary Korean health-magazine art with tactile paper and softly modeled forms, generous negative space, "
        "natural material texture, soft window light, warm ivory neutrals, subtle navy details, "
        "and one restrained muted-gold accent. Clean, calm, trustworthy and balanced; never glossy luxury. "
        "Tasteful and strictly non-graphic: no blood, no surgical gore, no needles in flesh, "
        "no distressing or clinical-procedure imagery. "
        "Keep it strictly non-explicit and family-friendly: do NOT depict bare skin, buttocks, "
        "genitalia, the anal or perianal region, underwear, or any nudity; for sensitive or "
        "proctological topics use a fully clothed, abstract wellness or lifestyle metaphor instead "
        "of body anatomy. "
        "Absolutely NO text, letters, numbers, words, captions, labels, logos, or watermarks anywhere. "
        "Do NOT depict real, identifiable, or named people and do NOT show recognizable faces; use anonymous "
        "hands, back/side crops, objects, food, architecture or equipment when people are not necessary. "
        "This is an original editorial scene, not documentary evidence of a real clinic or real doctor. "
        "No vectors, infographics, floating icons, anatomy diagrams, collages, generic stock-photo clichés, "
        "or empty blue rooms. 16:9 banner composition."
    )


# ── 유형별 이미지 프롬프트 (Google 폴백용) ───────────────────────────────
IMAGE_PROMPTS = {
    ContentType.FAQ: (
        "Refined Korean health-magazine editorial illustration, one clear reassuring visual metaphor, "
        "tactile paper and softly modeled forms, generous negative space, no text, no recognizable face"
    ),
    ContentType.DISEASE: (
        "Calm symbolic editorial still life explaining a health condition without explicit anatomy, "
        "tactile material texture, restrained composition, no text, no recognizable face"
    ),
    ContentType.TREATMENT: (
        "Editorial illustration of careful preparation for a medical examination, non-graphic objects, "
        "soft window light, warm ivory neutrals, no active procedure, no text, no recognizable face"
    ),
    ContentType.COLUMN: (
        "Thoughtful editorial illustration expressing a clinician's care philosophy through symbolic objects, "
        "warm calm atmosphere, subtle natural lighting, no named person, no text"
    ),
    ContentType.HEALTH: (
        "Bright Korean lifestyle editorial illustration about sustainable daily health habits, "
        "tactile materials, calm balanced composition, no text, no recognizable face"
    ),
    ContentType.LOCAL: (
        "Welcoming Korean neighborhood editorial streetscape suggesting accessible local healthcare, "
        "daytime, clean architecture, soft warm tones, no signage, no text"
    ),
    ContentType.NOTICE: (
        "Quiet editorial information motif made from paper, light, and orderly objects, "
        "contemporary Korean clinic mood, generous negative space, no text, no logo"
    ),
}

# 모델이 안전한 의료 제목까지 IMAGE_SAFETY로 오탐할 때 쓰는 최종 장면. 진단명·신체·
# 실존 병원·인물을 전혀 언급하지 않아 카드가 이미지 없이 영구 차단되는 것을 막는다.
GOOGLE_SAFETY_FALLBACK_PROMPT = (
    "Abstract editorial still life made from layered ivory paper, soft navy geometric curves, "
    "and one muted gold circle, calm balanced composition, subtle natural shadows, no people, "
    "no body parts, no medical procedure, no building, no text, no letters, no numbers, no logo, "
    "no watermark, family friendly, original artwork, 16:9 banner"
)

GOOGLE_EDITORIAL_SAFETY = (
    "No text, letters, numbers, caption, logo, or watermark. No recognizable face or named person. "
    "No real clinic documentary claim, explicit anatomy, blood, invasive procedure, or patient-result imagery. "
    "Keep every person fully clothed and anonymous. Original editorial artwork, 16:9 banner composition."
)


def _build_google_image_prompt(
    content_type: ContentType,
    topic: str | None,
    direction: HospitalImageDirection | None = None,
) -> str:
    parts = [IMAGE_PROMPTS.get(content_type, IMAGE_PROMPTS[ContentType.FAQ])]
    if topic:
        parts.append(f"Specific visual scene: {_safe_google_visual_scene(topic)}")
    clinic_direction = image_direction_prompt(direction)
    if clinic_direction:
        parts.append(clinic_direction)
    # The non-overridable safety/semantics contract comes after operator direction.
    parts.append(GOOGLE_EDITORIAL_SAFETY)
    return ". ".join(parts)


class _CallCounter:
    """동기 실행기 안에서 일어나는 재시도 횟수를 밖으로 전달하기 위한 카운터.

    재시도는 tenacity가 워커 스레드에서 처리하므로 async 호출부는 시도 횟수를 볼 수
    없다. 각 시도가 직접 tick()해 실제 호출 수를 남긴다.
    """

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


async def generate_image(
    content_type: ContentType,
    hospital_name: str,
    *,
    topic: str | None = None,
    direction: HospitalImageDirection | None = None,
) -> tuple[str, str]:
    """
    대표 이미지 생성 후 GCS에 저장.
    - 기본 Vertex AI Gemini 3.1 Flash Image (안전한 주제 장면으로 항목별 다양성 확보)
    - IMAGE_PROVIDER=openai이면 gpt-image-2 우선, 실패 시 Google 폴백
    - 둘 다 불가하면 ("", "") — 이미지 실패가 텍스트 콘텐츠를 막지 않게 한다.
    Returns: (gcs_path, prompt_used)  — gs://bucket/path 형태
    """
    import asyncio

    # 비용 가드: 이미지 생성 호출 예산 확인. 차단 시 이미지 없이 진행한다 — 본문은 유지되고,
    # 호출부는 이미 ("", "") 반환(=이미지 없음)을 정상 처리하므로 기존 실패 경로를 재사용한다.
    from app.services import cost_guard

    decision = await cost_guard.check_and_increment("image")
    if not decision.allowed:
        logger.warning("이미지 생성이 비용 가드로 차단됨 — 이미지 없이 진행: %s", decision.reason)
        return ("", "")

    loop = asyncio.get_running_loop()
    provider = (settings.IMAGE_PROVIDER or "").lower()

    # 이미지 1건은 공급자 호출 1회가 아니다 — OpenAI가 최대 3회 재시도되고, 그게 다
    # 실패하면 Google이 다시 호출된다. 예약은 위에서 1건만 잡았으므로, 실제 호출을
    # 세어 두지 않으면 상한이 실제 지출의 몇 분의 일만 보고 있게 된다.
    attempts = _CallCounter()

    if provider == "openai" and settings.OPENAI_API_KEY:
        prompt = _build_openai_image_prompt(content_type, topic, direction)
        try:
            url = await loop.run_in_executor(
                None, lambda: _openai_generate_and_upload(prompt, hospital_name, counter=attempts)
            )
            if url:
                return url, prompt
        except Exception as e:  # noqa: BLE001 — gpt-image-2 불가 시 Google 경로로 폴백
            logger.error("gpt-image-2 path failed, falling back to Google image: %s", e)
        finally:
            await _record_image_calls(attempts)

    # ── Vertex AI Gemini image (기본 또는 폴백) ──
    if not settings.GCP_PROJECT_ID:
        logger.warning("No usable image provider (OPENAI_API_KEY/GCP_PROJECT_ID) — skipping")
        return ("", "")

    prompt = _build_google_image_prompt(content_type, topic, direction)
    fallback_attempts = _CallCounter()
    try:
        url = await loop.run_in_executor(
            None, lambda: _generate_and_upload(prompt, hospital_name, counter=fallback_attempts)
        )
        return url, prompt
    except Exception as e:  # noqa: BLE001
        logger.warning("Google topic image failed; trying safety-neutral fallback: %s", e)
        try:
            url = await loop.run_in_executor(
                None,
                lambda: _generate_and_upload(
                    GOOGLE_SAFETY_FALLBACK_PROMPT,
                    hospital_name,
                    counter=fallback_attempts,
                ),
            )
            return url, GOOGLE_SAFETY_FALLBACK_PROMPT
        except Exception as fallback_exc:  # noqa: BLE001
            logger.error("Google image fallback failed: %s", fallback_exc)
            return ("", "")
    finally:
        await _record_image_calls(fallback_attempts)


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


def _safe_google_visual_scene(topic: str) -> str:
    """Map medical titles to non-sensitive, anonymous editorial still lifes.

    Raw titles such as pediatric fever or proctology terms can trigger an image
    model's safety stop even when the requested artwork is harmless.  They also
    encourage unwanted anatomy.  Keep topic-level variety without sending the
    diagnosis or body-region wording to the image model.
    """
    compact = "".join(topic.lower().split())
    if any(keyword in compact for keyword in ("발열", "탈수", "수분")):
        return "a glass of water and a digital thermometer arranged on a clean desk"
    if any(keyword in compact for keyword in ("유방초음파", "초음파")):
        return "an ultrasound monitor and folded towel in an empty bright examination room"
    if any(keyword in compact for keyword in ("건강검진", "검진")):
        return "a stethoscope, blank clipboard, and calendar blocks on a bright desk"
    if any(
        keyword in compact
        for keyword in (
            "항문",
            "치루",
            "치질",
            "치핵",
            "치열",
            "변비",
            "대장",
            "내시경",
            "출혈",
        )
    ):
        return "a glass of water, a blank appointment card, and a folded neutral towel"
    return "a blank appointment card and calm clinic objects arranged as a wellness still life"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=15),
    retry=retry_if_exception(_is_transient_openai_error),
)
def _openai_generate_and_upload(
    prompt: str, hospital_name: str, *, counter: _CallCounter | None = None
) -> str:
    """동기 — gpt-image-2 이미지 생성 + GCS 업로드 (실패 시 raise → 호출부에서 폴백).
    moderation_blocked 등 결정적 4xx 는 재시도하지 않고 즉시 raise → Google 폴백."""
    # tenacity 재시도마다 본문이 다시 실행된다 — 시도 1회 = 유료 호출 1회.
    if counter is not None:
        counter.tick()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=180.0, max_retries=0)
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
def _generate_and_upload(
    prompt: str, hospital_name: str, *, counter: _CallCounter | None = None
) -> str:
    """동기 — Vertex AI Gemini 이미지 생성 + GCS 업로드 (기본/폴백)."""
    if counter is not None:
        counter.tick()
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.GOOGLE_IMAGE_LOCATION,
            http_options=types.HttpOptions(api_version="v1"),
        )
        response = client.models.generate_content(
            model=settings.GOOGLE_IMAGE_MODEL,
            contents=prompt,
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
        image_bytes = next(
            (
                part.inline_data.data
                for part in parts
                if part.inline_data and part.inline_data.data
            ),
            None,
        )
        if not image_bytes:
            finish_reasons = [
                str(getattr(candidate, "finish_reason", None))
                for candidate in (response.candidates or [])
            ]
            raise ValueError(
                "Google image model returned no image payload "
                f"(finish_reasons={finish_reasons})"
            )
        return _upload_png_to_gcs(image_bytes, hospital_name)

    except ImportError:
        logger.error("Google Gen AI or GCS SDK not installed")
        return ""
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        raise
