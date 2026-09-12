"""빌려온 대표 이미지를 그 글의 주제 이미지로 바꿔 다는 사후 스윕.

이미지 실패가 발행을 막지 않도록, 예산이 끝난 글은 같은 병원의 인증된 이미지를 빌려
발행된다(`services/content_image_reuse`). 빌린 상태는 정상이지만 최종 상태는 아니다 —
이 스윕이 01:20·04:20·07:20(KST)에 그 글의 주제로 새 이미지를 만들어 인증하고 marker를
푼다. 새 이미지를 만들지 못하면 아무것도 바꾸지 않는다: 빌린 인증은 그대로 유효하고,
공개 페이지는 계속 그림이 있는 글을 보여준다.

설계 규칙:

- 예산은 야간 스윕과 **공유**한다. 저장된 이미지 시도 기록이 아직 due가 아니면 건너뛴다
  (`generation_retry_policy.retry_is_due`). 이 스윕만의 별도 재시도 예산을 만들지 않는다.
- 실패는 시도 기록에만 남긴다. Slack도, 시도마다 인시던트를 여는 일도 없다 — 자동 복구가
  소유한 상태를 사람의 할 일로 만들지 않기 위해서다.
- `content_revision`은 올리지 않는다. 본문 후보가 바뀌지 않았기 때문이다.
- 공개 캐시 무효화는 커밋 뒤 best-effort다. 실패해도 저장을 되돌리지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from celery import current_task
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)
from app.workers.dispatch_auth import require_dispatch
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    read_generation_attempt,
)
from app.workers.generation_retry_policy import (
    environment_attempt_period,
    next_recovery_sweep,
    retry_class_for,
    retry_is_due,
    stored_attempt_period,
)
from app.workers.nightly_generation_batch import write_back_published_image

logger = logging.getLogger(__name__)

PUBLISHED_IMAGE_REFRESH_CAP = 50
PUBLISHED_IMAGE_REFRESH_PURPOSE = "refresh-reused-content-images"

_IMAGE_POLICY_REJECTION_CODE = "CONTENT_IMAGE_POLICY_REJECTED"
_IMAGE_FAILURE_CODE = "IMAGE_GENERATION_FAILED"


def _reused_image_stmt():
    """공개 중이고 이미지를 빌려온 글. 오래 빌린 것부터 돌려준다."""

    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.image_reused_from_content_id.is_not(None),
            Hospital.status == HospitalStatus.ACTIVE,
            Hospital.site_live.is_(True),
        )
        .order_by(ContentItem.published_at.asc(), ContentItem.sequence_no.asc())
        .options(joinedload(ContentItem.hospital))
        .limit(PUBLISHED_IMAGE_REFRESH_CAP)
    )


def _failure_code(diagnostics: dict[str, Any] | None) -> str:
    reason = str((diagnostics or {}).get("reason") or "").upper()
    if reason == "COST_BLOCKED":
        return "COST_BLOCKED"
    if reason in {"POLICY_REJECTED", "IMAGE_SAFETY"}:
        return _IMAGE_POLICY_REJECTION_CODE
    return _IMAGE_FAILURE_CODE


def remember_image_attempt(db, item: ContentItem, reason: str) -> dict[str, Any]:
    """야간 스윕과 같은 필드 이름으로 이 시도를 기록한다.

    `workers/tasks._remember_generation_attempt`와 같은 조각을 쓰되, 지문(context)은
    건드리지 않는다 — 이 스윕은 본문 입력을 바꾸지 않으므로 본문 지문의 주인이 아니다.
    """

    summary = getattr(item, "essence_check_summary", None)
    updated = dict(summary) if isinstance(summary, dict) else {}
    previous = read_generation_attempt(item)
    observed_at = datetime.now(UTC)
    attempt_period = environment_attempt_period(observed_at)
    same_period = stored_attempt_period(previous) == attempt_period
    try:
        provider_attempt_count = (
            int(previous.get("provider_attempt_count", previous.get("attempt_count")) or 0)
            if same_period
            else 0
        )
    except (TypeError, ValueError):
        provider_attempt_count = 0
    if reason != "COST_BLOCKED":
        # 비용 가드 보류는 공급자를 부른 적이 없다. 예산을 소모한 것으로 세지 않는다.
        provider_attempt_count += 1
    attempt = {
        "reason": reason,
        "observed_at": observed_at.isoformat(),
        "attempt_period": attempt_period,
        "retry_class": retry_class_for(reason).value,
        "attempt_count": provider_attempt_count,
        "provider_attempt_count": provider_attempt_count,
        "next_retry_at": next_recovery_sweep(observed_at).isoformat(),
    }
    context = previous.get("context")
    if isinstance(context, str):
        attempt["context"] = context
    updated[GENERATION_ATTEMPT_KEY] = attempt
    item.essence_check_summary = updated
    db.commit()
    return attempt


def _clear_image_attempt(db, item: ContentItem) -> None:
    summary = getattr(item, "essence_check_summary", None)
    if not isinstance(summary, dict) or GENERATION_ATTEMPT_KEY not in summary:
        return
    updated = dict(summary)
    updated.pop(GENERATION_ATTEMPT_KEY, None)
    updated.pop("image_reused", None)
    item.essence_check_summary = updated
    db.commit()


@celery_app.task(
    name="app.workers.published_image_refresh.refresh_reused_content_images",
    soft_time_limit=1500,
    time_limit=1800,
)
def refresh_reused_content_images() -> dict[str, int]:
    """빌린 이미지를 그 글의 주제 이미지로 교체한다. 실패는 다음 스윕으로 넘긴다."""

    require_dispatch(current_task, PUBLISHED_IMAGE_REFRESH_PURPOSE)
    # 순환 import 회피: 이미지 파이프라인과 사이트 재검증은 워커 태스크 모듈과 서로를 본다.
    from app.services.image_direction import hospital_image_direction
    from app.services.image_engine import generate_image
    from app.services.site_revalidate import trigger_content_site_revalidate_safe
    from app.workers.tasks import _run_async

    replaced = skipped = failed = 0
    revalidations: list[tuple[str, uuid.UUID, str | None, Any]] = []
    with SyncSessionLocal() as db:
        items = list(db.execute(_reused_image_stmt()).unique().scalars().all())
        for item in items:
            attempt = read_generation_attempt(item)
            if attempt and not retry_is_due(attempt):
                # 같은 글의 이미지 예산은 야간 스윕과 하나다. 아직 due가 아니면 사지 않는다.
                skipped += 1
                continue
            hospital = item.hospital
            if hospital is None:
                skipped += 1
                continue
            expected_title = item.title
            expected_revision = int(getattr(item, "content_revision", 1) or 1)
            diagnostics: dict[str, Any] = {}
            try:
                image_url, image_prompt = _run_async(
                    generate_image(
                        item.content_type,
                        hospital.slug,
                        topic=expected_title,
                        direction=hospital_image_direction(hospital),
                        hospital_id=hospital.id,
                        diagnostics=diagnostics,
                    )
                )
            except Exception as error:  # noqa: BLE001 - 공급자 실패는 다음 스윕이 이어받는다.
                logger.warning(
                    "reused image refresh failed for %s: %s", item.id, type(error).__name__
                )
                db.rollback()
                remember_image_attempt(db, item, _IMAGE_FAILURE_CODE)
                failed += 1
                continue
            if not image_url:
                # 비용 가드 보류·정책 거절·공급자 무응답. 빌린 이미지는 그대로 둔다.
                remember_image_attempt(db, item, _failure_code(diagnostics))
                failed += 1
                continue
            written = write_back_published_image(
                db,
                item_id=item.id,
                expected_title=expected_title,
                expected_revision=expected_revision,
                values={
                    "image_url": image_url,
                    "image_prompt": image_prompt,
                    "image_content_hash": image_content_hash_from_url(image_url),
                    "image_subject_hash": image_subject_hash(item.content_type, expected_title),
                    "image_policy_version": IMAGE_POLICY_VERSION,
                    "image_policy_verified_at": datetime.now(UTC),
                    # 이제 이 글 자신의 주제로 인증된 이미지다.
                    "image_reused_from_content_id": None,
                },
            )
            if written == 0:
                # 교체 중 제목·판이 바뀌었다. 그 편집이 자기 경로로 다시 요청한다.
                db.rollback()
                skipped += 1
                continue
            db.commit()
            db.refresh(item)
            _clear_image_attempt(db, item)
            replaced += 1
            revalidations.append(
                (hospital.slug, item.id, hospital.name, hospital.treatments)
            )
    for slug, item_id, hospital_name, treatments in revalidations:
        # 공개 이미지 URL은 인증된 내용 hash를 담으므로 교체와 함께 캐시 키가 바뀐다.
        # 재검증 실패는 이미 커밋된 교체를 되돌리지 않는다.
        _run_async(
            trigger_content_site_revalidate_safe(
                slug, item_id, hospital_name=hospital_name, treatments=treatments
            )
        )
    return {"replaced": replaced, "skipped": skipped, "failed": failed}
