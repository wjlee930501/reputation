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
import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.content import ContentType
from app.services.image_direction import (
    HospitalImageDirection,
    image_direction_prompt,
    image_repair_direction_prompt,
)
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    ImagePolicyUnavailableError,
    image_is_publishable,
)

logger = logging.getLogger(__name__)
IMAGE_POLICY_VERSION = "image-policy-v2"
IMAGE_POLICY_FALLBACK_PROMPT_VERSION = "topical-no-text-v1"
IMAGE_POLICY_REPAIR_PROMPT_VERSION = "topical-no-text-repair-v2"


@dataclass(frozen=True)
class CertifiedImageArtifact:
    image_bytes: bytes
    content_hash: str
    subject_hash: str


class ImagePolicyStage(StrEnum):
    EXISTING_IMAGE_REVIEW = "EXISTING_IMAGE_REVIEW"
    OPENAI_PRIMARY = "OPENAI_PRIMARY"
    OPENAI_REPAIR = "OPENAI_REPAIR"
    GOOGLE_PRIMARY = "GOOGLE_PRIMARY"
    GOOGLE_FALLBACK = "GOOGLE_FALLBACK"
    GOOGLE_REPAIR = "GOOGLE_REPAIR"


@dataclass(frozen=True, slots=True)
class ImagePolicyRejectionDiagnostic:
    stage: ImagePolicyStage
    assessment: ImagePolicyAssessment
    prompt_version: str
    prior_failure: str | None = None

    def to_state(self) -> dict[str, str | bool]:
        return {
            "reason": "POLICY_REJECTED",
            "stage": self.stage.value,
            "prompt_version": self.prompt_version,
            "has_text": self.assessment.has_text,
            "has_logo": self.assessment.has_logo,
            "has_recognizable_people": self.assessment.has_recognizable_people,
            "impersonates_real_clinic": self.assessment.impersonates_real_clinic,
            "topic_relevant": self.assessment.topic_relevant,
            **({"prior_failure": self.prior_failure} if self.prior_failure else {}),
        }


def _record_policy_rejection(
    diagnostics: dict[str, object] | None,
    exc: ImagePolicyRejectedError,
    *,
    stage: ImagePolicyStage,
    prompt_version: str,
    prior_failure: str | None = None,
) -> None:
    if diagnostics is None:
        return
    diagnostics["reason"] = "POLICY_REJECTED"
    diagnostics["policy_rejection"] = ImagePolicyRejectionDiagnostic(
        stage=stage,
        assessment=exc.assessment,
        prompt_version=prompt_version,
        prior_failure=prior_failure,
    ).to_state()

# ── 공급자 클라이언트 lazy 싱글턴 ────────────────────────────────────────
# 시도마다 클라이언트를 새로 만들면 커넥션 풀과 TLS 세션을 매번 버린다.
# 초기화만 잠그고(Celery prefork 자식 안 스레드 동시 진입) 이후에는 그대로 재사용한다.
_openai_client_instance = None
_google_client_instance = None
_client_lock = threading.Lock()


def _get_openai_client():
    global _openai_client_instance
    if _openai_client_instance is None:
        from openai import OpenAI

        with _client_lock:
            if _openai_client_instance is None:
                _openai_client_instance = OpenAI(
                    api_key=settings.OPENAI_API_KEY, timeout=180.0, max_retries=0
                )
    return _openai_client_instance


def _get_google_client():
    global _google_client_instance
    if _google_client_instance is None:
        from google import genai
        from google.genai import types

        with _client_lock:
            if _google_client_instance is None:
                _google_client_instance = genai.Client(
                    vertexai=True,
                    project=settings.GCP_PROJECT_ID,
                    location=settings.GOOGLE_IMAGE_LOCATION,
                    http_options=types.HttpOptions(api_version="v1", timeout=60_000),
                )
    return _google_client_instance


def _reset_clients_for_tests() -> None:
    """테스트가 settings/SDK 생성자를 바꿔치기한 뒤 캐시를 비우기 위한 훅."""

    global _openai_client_instance, _google_client_instance
    with _client_lock:
        _openai_client_instance = None
        _google_client_instance = None


class ImageSafetyBlockedError(ValueError):
    """모델 안전/정책 차단 — 같은 프롬프트를 다시 보내도 항상 같은 결과다."""


# Vertex 이미지 모델이 프롬프트를 정책으로 막을 때 쓰는 finish_reason 계열.
_GOOGLE_BLOCK_MARKERS = (
    "IMAGE_SAFETY",
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "RECITATION",
    "SPII",
)


def _looks_like_policy_block(value: object) -> bool:
    text = str(value).upper()
    return any(marker in text for marker in _GOOGLE_BLOCK_MARKERS)


def _is_transient_google_image_error(exc: BaseException) -> bool:
    """안전 차단과 결정적 4xx는 재시도 금지 — 바로 안전 폴백 프롬프트로 넘긴다.

    종전에는 IMAGE_SAFETY 차단도 3회 재시도한 뒤 폴백 프롬프트를 다시 3회 재시도해
    이미지 1장에 최대 6회 유료 호출이 나갔다. 차단은 같은 입력에 항상 같은 결과다.
    """
    if isinstance(
        exc,
        (ImageSafetyBlockedError, ImagePolicyRejectedError, ImagePolicyUnavailableError),
    ) or _looks_like_policy_block(exc):
        return False
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        return False
    return True


def _is_transient_openai_error(exc: BaseException) -> bool:
    """결정적 4xx(예: moderation_blocked)는 재시도해도 항상 실패하므로 재시도 금지 —
    바로 Google 경로로 넘겨 시간/비용 낭비와 Job 타임아웃을 막는다. 5xx/네트워크만 재시도."""
    if isinstance(exc, (ImagePolicyRejectedError, ImagePolicyUnavailableError)):
        return False
    try:
        from openai import APIStatusError

        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", 500) or 500
            return status >= 500
    except Exception:  # noqa: BLE001 — openai 미설치 등은 재시도 대상으로 둔다
        pass
    return True


def _is_transient_upload_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return isinstance(exc, (ConnectionError, TimeoutError, OSError, RuntimeError))


def image_subject_hash(content_type: ContentType | object, topic: str | None) -> str:
    value = getattr(content_type, "value", content_type)
    normalized = " ".join(str(topic or "").split()).casefold()
    return hashlib.sha256(f"{value}|{normalized}".encode("utf-8")).hexdigest()


def image_content_hash_from_url(url: str | None) -> str | None:
    filename = str(url or "").rsplit("/", 1)[-1]
    candidate = filename.split("-", 1)[0]
    return candidate if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate) else None

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

# The fallback keeps safe topical objects. A generic abstract composition cannot
# truthfully pass the separate topic-relevance gate for a concrete medical article.
GOOGLE_SAFETY_FALLBACK_PROMPT = (
    "Create one topic-specific editorial still life using only the listed unmarked objects. "
    "Do not add symbols, screens, forms, charts, packaging, signs, or printed surfaces"
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
        parts.append(
            f"Specific visual scene: {_safe_google_visual_scene(topic, content_type)}"
        )
    clinic_direction = image_direction_prompt(direction)
    if clinic_direction:
        parts.append(clinic_direction)
    # The non-overridable safety/semantics contract comes after operator direction.
    parts.append(GOOGLE_EDITORIAL_SAFETY)
    return ". ".join(parts)


def _build_google_safety_fallback_prompt(
    content_type: ContentType,
    topic: str | None,
    direction: HospitalImageDirection | None = None,
) -> str:
    """Build a topical repair prompt without clinic identity or free-form direction."""

    parts = [
        GOOGLE_SAFETY_FALLBACK_PROMPT,
        f"Specific visual scene: {_safe_google_visual_scene(topic or '', content_type)}",
    ]
    repair_direction = image_repair_direction_prompt(direction)
    if repair_direction:
        parts.append(repair_direction)
    parts.append(GOOGLE_EDITORIAL_SAFETY)
    return ". ".join(parts)


def _build_google_policy_repair_prompt(
    content_type: ContentType,
    topic: str | None,
    direction: HospitalImageDirection | None = None,
    prior_rejection: dict[str, object] | None = None,
) -> str:
    """Build a distinct second-attempt prompt from bounded prior policy facts."""

    scene = _safe_google_visual_scene(topic or "", content_type)
    parts = [
        GOOGLE_SAFETY_FALLBACK_PROMPT,
        f"Make this exact object group the single dominant subject: {scene}",
        "Use a plain textile background with no flat writable or printed surfaces",
    ]
    repair_direction = image_repair_direction_prompt(direction)
    if repair_direction:
        parts.append(repair_direction)
    if prior_rejection:
        if prior_rejection.get("topic_relevant") is False:
            parts.append("Keep every listed topical object large, clear, and unobscured")
        if prior_rejection.get("has_text") is True:
            parts.append("Use organic textures only and remove every mark or glyph-like detail")
        if prior_rejection.get("has_logo") is True:
            parts.append("Use generic natural materials with no branded product shapes")
        if prior_rejection.get("has_recognizable_people") is True:
            parts.append("Objects only, with no people, hands, faces, or silhouettes")
        if prior_rejection.get("impersonates_real_clinic") is True:
            parts.append("Use no clinic interior, exterior, uniform, or documentary photography")
    parts.append(GOOGLE_EDITORIAL_SAFETY)
    return ". ".join(parts)


class _CallCounter:
    """Carry generation and semantic-review call counts out of sync executors.

    Image generation uses the image budget. The cheaper multimodal policy check
    uses the existing content/review budget; combining them would make one image
    consume two scarce image-generation units.
    """

    __slots__ = ("count", "review_count", "events", "logical_call_id")

    def __init__(self) -> None:
        self.count = 0
        self.review_count = 0
        self.events: list[dict[str, object]] = []
        self.logical_call_id = str(uuid.uuid4())

    def tick(self, provider: str = "unknown", model: str = "unknown") -> dict[str, object]:
        self.count += 1
        event: dict[str, object] = {
            "provider": provider,
            "model": model,
            "workflow": "content_image_generation",
        }
        self.events.append(event)
        return event

    def tick_review(
        self, provider: str = "unknown", model: str = "unknown"
    ) -> dict[str, object]:
        self.review_count += 1
        event: dict[str, object] = {
            "provider": provider,
            "model": model,
            "workflow": "content_image_review",
        }
        self.events.append(event)
        return event


async def _record_image_calls(
    counter: _CallCounter, hospital_id: uuid.UUID | str | None = None
) -> None:
    if counter.count <= 0 and counter.review_count <= 0:
        return
    from app.services import cost_guard

    if counter.count > 0:
        await cost_guard.record_provider_call("image", count=counter.count)
    if counter.review_count > 0:
        await cost_guard.record_provider_call("content", count=counter.review_count)
    from app.services import provider_usage

    workflow_attempts: dict[str, int] = {}
    for event in counter.events:
        is_generation = event["workflow"] == "content_image_generation"
        workflow = str(event["workflow"])
        http_attempt = workflow_attempts.get(workflow, 0) + 1
        workflow_attempts[workflow] = http_attempt
        await provider_usage.record_attempt(
            provider=str(event["provider"]),
            model=str(event["model"]),
            workflow=workflow,
            cost_category="image" if is_generation else "content",
            hospital_id=hospital_id,
            logical_call_id=counter.logical_call_id,
            attempt_id=f"{counter.logical_call_id}:{workflow}:http:{http_attempt}",
            http_attempt=http_attempt,
            provider_request_id=str(event.get("provider_request_id") or "") or None,
            usage=event.get("usage"),
            usage_known=event.get("usage_known"),
            image_units=event.get("image_units") if is_generation else None,
        )


async def _settle_image_reservations(
    *counters: _CallCounter,
    image_receipt=None,
    review_receipt=None,
) -> None:
    """Settle against the original reservation period, even across midnight."""

    from app.services import cost_guard

    await cost_guard.settle_reservation(
        image_receipt,
        consumed_units=min(sum(counter.count for counter in counters), 1),
    )
    await cost_guard.settle_reservation(
        review_receipt,
        consumed_units=min(sum(counter.review_count for counter in counters), 1),
    )


async def generate_image(
    content_type: ContentType,
    hospital_name: str,
    *,
    topic: str | None = None,
    direction: HospitalImageDirection | None = None,
    hospital_id: uuid.UUID | str | None = None,
    diagnostics: dict[str, object] | None = None,
    policy_repair: bool = False,
    prior_policy_rejection: dict[str, object] | None = None,
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

    decision = await cost_guard.reserve("image")
    if not decision.allowed:
        if diagnostics is not None:
            diagnostics["reason"] = "COST_BLOCKED"
        logger.warning("이미지 생성이 비용 가드로 차단됨 — 이미지 없이 진행: %s", decision.reason)
        return ("", "")
    review_decision = await cost_guard.reserve("content")
    if not review_decision.allowed:
        if diagnostics is not None:
            diagnostics["reason"] = "COST_BLOCKED"
        await cost_guard.settle_reservation(decision.receipt, consumed_units=0)
        logger.warning(
            "이미지 안전 검수가 비용 가드로 차단됨 — 이미지 생성을 건너뜀: %s",
            review_decision.reason,
        )
        return ("", "")

    loop = asyncio.get_running_loop()
    provider = (settings.IMAGE_PROVIDER or "").lower()

    # 이미지 1건은 공급자 호출 1회가 아니다 — OpenAI가 최대 3회 재시도되고, 그게 다
    # 실패하면 Google이 다시 호출된다. 예약은 위에서 1건만 잡았으므로, 실제 호출을
    # 세어 두지 않으면 상한이 실제 지출의 몇 분의 일만 보고 있게 된다.
    attempts = _CallCounter()

    if provider == "openai" and settings.OPENAI_API_KEY:
        prompt = (
            _build_google_policy_repair_prompt(
                content_type, topic, direction, prior_policy_rejection
            )
            if policy_repair
            else _build_openai_image_prompt(content_type, topic, direction)
        )
        openai_stage = (
            ImagePolicyStage.OPENAI_REPAIR
            if policy_repair
            else ImagePolicyStage.OPENAI_PRIMARY
        )
        try:
            url = await loop.run_in_executor(
                None,
                lambda: _openai_generate_and_upload(
                    prompt,
                    hospital_name,
                    expected_topic=topic,
                    counter=attempts,
                ),
            )
            if url:
                await _settle_image_reservations(
                    attempts,
                    image_receipt=decision.receipt,
                    review_receipt=review_decision.receipt,
                )
                return url, prompt
        except ImagePolicyUnavailableError as e:
            if diagnostics is not None:
                diagnostics["reason"] = "POLICY_UNAVAILABLE"
                diagnostics["stage"] = openai_stage.value
            logger.error("Image policy review unavailable: %s", e)
            await _settle_image_reservations(
                attempts,
                image_receipt=decision.receipt,
                review_receipt=review_decision.receipt,
            )
            return ("", "")
        except ImagePolicyRejectedError as exc:
            _record_policy_rejection(
                diagnostics,
                exc,
                stage=openai_stage,
                prompt_version=(
                    IMAGE_POLICY_REPAIR_PROMPT_VERSION
                    if policy_repair
                    else "openai-primary-v1"
                ),
            )
            logger.warning("Generated OpenAI image failed semantic policy review")
            await _settle_image_reservations(
                attempts,
                image_receipt=decision.receipt,
                review_receipt=review_decision.receipt,
            )
            return ("", "")
        except Exception as e:  # noqa: BLE001 — gpt-image-2 불가 시 Google 경로로 폴백
            logger.error("gpt-image-2 path failed, falling back to Google image: %s", e)
        finally:
            await _record_image_calls(attempts, hospital_id)

    # ── Vertex AI Gemini image (기본 또는 폴백) ──
    if not settings.GCP_PROJECT_ID:
        logger.warning("No usable image provider (OPENAI_API_KEY/GCP_PROJECT_ID) — skipping")
        await _settle_image_reservations(
            attempts,
            image_receipt=decision.receipt,
            review_receipt=review_decision.receipt,
        )
        return ("", "")

    prompt = (
        _build_google_policy_repair_prompt(
            content_type, topic, direction, prior_policy_rejection
        )
        if policy_repair
        else _build_google_image_prompt(content_type, topic, direction)
    )
    google_stage = (
        ImagePolicyStage.GOOGLE_REPAIR
        if policy_repair
        else ImagePolicyStage.GOOGLE_PRIMARY
    )
    fallback_attempts = _CallCounter()
    try:
        url = await loop.run_in_executor(
            None,
            lambda: _generate_and_upload(
                prompt,
                hospital_name,
                expected_topic=topic,
                counter=fallback_attempts,
            ),
        )
        if not url and diagnostics is not None:
            diagnostics["reason"] = "PROVIDER_EMPTY"
            diagnostics["stage"] = google_stage.value
        return url, prompt
    except ImagePolicyUnavailableError as e:
        if diagnostics is not None:
            diagnostics["reason"] = "POLICY_UNAVAILABLE"
            diagnostics["stage"] = google_stage.value
        logger.error("Image policy review unavailable: %s", e)
        return ("", "")
    except ImagePolicyRejectedError as exc:
        _record_policy_rejection(
            diagnostics,
            exc,
            stage=google_stage,
            prompt_version=(
                IMAGE_POLICY_REPAIR_PROMPT_VERSION
                if policy_repair
                else "google-primary-v1"
            ),
        )
        logger.warning("Generated Google image failed semantic policy review")
        return ("", "")
    except Exception as e:  # noqa: BLE001
        primary_failure = (
            "IMAGE_SAFETY"
            if isinstance(e, ImageSafetyBlockedError) or _looks_like_policy_block(e)
            else "PROVIDER_ERROR"
        )
        if diagnostics is not None:
            diagnostics["reason"] = primary_failure
            diagnostics["stage"] = google_stage.value
        if policy_repair:
            logger.warning("Google policy repair image failed: %s", type(e).__name__)
            return ("", "")
        logger.warning("Google topic image failed; trying safety-neutral fallback: %s", e)
        fallback_prompt = _build_google_safety_fallback_prompt(
            content_type, topic, direction
        )
        try:
            url = await loop.run_in_executor(
                None,
                lambda: _generate_and_upload(
                    fallback_prompt,
                    hospital_name,
                    expected_topic=topic,
                    counter=fallback_attempts,
                ),
            )
            if not url and diagnostics is not None:
                diagnostics["reason"] = "PROVIDER_EMPTY"
                diagnostics["stage"] = ImagePolicyStage.GOOGLE_FALLBACK.value
            return url, fallback_prompt
        except ImagePolicyRejectedError as fallback_exc:
            _record_policy_rejection(
                diagnostics,
                fallback_exc,
                stage=ImagePolicyStage.GOOGLE_FALLBACK,
                prompt_version=IMAGE_POLICY_FALLBACK_PROMPT_VERSION,
                prior_failure=primary_failure,
            )
            logger.warning("Google topical fallback failed semantic policy review")
            return ("", "")
        except ImagePolicyUnavailableError as fallback_exc:
            if diagnostics is not None:
                diagnostics["reason"] = "POLICY_UNAVAILABLE"
                diagnostics["stage"] = ImagePolicyStage.GOOGLE_FALLBACK.value
            logger.error("Google image fallback policy review unavailable: %s", fallback_exc)
            return ("", "")
        except ImageSafetyBlockedError as fallback_exc:
            if diagnostics is not None:
                diagnostics["reason"] = "IMAGE_SAFETY"
                diagnostics["stage"] = ImagePolicyStage.GOOGLE_FALLBACK.value
            logger.warning("Google topical fallback blocked by image safety: %s", fallback_exc)
            return ("", "")
        except Exception as fallback_exc:  # noqa: BLE001
            if diagnostics is not None:
                diagnostics["reason"] = "PROVIDER_ERROR"
                diagnostics["stage"] = ImagePolicyStage.GOOGLE_FALLBACK.value
            logger.error("Google image fallback failed: %s", fallback_exc)
            return ("", "")
    finally:
        await _record_image_calls(fallback_attempts, hospital_id)
        await _settle_image_reservations(
            attempts,
            fallback_attempts,
            image_receipt=decision.receipt,
            review_receipt=review_decision.receipt,
        )


def _download_stored_image(image_url: str) -> bytes:
    if not image_url.startswith("gs://"):
        raise ImagePolicyUnavailableError("stored image is not a managed GCS object")
    bucket_name, separator, blob_name = image_url.removeprefix("gs://").partition("/")
    if not separator or not bucket_name or not blob_name:
        raise ImagePolicyUnavailableError("stored image reference is invalid")
    from app.services.gcs_utils import _get_gcs_client

    return _get_gcs_client().bucket(bucket_name).blob(blob_name).download_as_bytes()


async def certify_existing_image_artifact(
    image_url: str,
    *,
    content_type: ContentType,
    topic: str | None,
    hospital_id: uuid.UUID | str | None = None,
) -> CertifiedImageArtifact:
    """Review stored bytes and return the exact artifact that was certified."""

    import asyncio

    from app.services import cost_guard

    decision = await cost_guard.reserve("content")
    if not decision.allowed:
        raise ImagePolicyUnavailableError("image policy review cost capacity unavailable")
    counter = _CallCounter()
    try:
        image_bytes = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _download_stored_image(image_url)
        )
        prompt = _build_google_image_prompt(content_type, topic)
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _validate_generated_image(
                image_bytes,
                mime_type="image/png",
                prompt=prompt,
                expected_topic=topic,
                counter=counter,
            ),
        )
        return CertifiedImageArtifact(
            image_bytes=image_bytes,
            content_hash=hashlib.sha256(image_bytes).hexdigest(),
            subject_hash=image_subject_hash(content_type, topic),
        )
    finally:
        await _record_image_calls(counter, hospital_id)
        if counter.review_count == 0:
            await cost_guard.settle_reservation(decision.receipt, consumed_units=0)
        else:
            await cost_guard.settle_reservation(decision.receipt, consumed_units=1)


async def certify_existing_image(
    image_url: str,
    *,
    content_type: ContentType,
    topic: str | None,
    hospital_id: uuid.UUID | str | None = None,
) -> tuple[str, str]:
    """Compatibility wrapper for callers that do not need the reviewed bytes."""

    artifact = await certify_existing_image_artifact(
        image_url,
        content_type=content_type,
        topic=topic,
        hospital_id=hospital_id,
    )
    return artifact.content_hash, artifact.subject_hash


def _upload_png_to_gcs(image_bytes: bytes, hospital_name: str) -> str:
    """PNG 바이트를 GCS content/{hospital}/{uuid}.png 로 업로드하고 gs:// 경로 반환."""
    from app.services.gcs_utils import _get_gcs_client

    gcs_client = _get_gcs_client()
    bucket = gcs_client.bucket(settings.GCP_STORAGE_BUCKET)
    content_hash = hashlib.sha256(image_bytes).hexdigest()
    filename = f"content/{hospital_name}/{content_hash}-{uuid.uuid4().hex}.png"
    blob = bucket.blob(filename)
    blob.upload_from_file(
        BytesIO(image_bytes),
        content_type="image/png",
        if_generation_match=0,
    )
    gcs_path = f"gs://{settings.GCP_STORAGE_BUCKET}/{filename}"
    logger.info("Image uploaded: %s", gcs_path)
    return gcs_path


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception(_is_transient_upload_error),
)
def _upload_verified_png(image_bytes: bytes, hospital_name: str) -> str:
    """Retry storage only; callers already paid for and verified these exact bytes."""

    return _upload_png_to_gcs(image_bytes, hospital_name)


def store_certified_image_bytes(image_bytes: bytes, hospital_name: str) -> str:
    """Copy already-reviewed bytes to a fresh immutable, hash-addressed object."""

    return _upload_verified_png(image_bytes, hospital_name)


def _validate_generated_image(
    image_bytes: bytes,
    *,
    mime_type: str,
    prompt: str,
    expected_topic: str | None,
    counter: _CallCounter | None = None,
) -> ImagePolicyAssessment:
    """Run one bounded multimodal review before any generated bytes are uploaded."""
    event: dict[str, object] | None = None
    if counter is not None:
        if settings.GCP_PROJECT_ID:
            event = counter.tick_review("google", settings.GEMINI_MODEL)
        else:
            event = counter.tick_review("openai", settings.OPENAI_MODEL_PARSE)
    rubric = (
        "Inspect this generated editorial image and return only the requested JSON policy "
        "assessment. A safe editorial metaphor counts as topic relevant when its objects clearly "
        "support the article topic; explicit anatomy or a literal procedure is never required. "
        f"The article topic is {expected_topic or prompt!r}. Text includes any readable letters, "
        "numbers, signage, or caption. Logo includes brand marks or watermarks. Recognizable "
        "people includes any identifiable face. Clinic impersonation is true only when the image "
        "implies an identifiable or named actual hospital, doctor, or patient encounter; a generic "
        "anonymous illustrative treatment room is allowed."
    )
    try:
        if settings.GCP_PROJECT_ID:
            from google.genai import types

            response = _get_google_client().models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=[rubric, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ImagePolicyAssessment,
                    temperature=0,
                    max_output_tokens=512,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            if event is not None:
                event["usage"] = getattr(response, "usage_metadata", None)
                event["provider_request_id"] = getattr(response, "response_id", None)
            response_text = response.text or ""
        elif settings.OPENAI_API_KEY:
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            response = _get_openai_client().with_options(
                timeout=60.0, max_retries=0
            ).chat.completions.create(
                model=settings.OPENAI_MODEL_PARSE,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": rubric},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "image_policy_assessment",
                        "strict": True,
                        "schema": ImagePolicyAssessment.model_json_schema(),
                    },
                },
                temperature=0,
                max_tokens=512,
            )
            if event is not None:
                event["usage"] = getattr(response, "usage", None)
                event["provider_request_id"] = getattr(response, "id", None)
            response_text = response.choices[0].message.content or ""
        else:
            raise ImagePolicyUnavailableError(
                "Google or OpenAI credentials are required for image policy review"
            )
        assessment = ImagePolicyAssessment.model_validate_json(response_text)
    except (ImportError, ValidationError) as exc:
        raise ImagePolicyUnavailableError("invalid image policy response") from exc
    except ImagePolicyUnavailableError:
        raise
    except Exception as exc:
        raise ImagePolicyUnavailableError("image policy review failed") from exc
    if not image_is_publishable(assessment):
        raise ImagePolicyRejectedError(assessment)
    return assessment


def _safe_google_visual_scene(
    topic: str,
    content_type: ContentType | None = None,
) -> str:
    """Map medical titles to non-sensitive, anonymous editorial still lifes.

    Raw titles such as pediatric fever or proctology terms can trigger an image
    model's safety stop even when the requested artwork is harmless.  They also
    encourage unwanted anatomy.  Keep topic-level variety without sending the
    diagnosis or body-region wording to the image model.
    """
    compact = "".join(topic.lower().split())
    if any(keyword in compact for keyword in ("발열", "탈수", "수분")):
        return "a clear glass of water and a folded cool cloth on a sunlit bedside table"
    if any(keyword in compact for keyword in ("유방초음파", "초음파")):
        return "an unpowered ultrasound probe, a plain gel bowl, and a folded neutral towel"
    if any(keyword in compact for keyword in ("건강검진", "검진")):
        return "a stethoscope, plain wooden blocks, and a folded cloth on a bright desk"
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
        return (
            "a clear glass of water, a bowl of leafy vegetables and whole grains, "
            "a neutral seat cushion, and a folded towel"
        )
    if any(
        keyword in compact
        for keyword in (
            "손목",
            "손가락",
            "팔꿈치",
            "어깨",
            "무릎",
            "관절",
            "연골",
            "척추",
            "허리",
            "목디스크",
            "재활",
            "도수",
            "물리치료",
            "스트레칭",
        )
    ):
        return "a plain resistance band, a cork therapy ball, and a folded exercise towel"
    if any(keyword in compact for keyword in ("예방접종", "백신", "접종")):
        return "a plain adhesive bandage, a cotton pad, and a soft reusable cool pack"
    if any(keyword in compact for keyword in ("감기", "기침", "비염", "호흡", "천식")):
        return "a warm ceramic steam bowl, a folded scarf, and a clear glass of water"
    if any(keyword in compact for keyword in ("피부", "아토피", "여드름", "습진")):
        return "an aloe leaf, a plain ceramic lotion jar, and a soft cotton cloth"
    if any(keyword in compact for keyword in ("당뇨", "혈압", "콜레스테롤")):
        return "a bowl of whole grains, leafy vegetables, and unbranded walking shoes"
    default_scenes = {
        ContentType.TREATMENT: (
            "a folded care towel, a cork therapy ball, and a plain ceramic bowl"
        ),
        ContentType.HEALTH: (
            "leafy vegetables, a clear water glass, and unbranded walking shoes"
        ),
        ContentType.LOCAL: (
            "a welcoming doorway, a small planter, and a sunlit pedestrian path without signs"
        ),
        ContentType.NOTICE: (
            "layered blank paper, a plain wooden tray, and one muted gold circle"
        ),
    }
    return default_scenes.get(
        content_type,
        "a clear water glass, a folded care towel, and a small green plant",
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=15),
    retry=retry_if_exception(_is_transient_openai_error),
)
def _openai_generate_verified_bytes(
    prompt: str,
    *,
    expected_topic: str | None = None,
    counter: _CallCounter | None = None,
) -> bytes:
    """Generate and verify one OpenAI candidate, retrying provider work only."""
    # tenacity 재시도마다 본문이 다시 실행된다 — 시도 1회 = 유료 호출 1회.
    event = counter.tick("openai", settings.OPENAI_IMAGE_MODEL) if counter is not None else None
    try:
        client = _get_openai_client()
        # response_format은 gpt-image 계열에서 기본 b64_json이며 일부 버전이 명시 전달을
        # 거부하므로 전달하지 않는다(기본값 사용).
        result = client.images.generate(
            model=settings.OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size=settings.OPENAI_IMAGE_SIZE,
            quality=settings.OPENAI_IMAGE_QUALITY,
            n=1,
        )
        if event is not None:
            event["usage"] = getattr(result, "usage", None)
            event["provider_request_id"] = getattr(result, "id", None)
        if not result.data:
            raise ValueError("gpt-image-2 returned no data")
        b64 = result.data[0].b64_json
        if not b64:
            raise ValueError("gpt-image-2 returned no b64_json payload")
        image_bytes = base64.b64decode(b64, validate=True)
        if event is not None:
            event["image_units"] = 1
        _validate_generated_image(
            image_bytes,
            mime_type="image/png",
            prompt=prompt,
            expected_topic=expected_topic,
            counter=counter,
        )
        return image_bytes
    except ImportError:
        logger.error("openai SDK not installed")
        return b""
    except Exception as e:
        logger.error("gpt-image-2 generation failed: %s", e)
        raise


def _openai_generate_and_upload(
    prompt: str,
    hospital_name: str,
    *,
    expected_topic: str | None = None,
    counter: _CallCounter | None = None,
) -> str:
    image_bytes = _openai_generate_verified_bytes(
        prompt, expected_topic=expected_topic, counter=counter
    )
    return _upload_verified_png(image_bytes, hospital_name) if image_bytes else ""


_openai_generate_and_upload.retry = _openai_generate_verified_bytes.retry


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=15),
    retry=retry_if_exception(_is_transient_google_image_error),
)
def _google_generate_verified_bytes(
    prompt: str,
    *,
    expected_topic: str | None = None,
    counter: _CallCounter | None = None,
) -> bytes:
    """Generate and verify one Google candidate, retrying provider work only.

    안전/정책 차단은 재시도하지 않고 즉시 raise → 호출부가 안전 폴백 프롬프트로 넘어간다.
    """
    event = counter.tick("google", settings.GOOGLE_IMAGE_MODEL) if counter is not None else None
    try:
        from google.genai import types

        client = _get_google_client()
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
        if event is not None:
            event["usage"] = getattr(response, "usage_metadata", None)
            event["provider_request_id"] = getattr(response, "response_id", None)
        parts = (
            response.candidates[0].content.parts
            if response.candidates and response.candidates[0].content
            else []
        ) or []
        image_part = next(
            (
                part.inline_data
                for part in parts
                if part.inline_data and part.inline_data.data
            ),
            None,
        )
        if not image_part:
            finish_reasons = [
                str(getattr(candidate, "finish_reason", None))
                for candidate in (response.candidates or [])
            ]
            message = (
                "Google image model returned no image payload "
                f"(finish_reasons={finish_reasons})"
            )
            prompt_feedback = getattr(response, "prompt_feedback", None)
            if _looks_like_policy_block(finish_reasons) or _looks_like_policy_block(
                getattr(prompt_feedback, "block_reason", "")
            ):
                raise ImageSafetyBlockedError(message)
            raise ValueError(message)
        image_bytes = image_part.data
        if event is not None:
            event["image_units"] = 1
        _validate_generated_image(
            image_bytes,
            mime_type=getattr(image_part, "mime_type", None) or "image/png",
            prompt=prompt,
            expected_topic=expected_topic,
            counter=counter,
        )
        return image_bytes

    except ImportError:
        logger.error("Google Gen AI or GCS SDK not installed")
        return b""
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        raise


def _generate_and_upload(
    prompt: str,
    hospital_name: str,
    *,
    expected_topic: str | None = None,
    counter: _CallCounter | None = None,
) -> str:
    image_bytes = _google_generate_verified_bytes(
        prompt, expected_topic=expected_topic, counter=counter
    )
    return _upload_verified_png(image_bytes, hospital_name) if image_bytes else ""


_generate_and_upload.retry = _google_generate_verified_bytes.retry
