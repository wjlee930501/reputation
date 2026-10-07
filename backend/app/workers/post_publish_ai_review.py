"""공개된 글의 사후 표본을 독립 AI 검수로 자동 처리하는 하루 한 번의 스윕.

사후 검수 표본(`post_publish_review_policy`)은 발행을 이미 통과한 글의 관찰용 확인이다.
소비자가 없던 시기에는 운영 DB의 공개 글 대부분이 미확인으로 쌓였고 월간 보고서에
'필수 사후검수 N건' 경고가 영구히 남았다. 사람이 버튼을 누르는 수동 경로만 있었기 때문이다.

설계 규칙:

- 생성 경로와 **같은** 독립 검수(`review_generated_content`, 비용 카테고리 `content`)를 쓴다.
  PASS(차단 지적 없음)가 지금 본문 해시에 묶여 나오면 `post_publish_reviewed_by='system:ai-review'`로
  확인 완료로 기록한다.
- 차단 지적(HARD/UNCERTAIN)이 나와도 **자동으로 내리지 않는다**. 공개 글을 숨기는 것은 사람이
  정한다. 이 글의 `ai_review`는 건드리지 않고(저장하면 공개 가시성이 그 글을 숨긴다) 별도 키
  `post_publish_ai_review`에 `FLAGGED`와 지적 문구를 남기고, 글 하나당 운영자 인시던트 하나를
  연다. 같은 본문을 매일 다시 사지 않도록 FLAGGED 글은 스윕이 고르지 않는다 — 사람이 본문을
  고치면(PATCH가 이 키를 지운다) 새 본문으로 다시 검수한다.
- 검수 불가(공급자 장애·비용 가드)는 아무것도 바꾸지 않는다. 다음 실행이 다시 집는다.
- 하루 상한(`POST_PUBLISH_AI_REVIEW_DAILY_CAP`)이 곧 실행당 상한이다(하루 한 번 실행). 편집된 글을
  먼저, 그다음 오래된 글부터 처리해 백로그를 며칠에 걸쳐 비운다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from celery import current_task
from sqlalchemy import case, select
from sqlalchemy.orm import joinedload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_sha256,
    review_generated_content,
)
from app.services.incident_types import IncidentFingerprint
from app.services.post_publish_review_policy import (
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)
from app.workers.dispatch_auth import require_dispatch

logger = logging.getLogger(__name__)

POST_PUBLISH_AI_REVIEW_PURPOSE = "post-publish-ai-review"
POST_PUBLISH_AI_REVIEWER = "system:ai-review"
POST_PUBLISH_FLAG_KEY = "post_publish_ai_review"
POST_PUBLISH_FLAGGED = "FLAGGED"

POST_PUBLISH_INCIDENT_PIPELINE = "post_publish_ai_review"
POST_PUBLISH_INCIDENT_OBJECT = "content_item"
POST_PUBLISH_INCIDENT_TYPE = "POST_PUBLISH_REVIEW_FLAGGED"

# 공급자가 연달아 이만큼 못 보면 오늘은 접는다 — 장애 중에 상한만큼 두드리지 않는다.
_CONSECUTIVE_UNAVAILABLE_LIMIT = 3
# 철학(승인 기준)이 없는 병원처럼 검수를 시작도 못 하는 행을 훑는 상한 배수.
_SCAN_MULTIPLIER = 5


def _candidate_content(item: ContentItem) -> dict[str, Any]:
    """생성 경로의 저장 본문 재검수와 같은 필드 집합."""

    return {
        field: getattr(item, field, None)
        for field in (
            "title",
            "body",
            "meta_description",
            "faq_question",
            "faq_answer_summary",
            "references_list",
        )
    }


def _review_stmt(limit: int):
    """미확인 표본 글. 편집된 글 먼저, 그다음 오래 공개된 글부터."""

    edited_first = case((ContentItem.body_updated_at > ContentItem.published_at, 0), else_=1)
    flag_status = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]["status"].as_string()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            human_post_publish_review_predicate(),
            publicly_operational_hospital_predicate(),
            ContentItem.body.is_not(None),
            flag_status.is_distinct_from(POST_PUBLISH_FLAGGED),
        )
        .order_by(edited_first, ContentItem.published_at, ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def _withheld_stmt(limit: int):
    """PATCH로 검수가 낡아진 비공개(보존) 글 — restore 전에 현재 본문의 검수가 필요하다."""

    edited = ContentItem.essence_check_summary["ai_review"]["edited_after_review"].as_boolean()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(ContentItem.status == ContentStatus.WITHHELD, edited.is_(True))
        .order_by(ContentItem.body_updated_at, ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def _flag_payload(review: ContentAiReview, reviewed_hash: str, now: datetime) -> dict[str, Any]:
    return {
        "status": POST_PUBLISH_FLAGGED,
        "candidate_sha256": reviewed_hash,
        "checked_at": now.isoformat(),
        "findings": [
            finding.message for finding in review.blocking_findings if finding.message
        ][:5],
    }


def apply_review_outcome(
    item: ContentItem, review: ContentAiReview, *, now: datetime
) -> str:
    """검수 결과를 잠근 행에 적용한다. `REVIEWED`·`FLAGGED`·`UNAVAILABLE`·`SKIPPED`를 돌려준다.

    호출부가 행을 `FOR UPDATE`로 다시 읽은 직후에 부른다. 그 사이 본문이 바뀌었거나(해시 불일치)
    사람이 이미 확인했거나 글이 내려갔으면 결과를 쓰지 않는다 — 늦게 온 응답이 편집을 덮지 않는다.
    """

    if review.status == ContentAiReviewStatus.UNAVAILABLE:
        return "UNAVAILABLE"
    if item.status == ContentStatus.WITHHELD:
        # 비공개(보존) 글: 야간 생성 스윕은 이 상태를 비켜 가므로(보존 본문을 다시 쓰지 않는다)
        # PATCH로 낡아진 검수는 여기서만 현재 해시로 되돌린다. restore가 영영 막히지 않게 한다.
        # 차단 지적이면 그 검수를 그대로 저장해 restore 게이트가 계속 막는다. 사후 검수 표본이
        # 아니므로 확인 기록·인시던트는 남기지 않는다.
        if review.candidate_sha256 != candidate_sha256(item):
            return "SKIPPED"
        summary = dict(item.essence_check_summary) if isinstance(item.essence_check_summary, dict) else {}
        summary["ai_review"] = review.payload()
        item.essence_check_summary = summary
        return "WITHHELD_REVIEWED"
    if (
        item.status != ContentStatus.PUBLISHED
        or item.post_publish_reviewed_at is not None
        or review.candidate_sha256 != candidate_sha256(item)
    ):
        return "SKIPPED"
    summary = dict(item.essence_check_summary) if isinstance(item.essence_check_summary, dict) else {}
    if review.blocking_findings:
        summary[POST_PUBLISH_FLAG_KEY] = _flag_payload(review, review.candidate_sha256, now)
        item.essence_check_summary = summary
        return "FLAGGED"
    # 차단 지적이 없는 PASS(비차단 SOFT 포함). 현재 본문에 묶인 검수라 `ai_review`도 갱신해
    # 편집으로 낡은 PASS를 현재 해시로 되돌린다 — 이후 되돌림·재발행 게이트가 같은 기록을 본다.
    summary["ai_review"] = review.payload()
    summary.pop(POST_PUBLISH_FLAG_KEY, None)
    item.essence_check_summary = summary
    item.post_publish_reviewed_at = now
    item.post_publish_reviewed_by = POST_PUBLISH_AI_REVIEWER
    return "REVIEWED"


def _open_flag_incident(item: ContentItem, hospital: Hospital, run_async) -> None:
    from app.services.ops_incident_alerts import open_ops_incident

    findings = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY, {}).get("findings") or []
    detail = f" 지적: {findings[0]}" if findings else ""
    run_async(
        open_ops_incident(
            pipeline=POST_PUBLISH_INCIDENT_PIPELINE,
            object_type=POST_PUBLISH_INCIDENT_OBJECT,
            object_id=str(item.id),
            incident_type=POST_PUBLISH_INCIDENT_TYPE,
            safe_error_code=POST_PUBLISH_INCIDENT_TYPE,
            problem=f"공개된 글 「{item.title}」의 자동 사후 검수에서 확인이 필요한 지적이 나왔습니다.{detail}",
            customer_impact="글은 그대로 공개되어 있습니다. 자동으로 내리지 않았습니다.",
            next_action=(
                "콘텐츠 탭에서 해당 글을 확인하고, 문제가 있으면 고치거나 비공개로 돌려 주세요. "
                "문제가 없으면 공개 내용 확인을 기록해 주세요."
            ),
            source_type="POST_PUBLISH_AI_REVIEW",
            hospital_name=hospital.name,
            hospital_id=hospital.id,
            admin_path=f"/hospitals/{hospital.id}/content",
            fingerprint=IncidentFingerprint.SAFETY_BLOCKED,
            actor=POST_PUBLISH_AI_REVIEWER,
        )
    )


@celery_app.task(
    name="app.workers.post_publish_ai_review.review_post_publish_samples",
    soft_time_limit=1500,
    time_limit=1800,
)
def review_post_publish_samples() -> dict[str, int]:
    """미확인 사후 표본을 하루 상한 안에서 독립 검수한다."""

    require_dispatch(current_task, POST_PUBLISH_AI_REVIEW_PURPOSE)
    cap = int(settings.POST_PUBLISH_AI_REVIEW_DAILY_CAP)
    counts = {
        "reviewed": 0,
        "flagged": 0,
        "unavailable": 0,
        "skipped": 0,
        "withheld_reviewed": 0,
    }
    if cap <= 0:
        return counts
    # 순환 import 회피: 워커 태스크 모듈이 이 모듈과 같은 앱을 공유한다.
    from app.workers.tasks import _generation_philosophy_sync, _run_async

    philosophies: dict[uuid.UUID, Any] = {}
    attempted = consecutive_unavailable = 0
    with SyncSessionLocal() as db:
        # 비공개 글(드물다)을 먼저 — restore가 막혀 있는 글이라 표본보다 급하다.
        candidates = list(db.execute(_withheld_stmt(cap)).unique().scalars().all())
        candidates += list(
            db.execute(_review_stmt(cap * _SCAN_MULTIPLIER)).unique().scalars().all()
        )
        for item in candidates:
            if attempted >= cap or consecutive_unavailable >= _CONSECUTIVE_UNAVAILABLE_LIMIT:
                break
            hospital = item.hospital
            if hospital.id not in philosophies:
                philosophies[hospital.id] = _generation_philosophy_sync(db, hospital.id)
            philosophy = philosophies[hospital.id]
            if philosophy is None:
                counts["skipped"] += 1
                continue
            attempted += 1
            item_id = item.id
            content = _candidate_content(item)
            try:
                review = _run_async(
                    review_generated_content(
                        hospital=hospital,
                        philosophy=philosophy,
                        content=content,
                        content_brief=getattr(item, "content_brief", None),
                        model=settings.POST_PUBLISH_AI_REVIEW_MODEL,
                    )
                )
            except Exception as error:  # noqa: BLE001 - 한 글의 실패가 하루 스윕을 멈추지 않는다.
                logger.warning(
                    "post-publish AI review failed for %s: %s", item_id, type(error).__name__
                )
                db.rollback()
                counts["unavailable"] += 1
                consecutive_unavailable += 1
                continue
            locked = db.execute(
                select(ContentItem)
                .where(ContentItem.id == item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if locked is None:
                db.rollback()
                counts["skipped"] += 1
                continue
            outcome = apply_review_outcome(locked, review, now=datetime.now(UTC))
            db.commit()
            consecutive_unavailable = consecutive_unavailable + 1 if outcome == "UNAVAILABLE" else 0
            counts[outcome.lower()] += 1
            if outcome == "FLAGGED":
                try:
                    _open_flag_incident(locked, hospital, _run_async)
                except Exception:  # noqa: BLE001 - 인시던트 기록 실패가 저장된 표시를 되돌리지 않는다.
                    logger.exception("post-publish flag incident failed for %s", item_id)
            elif outcome == "REVIEWED":
                _recover_flag_incident(locked, hospital, _run_async)
    logger.info("post-publish AI review sweep finished: %s", counts)
    return counts


def _recover_flag_incident(item: ContentItem, hospital: Hospital, run_async) -> None:
    """사람이 고친 글이 새 본문으로 PASS하면 그 글의 열린 인시던트를 닫는다."""

    from app.services.ops_incident_alerts import recover_ops_incident

    try:
        run_async(
            recover_ops_incident(
                pipeline=POST_PUBLISH_INCIDENT_PIPELINE,
                object_type=POST_PUBLISH_INCIDENT_OBJECT,
                object_id=str(item.id),
                fingerprint=IncidentFingerprint.SAFETY_BLOCKED,
                hospital_name=hospital.name,
                actor=POST_PUBLISH_AI_REVIEWER,
            )
        )
    except Exception:  # noqa: BLE001 - 닫기 실패는 다음 실행이 아닌 운영자 확인으로 남는다.
        logger.exception("post-publish flag incident recovery failed for %s", item.id)
