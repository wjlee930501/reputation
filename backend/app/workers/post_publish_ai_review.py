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
- FLAGGED 글은 자동 교정한다(2026-10-08 대표 결정, `published_correction`). 지적 문장만 최소 교정 패스로
  고친 **메모리 안의 후보**를 독립 재검수하고, 그 후보 hash에 묶인 PASS일 때만 살아 있는 행에 CAS로
  쓴다. 아니면 글은 그대로이고 FLAGGED 표시·인시던트가 남으며(시도 기록과 함께) 글당 상한
  (`POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES`)을 넘기면 사람이 직접 고친다고 인시던트가 말한다.
  표시에 구조화 지적(인용)이 없는 옛 FLAGGED 글은 문구에서 인용을 짐작하지 않고 재검수로 구조를 얻는다.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from celery import current_task
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import joinedload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.schemas.content import PendingContentCandidate
from app.services import indexnow
from app.services import published_correction as pc
from app.services.audit_log import write_audit_log_sync
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_sha256,
    review_generated_content,
)
from app.services.content_candidate_publication import (
    CandidateApprovalApplied,
    parse_pending_candidate,
    pending_candidate_content,
    publish_pending_candidate_sync,
    reject_pending_candidate,
    stage_pending_candidate,
)
from app.services.content_minimal_correction import (
    CorrectionDependencies,
    CorrectionLimits,
    propose_sentence_corrections,
    published_correction_allowed_for,
    run_published_correction,
)
from app.services.incident_types import IncidentFingerprint
from app.services.must_use_verbatim import required_must_use_messages
from app.services.post_publish_review_policy import (
    human_post_publish_review_predicate,
    publicly_operational_hospital_predicate,
)
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.services.site_revalidate import trigger_content_site_revalidate_safe
from app.utils.db_locks import acquire_hospital_advisory_lock_sync
from app.workers.dispatch_auth import require_dispatch

logger = logging.getLogger(__name__)

POST_PUBLISH_AI_REVIEW_PURPOSE = "post-publish-ai-review"
POST_PUBLISH_AI_REVIEWER = "system:ai-review"
POST_PUBLISH_FLAG_KEY = pc.POST_PUBLISH_FLAG_KEY
POST_PUBLISH_FLAGGED = "FLAGGED"

POST_PUBLISH_INCIDENT_PIPELINE = "post_publish_ai_review"
POST_PUBLISH_INCIDENT_OBJECT = "content_item"
POST_PUBLISH_INCIDENT_TYPE = "POST_PUBLISH_REVIEW_FLAGGED"

# 공급자가 연달아 이만큼 못 보면 오늘은 접는다 — 장애 중에 상한만큼 두드리지 않는다.
_CONSECUTIVE_UNAVAILABLE_LIMIT = 3
# 철학(승인 기준)이 없는 병원처럼 검수를 시작도 못 하는 행을 훑는 상한 배수.
_SCAN_MULTIPLIER = 5
_STRUCTURED_FINDINGS_LIMIT = 20
# 실행 한 번이 새 글을 시작하지 않는 경과 시간(초). 태스크 soft limit보다 충분히 앞서 멈춘다.
_RUN_BUDGET_SECONDS = 2000
_FLAG_SCAN_MULTIPLIER = 5


def _candidate_content(item: ContentItem) -> dict[str, Any]:
    """생성 경로의 저장 본문 재검수와 같은 필드 집합."""

    pending = parse_pending_candidate(item)
    if pending is not None and pending.review is None:
        return pending_candidate_content(pending)
    return pc.candidate_content(item)


def _review_stmt(limit: int):
    """미확인 표본 글. 편집된 글 먼저, 그다음 오래 공개된 글부터."""

    edited_first = case((ContentItem.body_updated_at > ContentItem.published_at, 0), else_=1)
    flag_status = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]["status"].as_string()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            or_(
                human_post_publish_review_predicate(),
                ContentItem.pending_revision.is_not(None),
            ),
            publicly_operational_hospital_predicate(),
            ContentItem.body.is_not(None),
            flag_status.is_distinct_from(POST_PUBLISH_FLAGGED),
        )
        .order_by(edited_first, ContentItem.published_at, ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def _flagged_stmt(limit: int):
    """자동 교정 대상 FLAGGED 공개 글. 교정이 끝났다고 표시된 글은 SQL에서 거른다.

    글당 패스 상한은 표시 안의 `correction.passes`라 SQL이 아니라 호출부가 한 번 더 거른다.
    """

    flag = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.post_publish_reviewed_at.is_(None),
            ContentItem.body.is_not(None),
            publicly_operational_hospital_predicate(),
            flag["status"].as_string() == POST_PUBLISH_FLAGGED,
            or_(
                flag["correction"]["finished"].as_string().is_distinct_from("true"),
                # 예전 교정 규칙으로 돈을 쓰지 않고 사람에게 넘긴 글은 새 규칙으로 다시 본다
                # (`published_correction.CORRECTION_RULES_VERSION`과 같은 판정).
                and_(
                    func.coalesce(flag["correction"]["passes"].as_integer(), 0) == 0,
                    func.coalesce(flag["correction"]["rules_version"].as_integer(), 1)
                    < pc.CORRECTION_RULES_VERSION,
                ),
            ),
        )
        .order_by(ContentItem.published_at, ContentItem.id)
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


def _flag_payload(
    review: ContentAiReview,
    reviewed_hash: str,
    now: datetime,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": POST_PUBLISH_FLAGGED,
        "candidate_sha256": reviewed_hash,
        "checked_at": now.isoformat(),
        "findings": [finding.message for finding in review.blocking_findings if finding.message][
            :5
        ],
        # 자동 교정이 읽는 구조(severity·kind·target·quote·message). 문구 문자열만으로는 어느
        # 문장을 고칠지 알 수 없다.
        "structured_findings": [
            finding.payload() for finding in review.blocking_findings if finding.message
        ][:_STRUCTURED_FINDINGS_LIMIT],
    }
    # 같은 본문의 재검수(구조 확보)가 앞선 교정 시도 기록을 지우지 않는다.
    if isinstance(previous, dict) and previous.get("candidate_sha256") == reviewed_hash:
        state = pc.correction_state(previous)
        if state:
            payload["correction"] = state
    return payload


def apply_review_outcome(item: ContentItem, review: ContentAiReview, *, now: datetime) -> str:
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
        summary = (
            dict(item.essence_check_summary) if isinstance(item.essence_check_summary, dict) else {}
        )
        summary["ai_review"] = review.payload()
        item.essence_check_summary = summary
        return "WITHHELD_REVIEWED"
    if (
        item.status != ContentStatus.PUBLISHED
        or item.post_publish_reviewed_at is not None
        or review.candidate_sha256 != candidate_sha256(item)
    ):
        return "SKIPPED"
    summary = (
        dict(item.essence_check_summary) if isinstance(item.essence_check_summary, dict) else {}
    )
    if review.blocking_findings:
        summary[POST_PUBLISH_FLAG_KEY] = _flag_payload(
            review, review.candidate_sha256, now, summary.get(POST_PUBLISH_FLAG_KEY)
        )
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


_NEEDS_HUMAN_COPY = {
    "TITLE_FINDING": "제목에 지적이 있습니다. 제목은 자동으로 바꾸지 않습니다.",
    "NOT_CORRECTABLE": "지적이 문장 하나를 고치는 방식으로는 풀 수 없는 내용입니다.",
    "PARTLY_UNCORRECTABLE": "지적 일부는 문장 하나를 고치는 방식으로는 풀 수 없는 내용입니다.",
    "blocked": "자동으로 고친 본문이 독립 재검수를 통과하지 못했습니다.",
}
_NEEDS_HUMAN_DEFAULT_COPY = "자동 교정이 안전 검사를 통과하지 못했습니다."


def _open_flag_incident(
    item: ContentItem,
    hospital: Hospital,
    run_async,
    *,
    needs_human_reason: str | None = None,
) -> None:
    from app.services.ops_incident_alerts import open_ops_incident

    findings = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY, {}).get(
        "findings"
    ) or []
    detail = f" 지적: {findings[0]}" if findings else ""
    if needs_human_reason:
        # 자동 교정을 이미 시도했거나 시도할 수 없는 글 — 사람이 직접 고쳐야 한다고 분명히 말한다.
        copy = _NEEDS_HUMAN_COPY.get(needs_human_reason, _NEEDS_HUMAN_DEFAULT_COPY)
        problem = (
            f"공개된 글 「{item.title}」의 사후 검수 지적을 자동 교정으로 풀지 못했습니다. "
            f"{copy}{detail}"
        )
        next_action = (
            "콘텐츠 탭에서 해당 글을 직접 고치거나 비공개로 돌려 주세요. "
            "문제가 없다고 판단하면 공개 내용 확인을 기록해 주세요."
        )
    else:
        problem = f"공개된 글 「{item.title}」의 자동 사후 검수에서 확인이 필요한 지적이 나왔습니다.{detail}"
        next_action = (
            "콘텐츠 탭에서 해당 글을 확인하고, 문제가 있으면 고치거나 비공개로 돌려 주세요. "
            "문제가 없으면 공개 내용 확인을 기록해 주세요."
        )
    run_async(
        open_ops_incident(
            pipeline=POST_PUBLISH_INCIDENT_PIPELINE,
            object_type=POST_PUBLISH_INCIDENT_OBJECT,
            object_id=str(item.id),
            incident_type=POST_PUBLISH_INCIDENT_TYPE,
            safe_error_code=POST_PUBLISH_INCIDENT_TYPE,
            problem=problem,
            customer_impact="글은 그대로 공개되어 있습니다. 자동으로 내리지 않았습니다.",
            next_action=next_action,
            source_type="POST_PUBLISH_AI_REVIEW",
            hospital_name=hospital.name,
            hospital_id=hospital.id,
            admin_path=f"/hospitals/{hospital.id}/content",
            fingerprint=IncidentFingerprint.SAFETY_BLOCKED,
            actor=POST_PUBLISH_AI_REVIEWER,
            # 이미 열린 인시던트의 문구만 새로 쓴다 — 같은 글의 두 번째 Slack 알림을 만들지 않는다.
            notify=needs_human_reason is None,
        )
    )


def _lock_item(db, item_id) -> ContentItem | None:
    return db.execute(
        select(ContentItem)
        .where(ContentItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _review_and_apply(db, item, hospital, philosophy, run_async, now: datetime) -> str:
    """현재 공개 본문을 독립 검수해 결과를 잠근 행에 적용한다(편집된 본문이면 쓰지 않는다)."""

    item_id = item.id
    try:
        review = run_async(
            review_generated_content(
                hospital=hospital,
                philosophy=philosophy,
                content=_candidate_content(item),
                content_brief=getattr(item, "content_brief", None),
                model=settings.POST_PUBLISH_AI_REVIEW_MODEL,
            )
        )
    except Exception as error:  # noqa: BLE001 - 한 글의 실패가 하루 스윕을 멈추지 않는다.
        logger.warning("post-publish AI review failed for %s: %s", item_id, type(error).__name__)
        db.rollback()
        return "UNAVAILABLE"
    locked = _lock_item(db, item_id)
    if locked is None:
        db.rollback()
        return "SKIPPED"
    pending = parse_pending_candidate(locked)
    if pending is not None and pending.review is None:
        if review.status == ContentAiReviewStatus.UNAVAILABLE:
            db.rollback()
            return "UNAVAILABLE"
        if review.candidate_sha256 != pending.candidate_sha256:
            db.rollback()
            return "SKIPPED"
        if review.status != ContentAiReviewStatus.PASS or review.blocking_findings:
            reject_pending_candidate(
                locked,
                expected_candidate_sha256=pending.candidate_sha256,
                review_payload=review.payload(),
                reviewed_at=now,
            )
            db.commit()
            return "CANDIDATE_REJECTED"
        publication = publish_pending_candidate_sync(
            db,
            locked,
            review,
            philosophy=philosophy,
            approved_by=POST_PUBLISH_AI_REVIEWER,
            approved_at=now,
        )
        if not isinstance(publication, CandidateApprovalApplied):
            db.rollback()
            return "CONFLICT"
        write_audit_log_sync(
            db,
            action="content_candidate_published",
            hospital_id=hospital.id,
            actor=POST_PUBLISH_AI_REVIEWER,
            target_type="content_item",
            target_id=locked.id,
            detail={
                "candidate_sha256": publication.candidate_sha256,
                "active_revision_id": str(locked.active_revision_id),
            },
        )
        indexnow.enqueue_content_published_sync(
            db,
            slug=hospital.slug,
            content_id=locked.id,
            aeo_domain=hospital.aeo_domain,
            treatments=hospital.treatments,
            revision=str(locked.active_revision_id),
        )
        enqueue_public_surface_intent(db, hospital, content_ids=[locked.id])
        db.commit()
        try:
            run_async(
                trigger_content_site_revalidate_safe(
                    hospital.slug,
                    locked.id,
                    hospital_name=hospital.name,
                    treatments=hospital.treatments,
                )
            )
        except Exception:  # noqa: BLE001 - committed intent owns retry.
            logger.exception("candidate publication revalidation failed for %s", locked.id)
        return "CANDIDATE_PUBLISHED"
    outcome = apply_review_outcome(locked, review, now=now)
    db.commit()
    return outcome


def _record_failed_attempt(
    db, item_id, base_sha: str, outcome, *, reason: str, now: datetime
) -> ContentItem | None:
    """교정이 끝났음을 표시에 남긴다. 그 사이 표시가 바뀌었으면(사람이 고침) 아무것도 쓰지 않는다."""

    locked = _lock_item(db, item_id)
    summary = locked.essence_check_summary if locked is not None else None
    marker = summary.get(POST_PUBLISH_FLAG_KEY) if isinstance(summary, dict) else None
    if (
        locked is None
        or locked.status != ContentStatus.PUBLISHED
        or not isinstance(marker, dict)
        or marker.get("candidate_sha256") != base_sha
    ):
        db.rollback()
        return None
    locked.essence_check_summary = {
        **summary,
        POST_PUBLISH_FLAG_KEY: pc.marker_with_attempt(
            marker, outcome=outcome, finished=True, reason=reason, now=now
        ),
    }
    db.commit()
    return locked


def _correct_flagged(db, item, hospital, philosophy, run_async, now: datetime) -> str:
    """구조화 지적이 있는 FLAGGED 공개 글을 교정→재검수→CAS 쓰기로 푼다."""

    max_passes = int(settings.POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES)
    marker = item.essence_check_summary[POST_PUBLISH_FLAG_KEY]
    review_payload = pc.structured_review_from_marker(marker)
    base_sha = candidate_sha256(item)
    if review_payload is None or marker.get("candidate_sha256") != base_sha:
        return "SKIPPED"
    item_id, base_revision = item.id, int(getattr(item, "content_revision", 1) or 1)
    slug, treatments, aeo_domain = hospital.slug, hospital.treatments, hospital.aeo_domain
    hospital_id, hospital_name = hospital.id, hospital.name

    async def post_publish_review(**kwargs):
        return await review_generated_content(**kwargs, model=settings.POST_PUBLISH_AI_REVIEW_MODEL)

    try:
        outcome = run_async(
            run_published_correction(
                hospital=hospital,
                philosophy=philosophy,
                content=_candidate_content(item),
                review=review_payload,
                content_brief=getattr(item, "content_brief", None),
                must_use_messages=required_must_use_messages(philosophy),
                state=pc.correction_state(marker),
                limits=CorrectionLimits(max_passes=max_passes, max_rereviews=max_passes),
                dependencies=CorrectionDependencies(
                    propose=propose_sentence_corrections, review=post_publish_review
                ),
            )
        )
    except Exception as error:  # noqa: BLE001 - 한 글의 실패가 하루 스윕을 멈추지 않는다.
        logger.warning("post-publish correction failed for %s: %s", item_id, type(error).__name__)
        db.rollback()
        return "UNAVAILABLE"
    if outcome.status == "UNAVAILABLE":
        # 재검수를 끝내지 못했다. 시도로 세지 않고 아무것도 남기지 않아 다음 실행이 다시 집는다.
        db.rollback()
        return "UNAVAILABLE"

    result = outcome.status if outcome.status in {"NEEDS_HUMAN", "REJECTED"} else "BLOCKED"
    reason = outcome.reason or ("blocked" if result == "BLOCKED" else outcome.status.lower())
    if outcome.status == "PASS":
        # 같은 병원의 편집·발행과 순서를 맞추려고 병원 잠금 뒤에 행을 잠근다(관리자 PATCH와 같은 순서).
        acquire_hospital_advisory_lock_sync(db, hospital_id)
        locked = _lock_item(db, item_id)
        if locked is None:
            db.rollback()
            return "SKIPPED"
        candidate_row = SimpleNamespace(**vars(locked))
        status, apply_reason = pc.apply_published_correction(
            candidate_row,
            outcome,
            base_sha=base_sha,
            base_revision=base_revision,
            philosophy=philosophy,
            now=now,
        )
        if status == "CONFLICT":
            db.rollback()
            logger.info("Discarding post-publish correction for %s — row changed", item_id)
            return "CONFLICT"
        if status == "CORRECTED":
            if not hasattr(locked, "active_revision_id"):
                # Rolling expand compatibility. Current ORM rows always take the
                # candidate path; old worker objects finish under their old contract.
                status, apply_reason = pc.apply_published_correction(
                    locked,
                    outcome,
                    base_sha=base_sha,
                    base_revision=base_revision,
                    philosophy=philosophy,
                    now=now,
                )
                if status != "CORRECTED":
                    db.rollback()
                    return "CONFLICT"
            else:
                corrected = pc.candidate_content(candidate_row)
                staged = stage_pending_candidate(
                    locked,
                    expected_active_revision_id=locked.active_revision_id,
                    expected_content_revision=base_revision,
                    title=corrected["title"],
                    body=corrected["body"],
                    meta_description=corrected["meta_description"],
                    faq_question=corrected["faq_question"],
                    faq_answer_summary=corrected["faq_answer_summary"],
                    references_list=corrected["references_list"],
                    reference_checks=list(locked.reference_checks or []),
                    created_by=pc.POST_PUBLISH_CORRECTION_ACTOR,
                    created_at=now,
                )
                if not isinstance(staged, PendingContentCandidate):
                    db.rollback()
                    return "CONFLICT"
                publication = publish_pending_candidate_sync(
                    db,
                    locked,
                    outcome.review,
                    philosophy=philosophy,
                    approved_by=pc.POST_PUBLISH_CORRECTION_ACTOR,
                    approved_at=now,
                )
                if not isinstance(publication, CandidateApprovalApplied):
                    db.rollback()
                    return "CONFLICT"
                locked.essence_check_summary = candidate_row.essence_check_summary
            new_revision = int(locked.content_revision)
            write_audit_log_sync(
                db,
                action="post_publish_auto_correction",
                hospital_id=hospital_id,
                actor=pc.POST_PUBLISH_CORRECTION_ACTOR,
                target_type="content_item",
                target_id=item_id,
                detail={
                    "before_sha256": base_sha,
                    "after_sha256": candidate_sha256(locked),
                    "content_revision": new_revision,
                    "human_edited": locked.human_edited_at is not None,
                },
            )
            # 관리자 PATCH가 공개 글의 공개 텍스트를 바꿀 때와 같은 트랜잭션 패턴: 색인 intent와
            # 사이트 재검증 intent를 이 커밋에 함께 묶는다. 커밋이 롤백되면 intent도 없다.
            indexnow.enqueue_content_published_sync(
                db,
                slug=slug,
                content_id=item_id,
                aeo_domain=aeo_domain,
                treatments=treatments,
                revision=new_revision,
            )
            enqueue_public_surface_intent(db, hospital, content_ids=[item_id])
            db.commit()
            try:
                run_async(
                    trigger_content_site_revalidate_safe(
                        slug, item_id, hospital_name=hospital_name, treatments=treatments
                    )
                )
            except Exception:  # noqa: BLE001 - 커밋된 교정을 되돌리지 않는다(intent가 재시도한다).
                logger.exception("post-publish correction revalidation failed for %s", item_id)
            _recover_flag_incident(
                locked, hospital, run_async, actor=pc.POST_PUBLISH_CORRECTION_ACTOR
            )
            return "CORRECTED"
        db.rollback()
        result, reason = "REJECTED", apply_reason or "rejected"

    recorded = _record_failed_attempt(db, item_id, base_sha, outcome, reason=reason, now=now)
    if recorded is None:
        return "CONFLICT"
    try:
        _open_flag_incident(recorded, hospital, run_async, needs_human_reason=reason)
    except Exception:  # noqa: BLE001 - 인시던트 기록 실패가 저장된 표시를 되돌리지 않는다.
        logger.exception("post-publish flag incident failed for %s", item_id)
    return result


def process_flagged_post(
    db, item, hospital, philosophy, *, run_async, now: datetime | None = None
) -> str:
    """FLAGGED 공개 글 하나를 자동 교정한다.

    구조화 지적이 없는 옛 표시는 먼저 재검수로 구조를 얻는다(그 결과가 PASS이면 확인 완료로 닫는다).
    결과: ``CORRECTED``·``REVIEWED``·``NEEDS_HUMAN``·``BLOCKED``·``REJECTED``·``CONFLICT``·
    ``UNAVAILABLE``·``SKIPPED``.
    """

    now = now or datetime.now(UTC)
    summary = item.essence_check_summary if isinstance(item.essence_check_summary, dict) else {}
    marker = summary.get(POST_PUBLISH_FLAG_KEY)
    if (
        not published_correction_allowed_for(item)
        or not isinstance(marker, dict)
        or marker.get("status") != POST_PUBLISH_FLAGGED
        or item.post_publish_reviewed_at is not None
        or pc.correction_exhausted(
            marker, max_passes=int(settings.POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES)
        )
    ):
        return "SKIPPED"
    if pc.structured_review_from_marker(marker) is None:
        outcome = _review_and_apply(db, item, hospital, philosophy, run_async, now)
        if outcome == "REVIEWED":
            _recover_flag_incident(item, hospital, run_async)
            return "REVIEWED"
        if outcome != "FLAGGED":
            return "UNAVAILABLE" if outcome == "UNAVAILABLE" else "SKIPPED"
    return _correct_flagged(db, item, hospital, philosophy, run_async, now)


# 이 결과들은 인시던트를 이미 처리했다(교정으로 닫았거나 사람의 일 문구로 갱신했다).
_INCIDENT_HANDLED = frozenset({"CORRECTED", "REVIEWED", "NEEDS_HUMAN", "BLOCKED", "REJECTED"})


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


@celery_app.task(
    name="app.workers.post_publish_ai_review.review_post_publish_samples",
    soft_time_limit=2400,
    time_limit=2700,
)
def review_post_publish_samples() -> dict[str, int]:
    """FLAGGED 공개 글을 먼저 자동 교정하고, 미확인 사후 표본을 하루 상한 안에서 독립 검수한다."""

    require_dispatch(current_task, POST_PUBLISH_AI_REVIEW_PURPOSE)
    cap = int(settings.POST_PUBLISH_AI_REVIEW_DAILY_CAP)
    correction_cap = int(settings.POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP)
    max_passes = int(settings.POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES)
    counts = {
        "reviewed": 0,
        "flagged": 0,
        "unavailable": 0,
        "skipped": 0,
        "withheld_reviewed": 0,
        "corrected": 0,
        "needs_human": 0,
        "correction_blocked": 0,
    }
    if cap <= 0 and correction_cap <= 0:
        return counts
    # 순환 import 회피: 워커 태스크 모듈이 이 모듈과 같은 앱을 공유한다.
    from app.workers.tasks import _generation_philosophy_sync, _run_async

    started = time.monotonic()
    philosophies: dict[uuid.UUID, Any] = {}
    attempted = consecutive_unavailable = corrections = 0

    def out_of_budget() -> bool:
        return (
            consecutive_unavailable >= _CONSECUTIVE_UNAVAILABLE_LIMIT
            or time.monotonic() - started > _RUN_BUDGET_SECONDS
        )

    def tally(result: str) -> None:
        nonlocal consecutive_unavailable
        consecutive_unavailable = consecutive_unavailable + 1 if result == "UNAVAILABLE" else 0
        key = {
            "CORRECTED": "corrected",
            "NEEDS_HUMAN": "needs_human",
            "BLOCKED": "correction_blocked",
            "REJECTED": "correction_blocked",
            "REVIEWED": "reviewed",
            "UNAVAILABLE": "unavailable",
        }.get(result)
        if key:
            _bump(counts, key)

    with SyncSessionLocal() as db:
        # 1) 이미 FLAGGED인 공개 글 — 표본 검수보다 먼저 푼다(글을 고치는 일이 새 표본보다 급하다).
        if correction_cap > 0:
            flagged = list(
                db.execute(_flagged_stmt(correction_cap * _FLAG_SCAN_MULTIPLIER))
                .unique()
                .scalars()
                .all()
            )
            for item in flagged:
                if corrections >= correction_cap or out_of_budget():
                    break
                marker = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY)
                if pc.correction_exhausted(marker, max_passes=max_passes):
                    continue
                hospital = item.hospital
                if hospital.id not in philosophies:
                    philosophies[hospital.id] = _generation_philosophy_sync(db, hospital.id)
                if philosophies[hospital.id] is None:
                    counts["skipped"] += 1
                    continue
                result = process_flagged_post(
                    db, item, hospital, philosophies[hospital.id], run_async=_run_async
                )
                if result != "SKIPPED":
                    corrections += 1
                tally(result)
        if cap <= 0:
            logger.info("post-publish AI review sweep finished: %s", counts)
            return counts
        # 비공개 글(드물다)을 먼저 — restore가 막혀 있는 글이라 표본보다 급하다.
        candidates = list(db.execute(_withheld_stmt(cap)).unique().scalars().all())
        candidates += list(
            db.execute(_review_stmt(cap * _SCAN_MULTIPLIER)).unique().scalars().all()
        )
        for item in candidates:
            if attempted >= cap or out_of_budget():
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
            outcome = _review_and_apply(
                db, item, hospital, philosophy, _run_async, datetime.now(UTC)
            )
            consecutive_unavailable = consecutive_unavailable + 1 if outcome == "UNAVAILABLE" else 0
            _bump(counts, outcome.lower())
            if outcome == "FLAGGED":
                locked = db.get(ContentItem, item_id)
                result = "DEFERRED"
                if correction_cap > 0 and corrections < correction_cap and not out_of_budget():
                    result = process_flagged_post(
                        db, locked, hospital, philosophy, run_async=_run_async
                    )
                    if result != "SKIPPED":
                        corrections += 1
                    tally(result)
                if result not in _INCIDENT_HANDLED:
                    try:
                        _open_flag_incident(locked, hospital, _run_async)
                    except Exception:  # noqa: BLE001 - 인시던트 기록 실패가 저장된 표시를 되돌리지 않는다.
                        logger.exception("post-publish flag incident failed for %s", item_id)
            elif outcome == "REVIEWED":
                _recover_flag_incident(db.get(ContentItem, item_id), hospital, _run_async)
    logger.info("post-publish AI review sweep finished: %s", counts)
    return counts


def _recover_flag_incident(
    item: ContentItem,
    hospital: Hospital,
    run_async,
    *,
    actor: str = POST_PUBLISH_AI_REVIEWER,
) -> None:
    """고쳐졌거나 새 본문으로 PASS한 글의 열린 인시던트를 닫는다."""

    from app.services.ops_incident_alerts import recover_ops_incident

    try:
        run_async(
            recover_ops_incident(
                pipeline=POST_PUBLISH_INCIDENT_PIPELINE,
                object_type=POST_PUBLISH_INCIDENT_OBJECT,
                object_id=str(item.id),
                fingerprint=IncidentFingerprint.SAFETY_BLOCKED,
                hospital_name=hospital.name,
                actor=actor,
            )
        )
    except Exception:  # noqa: BLE001 - 닫기 실패는 다음 실행이 아닌 운영자 확인으로 남는다.
        logger.exception("post-publish flag incident recovery failed for %s", item.id)
