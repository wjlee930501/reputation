"""대표 이미지를 만들지 못한 글에 **같은 병원의 이미 인증된 이미지**를 빌려주는 경로.

왜 필요한가: 이미지 실패는 표본 실패다. 하룻밤 예산을 다 써도 본문은 완성돼 있고,
계약은 그 글의 발행을 기다린다. 그렇다고 이미지 없이 내보내지는 않는다 — 대신 그 병원이
이미 공개 중인 글의 인증된 이미지 하나를 빌려 발행하고, 사후 교체 스윕
(`workers/published_image_refresh`)이 그 글의 주제 이미지를 만들어 바꿔 단다.

계약:

- 합성 인증값을 만들지 않는다. 빌려온 행에는 원본의 내용 hash·주제 hash·정책 버전·검수
  시각을 **그대로** 옮기고 `image_reused_from_content_id`로 출처를 명시한다. 새 제목으로
  주제 hash를 다시 계산하면 아무도 검수하지 않은 값이 인증처럼 보이게 된다.
- 빌려줄 수 있는 원본은 지금 인증이 현재인 **공개 중(PUBLISHED)** 글뿐이다. 빌려온
  이미지는 다시 빌려주지 않는다(재사용의 연쇄로 한 장이 병원 전체로 번지지 않게 한다).
- 가장 오래 전에 인증된 이미지부터 빌린다. 최근 글일수록 사람 눈에 자주 보이므로 같은
  이미지가 이웃한 두 글에 나란히 붙는 상황을 피한다.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.services.content_publication import (
    HOSPITAL_FALLBACK_IMAGE_SOURCE,
    image_certification_current,
)
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    _download_stored_image,
    _validate_generated_image,
    image_content_hash_from_url,
    store_certified_image_bytes,
)
from app.services.image_policy import (
    ImagePolicyAssessment,
    ImagePolicyRejectedError,
    ImagePolicyUnavailableError,
)

logger = logging.getLogger(__name__)

# 생성 결과를 되쓸 수 있는 상태와 같다 — 공개 뒤의 행은 이 경로로 바꾸지 않는다.
REUSE_WRITE_BACK_STATUSES = (
    ContentStatus.DRAFT,
    ContentStatus.REJECTED,
    ContentStatus.READY,
)

# 한 번의 선택을 위해 읽는 후보 상한. 병원 하나의 공개 글 수는 수백 단위이므로
# 전량을 읽지 않고 오래된 순으로 필요한 만큼만 본다.
REUSE_CANDIDATE_SCAN_LIMIT = 50


def _candidate_stmt(
    hospital_id: uuid.UUID,
    exclude_item_id: uuid.UUID | None,
    *,
    content_type: Any | None = None,
):
    predicates = [
        ContentItem.hospital_id == hospital_id,
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.image_url.is_not(None),
        ContentItem.image_policy_verified_at.is_not(None),
        # 빌려온 이미지는 다시 빌려주지 않는다.
        ContentItem.image_reused_from_content_id.is_(None),
    ]
    if exclude_item_id is not None:
        predicates.append(ContentItem.id != exclude_item_id)
    if content_type is not None:
        predicates.append(ContentItem.content_type == content_type)
    return (
        select(ContentItem)
        .where(*predicates)
        .order_by(
            ContentItem.image_policy_verified_at.asc(),
            ContentItem.generated_at.asc(),
            ContentItem.id.asc(),
        )
        .limit(REUSE_CANDIDATE_SCAN_LIMIT)
    )


def apply_reused_image(
    db: Any,
    *,
    item: ContentItem,
    source: ContentItem,
    expected_revision: int | None = None,
    expected_claim_token: uuid.UUID | None = None,
) -> int:
    """빌려온 인증을 가드와 함께 쓴다. 반환값은 갱신된 행 수(0이면 호출부는 버린다).

    `content_revision`은 생성 write-back과 같은 규칙으로 다루지 않는다 — 이 경로는
    본문 후보를 바꾸지 않으므로 판을 올리지 않고, 대신 판이 그대로일 때만 쓴다.
    """

    predicates = [
        ContentItem.id == item.id,
        ContentItem.status.in_(REUSE_WRITE_BACK_STATUSES),
    ]
    if expected_revision is not None:
        predicates.append(ContentItem.content_revision == expected_revision)
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(
            image_url=source.image_url,
            # 프롬프트는 원본 글의 주제로 쓰인 것이다. 이 글의 것처럼 남기지 않는다.
            image_prompt=None,
            image_content_hash=source.image_content_hash,
            # 원본의 주제 결합을 그대로 들고 간다(합성값 금지). 결합 대상은 marker가 말한다.
            image_subject_hash=source.image_subject_hash,
            image_policy_version=source.image_policy_version,
            image_policy_verified_at=source.image_policy_verified_at,
            image_reused_from_content_id=source.id,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


# ── 유형 선호 ───────────────────────────────────────────────────────────
# 같은 유형의 이미지를 먼저 빌린다. FAQ 글에 지역 거리 그림을, LOCAL 글에 재활 도구
# 정물을 붙이는 것보다 같은 유형의 그림이 글과 덜 어긋난다. 유형 안에서는 종전과 같이
# 가장 오래 전에 인증된 것부터 빌린다. 같은 유형이 하나도 없으면 종전 동작 그대로
# 모든 유형에서 가장 오래된 것을 빌린다 — 선호는 자격을 좁히지 않는다.


def select_reusable_hospital_image(
    db: Any,
    hospital_id: uuid.UUID,
    exclude_item_id: uuid.UUID | None = None,
    *,
    content_type: Any | None = None,
) -> ContentItem | None:
    """빌려올 수 있는 인증 이미지를 가진 공개 글. 없으면 None.

    같은 `content_type` 후보를 먼저 보고(오래된 인증 순), 없으면 유형을 가리지 않고 다시
    오래된 순으로 본다. `content_type`을 주지 않으면 대상 글의 유형을 쓴다 — 호출부가
    유형을 따로 계산하지 않아도 선호가 적용되게 한다.

    SQL 조건은 후보를 좁히기만 한다 — 실제 통과 여부는 발행·공개와 **같은 함수**
    (`image_certification_current`)가 행 단위로 판정한다. 두 판정이 갈라지면 공개 게이트가
    거부할 이미지를 빌려주게 된다.
    """

    preferred_type = content_type
    if preferred_type is None and exclude_item_id is not None:
        target = db.get(ContentItem, exclude_item_id) if hasattr(db, "get") else None
        preferred_type = getattr(target, "content_type", None)
    seen: set[Any] = set()
    for candidate_type in (preferred_type, None):
        if candidate_type is None and preferred_type is None and seen:
            # 유형 선호가 없으면 같은 질의를 두 번 돌리지 않는다.
            break
        stmt = _candidate_stmt(hospital_id, exclude_item_id, content_type=candidate_type)
        for candidate in db.execute(stmt).scalars().all():
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            if image_certification_current(candidate):
                return candidate
    return None


# ── 병원 히어로 대체 이미지 ─────────────────────────────────────────────
# 첫 글을 쓰는 병원에는 빌려올 인증 이미지가 아직 없다. 그 글은 종전에
# `CONTENT_IMAGE_NOT_READY`로 막혔다. 마지막 수단으로 그 병원의 히어로 이미지를 쓴다.
#
# 정책 판정은 생성 이미지와 다르다 — 이건 그 병원이 스스로 고른 자기 자산이다.
#
# - 주제 적합성(`topic_relevant`)은 요구하지 않는다. 결합 대상이 글의 제목이 아니라 병원이다.
# - 로고(`has_logo`)는 허용한다. 자기 병원 히어로에 자기 로고가 있는 것은 사칭이 아니다.
#   같은 이유로 `impersonates_real_clinic`도 막지 않는다(그 병원 자신이다).
# - 글자(`has_text`)와 식별 가능한 인물(`has_recognizable_people`)은 그대로 막는다. 배너
#   문구가 박힌 이미지는 의료광고 표현 검사를 우회하는 통로가 되고, 인물 사진은 본인
#   동의 범위를 넘어 콘텐츠 카드로 재사용된다.
#
# 인증은 언제나 **저장한 바이트**에서 계산한다. 히어로 원본을 그대로 가리키지 않고
# content-addressed 불변 사본(`store_certified_image_bytes`)을 따로 만드는 이유는, 원본
# 히어로가 나중에 교체돼도 이미 발행된 글의 인증이 거짓이 되지 않게 하기 위해서다.

HOSPITAL_FALLBACK_MAX_BYTES = 12 * 1024 * 1024
_HERO_REVIEW_TOPIC = "the clinic's own hero photograph used as a generic cover image"


@dataclass(frozen=True, slots=True)
class HospitalFallbackImage:
    image_url: str
    content_hash: str
    policy_version: str
    verified_at: datetime
    source_url: str


def _hero_assessment_is_acceptable(assessment: ImagePolicyAssessment) -> bool:
    return not assessment.has_text and not assessment.has_recognizable_people


def stored_hospital_fallback_image(hospital: Any) -> HospitalFallbackImage | None:
    """병원 행에 캐시된 인증이 지금도 유효하면 그대로 쓴다.

    유효 조건은 공개 게이트와 같다: 저장 사본의 URL이 담은 내용 hash가 저장된 hash와 같고,
    정책 버전이 현재이며, 검수한 원본이 지금의 `hero_image_url`과 같아야 한다.
    """

    image_url = getattr(hospital, "fallback_image_url", None)
    content_hash = getattr(hospital, "fallback_image_content_hash", None)
    policy_version = getattr(hospital, "fallback_image_policy_version", None)
    verified_at = getattr(hospital, "fallback_image_verified_at", None)
    source_url = getattr(hospital, "fallback_image_source_url", None)
    hero_url = (getattr(hospital, "hero_image_url", None) or "").strip()
    if not (image_url and content_hash and verified_at and hero_url):
        return None
    if (source_url or "").strip() != hero_url:
        # 히어로가 바뀌었다. 옛 인증은 새 원본을 말해 주지 않는다 — 다시 검수한다.
        return None
    if policy_version != IMAGE_POLICY_VERSION:
        return None
    if image_content_hash_from_url(image_url) != content_hash:
        return None
    return HospitalFallbackImage(
        image_url=image_url,
        content_hash=content_hash,
        policy_version=policy_version,
        verified_at=verified_at,
        source_url=hero_url,
    )


def _fetch_hero_bytes(url: str) -> bytes:
    if url.startswith("gs://"):
        return _download_stored_image(url)
    if not url.startswith("https://"):
        raise ImagePolicyUnavailableError("hero image reference is not fetchable")
    import httpx

    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    payload = response.content
    if len(payload) > HOSPITAL_FALLBACK_MAX_BYTES:
        raise ImagePolicyUnavailableError("hero image is too large to certify")
    return payload


def _as_png_bytes(payload: bytes) -> bytes:
    """검수·저장 대상을 PNG 한 가지로 맞춘다 — 저장 content_type과 실제 바이트가 같아야 한다."""

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(payload)) as image:
        converted = image.convert("RGB")
        buffer = BytesIO()
        converted.save(buffer, format="PNG")
    return buffer.getvalue()


async def certify_hospital_fallback_image(
    db: Any, hospital: Any
) -> HospitalFallbackImage | None:
    """히어로 이미지를 대표 이미지 대체본으로 인증하고 병원 행에 캐시한다.

    필요할 때 한 번만 수행한다(lazy). 실패는 예외 없이 None이다 — 대체 이미지가 없다는 것은
    종전 동작(`CONTENT_IMAGE_NOT_READY`)으로 돌아간다는 뜻일 뿐, 발행을 깨뜨리지 않는다.
    """

    import asyncio

    from app.services import cost_guard
    from app.services.image_engine import _CallCounter, _record_image_calls

    cached = stored_hospital_fallback_image(hospital)
    if cached is not None:
        return cached
    hero_url = (getattr(hospital, "hero_image_url", None) or "").strip()
    if not hero_url:
        return None

    decision = await cost_guard.reserve("content")
    if not decision.allowed:
        return None
    counter = _CallCounter()
    consumed = 0
    try:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, lambda: _fetch_hero_bytes(hero_url))
        image_bytes = await loop.run_in_executor(None, lambda: _as_png_bytes(payload))

        def _review() -> ImagePolicyAssessment | None:
            try:
                return _validate_generated_image(
                    image_bytes,
                    mime_type="image/png",
                    prompt=_HERO_REVIEW_TOPIC,
                    expected_topic=_HERO_REVIEW_TOPIC,
                    counter=counter,
                )
            except ImagePolicyRejectedError as exc:
                # 생성 이미지 기준으로는 거절이지만, 히어로에는 더 좁은 안전 규칙만 적용한다.
                return exc.assessment

        assessment = await loop.run_in_executor(None, _review)
        consumed = min(counter.review_count, 1)
        if assessment is None or not _hero_assessment_is_acceptable(assessment):
            return None
        stored_url = await loop.run_in_executor(
            None,
            lambda: store_certified_image_bytes(
                image_bytes, str(getattr(hospital, "slug", "") or "hospital")
            ),
        )
    except Exception as error:  # noqa: BLE001 — 대체 이미지 실패는 종전 차단으로 돌아간다.
        consumed = min(counter.review_count, 1)
        logger.warning(
            "hospital fallback image certification failed for %s: %s",
            getattr(hospital, "id", None),
            type(error).__name__,
        )
        return None
    finally:
        await _record_image_calls(counter, getattr(hospital, "id", None))
        await cost_guard.settle_reservation(decision.receipt, consumed_units=consumed)

    content_hash = hashlib.sha256(image_bytes).hexdigest()
    verified_at = datetime.now(timezone.utc)
    db.execute(
        update(Hospital)
        .where(Hospital.id == hospital.id)
        .values(
            fallback_image_url=stored_url,
            fallback_image_source_url=hero_url,
            fallback_image_content_hash=content_hash,
            fallback_image_policy_version=IMAGE_POLICY_VERSION,
            fallback_image_verified_at=verified_at,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(hospital)
    return HospitalFallbackImage(
        image_url=stored_url,
        content_hash=content_hash,
        policy_version=IMAGE_POLICY_VERSION,
        verified_at=verified_at,
        source_url=hero_url,
    )


def apply_hospital_fallback_image(
    db: Any,
    *,
    item: ContentItem,
    fallback: HospitalFallbackImage,
    expected_revision: int | None = None,
    expected_claim_token: uuid.UUID | None = None,
) -> int:
    """병원 히어로 대체 인증을 글에 옮긴다. 반환값은 갱신된 행 수(0이면 호출부는 버린다).

    `image_subject_hash`는 **비운다** — 이 이미지는 이 글의 주제로 검수된 적이 없고, 새로
    계산해 넣으면 아무도 검수하지 않은 합성 인증값이 된다. 결합 대상은 marker가 말한다.
    """

    predicates = [
        ContentItem.id == item.id,
        ContentItem.status.in_(REUSE_WRITE_BACK_STATUSES),
    ]
    if expected_revision is not None:
        predicates.append(ContentItem.content_revision == expected_revision)
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(
            image_url=fallback.image_url,
            image_prompt=None,
            image_content_hash=fallback.content_hash,
            image_subject_hash=None,
            image_policy_version=fallback.policy_version,
            image_policy_verified_at=fallback.verified_at,
            image_reused_from_content_id=None,
            image_fallback_source=HOSPITAL_FALLBACK_IMAGE_SOURCE,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
