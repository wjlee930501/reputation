"""공개된 글의 사후 표본을 독립 AI 검수로 자동 처리하는 하루 한 번의 스윕.

사후 검수 표본(`post_publish_review_policy`)은 발행을 이미 통과한 글의 관찰용 확인이다.
소비자가 없던 시기에는 운영 DB의 공개 글 대부분이 미확인으로 쌓였고 월간 보고서에
'필수 사후검수 N건' 경고가 영구히 남았다. 사람이 버튼을 누르는 수동 경로만 있었기 때문이다.

설계 규칙:

- 생성 경로와 **같은** 독립 검수(`review_generated_content`, 비용 카테고리 `content`)를 쓴다.
  PASS(차단 지적 없음)가 지금 본문 해시에 묶여 나오면 `post_publish_reviewed_by='system:ai-review'`로
  확인 완료로 기록한다.
- 차단 지적(HARD/UNCERTAIN)이 나와도 **자동으로 내리지 않는다**(D35). 공개 글을 숨기는 것은 사람이
  정한다. 이 글의 `ai_review`는 건드리지 않고(저장하면 공개 가시성이 그 글을 숨긴다) 별도 키
  `post_publish_ai_review`에 `FLAGGED`와 지적 문구를 남긴다. 사고는 Critical일 때만 연다(아래). 같은 본문을 매일 다시 사지 않도록 FLAGGED 글은 스윕이 고르지 않는다 — 사람이 본문을
  고치면(PATCH가 이 키를 지운다) 새 본문으로 다시 검수한다.
- 검수 불가(공급자 장애·비용 가드)는 아무것도 바꾸지 않는다. 다음 실행이 다시 집는다.
- 하루 상한(`POST_PUBLISH_AI_REVIEW_DAILY_CAP`)이 곧 실행당 상한이다(하루 한 번 실행). 편집된 글을
  먼저, 그다음 오래된 글부터 처리해 백로그를 며칠에 걸쳐 비운다.
- FLAGGED 글은 자동 교정한다(2026-10-08 대표 결정, `published_correction`). 지적 문장만 최소 교정 패스로
  고친 **메모리 안의 후보**를 독립 재검수하고, 그 후보 hash에 묶인 PASS일 때만 살아 있는 행에 CAS로
  쓴다. 아니면 글은 그대로이고 FLAGGED 표시·인시던트가 남으며(시도 기록과 함께) 글당 상한
  (`POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES`)을 넘기면 사람이 직접 고친다고 인시던트가 말한다.
  표시에 구조화 지적(인용)이 없는 옛 FLAGGED 글은 문구에서 인용을 짐작하지 않고 재검수로 구조를 얻는다.
- 자동 교정이 끝나고도 UNCERTAIN(확신 부족)만 남은 글은 `POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS` 뒤 검수
  모델로 한 번만 더 본다(표시의 `senior_rereview`가 재구매를 막는다). PASS면 확인 완료로 닫는다.
- 지적의 등급(`finding_criticality`, 2026-10-14 D35): **Critical**(의료 안전 HARD·응급 안내 누락)이 남은
  공개 글은 내리지 않고 글당 한 번 "마케터 검토 필요" 사고(`POST_PUBLISH_CRITICAL_FINDING`, 운영 담당·
  Slack)를 연다. Critical이 아닌 지적만 남으면 사고를 열지 않고, 교정이 끝나면(UNCERTAIN만이면 상위 재검수
  뒤) `system:ai-review-noncritical` 확인으로 조용히 닫는다. 옛 조용한 `POST_PUBLISH_REVIEW_FLAGGED` 사고는
  더 열지 않고 건드릴 때 닫는다.
- 검수 장애로 판정 없이 먼저 발행된 글(D37 — 저장된 `ai_review`가 UNAVAILABLE)은 하루 상한 안에서
  표본보다 먼저 검수한다(`_deferred_review_stmt`).
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, NamedTuple

import redis
from celery import current_task
from redis.exceptions import RedisError
from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import joinedload

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital
from app.schemas.content import PendingContentCandidate
from app.services import cost_guard, indexnow
from app.services import published_correction as pc
from app.services.audit_log import write_audit_log_sync
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    candidate_sha256,
    remember_blocking_review_hashes,
    review_generated_content,
    store_ai_review,
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
from app.services.content_publication import (
    AI_REVIEW_DEFERRED_KEY,
    POST_PUBLISH_CRITICAL_ALERT_KEY,
)
from app.services.finding_criticality import flag_findings_are_critical
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
POST_PUBLISH_SENIOR_REREVIEW_KEY = "senior_rereview"

POST_PUBLISH_INCIDENT_PIPELINE = "post_publish_ai_review"
POST_PUBLISH_INCIDENT_OBJECT = "content_item"
POST_PUBLISH_INCIDENT_TYPE = "POST_PUBLISH_REVIEW_FLAGGED"
# D35(2026-10-14): 공개 글의 Critical 지적 — 글은 내리지 않고 "마케터 검토 필요" 알림 하나(글당).
POST_PUBLISH_CRITICAL_INCIDENT_PIPELINE = "post_publish_critical_review"
POST_PUBLISH_CRITICAL_INCIDENT_TYPE = "POST_PUBLISH_CRITICAL_FINDING"
# Critical이 아닌 지적만 남은 공개 글을 조용히 닫을 때의 확인 기록(사람 확인·검수 PASS와 구분한다).
POST_PUBLISH_NONCRITICAL_CLOSER = "system:ai-review-noncritical"
# 글당 Critical 알림 의도(요청·전달 시각, 어느 본문의 판정인가). Critical 판정을 저장하는 같은 커밋에
# 요청을 남기고, 사고를 연 뒤 전달 시각을 적는다 — 교정 성공·장애·하루 상한과 무관하다. 같은 본문에는
# 다시 알리지 않고, 본문이 바뀌어 새 Critical이 나올 때만 다시 알린다(Codex P1-4).
# D37 선발행 글 검수 단계의 자기 예산 — 뒤 단계(교정·상위 재검수·표본)와 시간·연속 장애를 나누지 않는다.
_DEFERRED_RUN_BUDGET_SECONDS = 600

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


def _deferred_review_stmt(limit: int):
    """검수 장애로 판정 없이 먼저 발행된 공개 글(D37) — 사후 검수 맨 앞. 오래 공개된 글부터."""

    flag_status = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]["status"].as_string()
    review_status = ContentItem.essence_check_summary["ai_review"]["status"].as_string()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.post_publish_reviewed_at.is_(None),
            ContentItem.published_at.is_not(None),
            ContentItem.body.is_not(None),
            publicly_operational_hospital_predicate(),
            review_status == "UNAVAILABLE",
            flag_status.is_distinct_from(POST_PUBLISH_FLAGGED),
        )
        .order_by(ContentItem.published_at, ContentItem.id)
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


# UNCERTAIN만 남은 후보를 id 키셋으로 넘겨 보는 쪽 크기와 한 번에 보는 쪽 수의 상한.
_UNCERTAIN_SCAN_PAGE_SIZE = 100
_UNCERTAIN_SCAN_MAX_PAGES = 10
# `checked_at_epoch`가 없는 옛 표시를 훑는 키셋 커서. tick 사이에 이어 가야 앞쪽의 묵지 않은·시각을
# 읽을 수 없는 표시 1,000건이 뒤쪽 글을 영영 가리지 않는다. 만료 없이 두고 끝에 닿으면 지운다(처음부터).
_UNCERTAIN_SCAN_CURSOR_KEY = "post_publish_ai_review:uncertain_scan_cursor"
_EPOCH_KEY = "checked_at_epoch"


class UncertainSelection(NamedTuple):
    items: list[ContentItem]
    # 옛 표시 훑기를 다음 tick이 이어 갈 id. None이면 끝에 닿았다 — 다음 tick은 처음부터.
    next_cursor: uuid.UUID | None


def _uncertain_only_stmt(
    page_size: int,
    *,
    after_id: uuid.UUID | None = None,
    settled_before_epoch: int | None = None,
):
    """교정이 끝났는데 UNCERTAIN만 남았고 아직 상위 재검수를 받지 않은 공개 글.

    UNCERTAIN만 조건을 LIMIT 앞 SQL에 건다 — 파이썬에서 거르면 오래된 HARD 글이 상한을 다 차지해
    UNCERTAIN 글이 영영 뽑히지 않는다.

    - `settled_before_epoch`가 있으면 쓰는 쪽이 정한 숫자 시각(`checked_at_epoch`)이 그 값 이하인
      표시만 고른다 — 묵음 판정까지 SQL이 하므로 앞쪽 백로그와 무관하다. 형이 숫자일 때만 CASE
      안에서 형 변환한다.
    - 없으면 숫자 시각이 없는 옛 표시를 id 키셋 한 쪽씩 읽는다. 그 묵음(`checked_at` ISO 문자열)
      판정은 SQL에서 하지 않는다: Postgres에는 try_cast가 없어 정규식을 통과한 2월 30일·13월 같은
      값의 `::timestamptz`가 스윕 전체를 깨뜨린다. 판정은 `settled_long_enough`가 파이썬에서 한다.
    """

    flag = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]
    findings = flag["structured_findings"]
    epoch = flag[_EPOCH_KEY]
    as_array = case(
        (func.jsonb_typeof(findings) == "array", findings),
        else_=func.jsonb_build_array(),
    )
    finding = func.jsonb_array_elements(as_array).table_valued("value").alias("finding")
    not_uncertain = (
        select(literal(1))
        .select_from(finding)
        .where(
            or_(
                func.jsonb_typeof(finding.c.value) != "object",
                func.upper(func.coalesce(finding.c.value.op("->>")("severity"), ""))
                != "UNCERTAIN",
            )
        )
        .exists()
    )
    stmt = (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.post_publish_reviewed_at.is_(None),
            ContentItem.body.is_not(None),
            ContentItem.pending_revision.is_(None),
            publicly_operational_hospital_predicate(),
            flag["status"].as_string() == POST_PUBLISH_FLAGGED,
            flag["correction"]["finished"].as_string() == "true",
            flag[POST_PUBLISH_SENIOR_REREVIEW_KEY].as_string().is_(None),
            func.jsonb_typeof(findings) == "array",
            func.jsonb_array_length(as_array) > 0,
            ~not_uncertain,
        )
    )
    if settled_before_epoch is not None:
        epoch_value = case((func.jsonb_typeof(epoch) == "number", epoch.as_float()), else_=None)
        stmt = stmt.where(epoch_value <= settled_before_epoch)
    else:
        stmt = stmt.where(func.jsonb_typeof(epoch).is_distinct_from("number"))
        if after_id is not None:
            stmt = stmt.where(ContentItem.id > after_id)
    return stmt.order_by(ContentItem.id).options(joinedload(ContentItem.hospital)).limit(page_size)


def _uncertain_settled_items(
    db, limit: int, *, now: datetime, days: int, after_id: uuid.UUID | None = None
) -> UncertainSelection:
    """UNCERTAIN만 남아 `days`일 묵은 글을 `limit`개까지 모은다.

    숫자 시각이 있는 표시는 SQL이 묵음까지 판정해 바로 고른다. 남은 자리는 숫자 시각 없는 옛 표시를
    `after_id` 뒤부터 id 키셋으로 쪽마다 읽어(상한 `_UNCERTAIN_SCAN_MAX_PAGES`쪽) 파이썬에서 판정해
    채운다. 마지막으로 읽은 id를 `next_cursor`로 돌려주고, 끝에 닿으면 None이다(다음은 처음부터).
    """

    cutoff = int(now.timestamp()) - days * 86400
    eligible: list[ContentItem] = [
        item
        for item in db.execute(_uncertain_only_stmt(limit, settled_before_epoch=cutoff))
        .unique()
        .scalars()
        .all()
        if uncertain_only((item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY))
    ][:limit]
    cursor = after_id
    for _ in range(_UNCERTAIN_SCAN_MAX_PAGES):
        if len(eligible) >= limit:
            break
        page = list(
            db.execute(_uncertain_only_stmt(_UNCERTAIN_SCAN_PAGE_SIZE, after_id=cursor))
            .unique()
            .scalars()
            .all()
        )
        for item in page:
            cursor = item.id
            marker = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY)
            if uncertain_only(marker) and settled_long_enough(marker, now, days):
                eligible.append(item)
                if len(eligible) >= limit:
                    return UncertainSelection(eligible, cursor)
        if len(page) < _UNCERTAIN_SCAN_PAGE_SIZE:
            return UncertainSelection(eligible, None)
    return UncertainSelection(eligible, cursor)


def _load_uncertain_scan_cursor() -> uuid.UUID | None:
    """저장된 옛 표시 훑기 커서. 읽지 못하면 처음부터(None) — 커서는 공정성일 뿐 정확성이 아니다."""

    try:
        with redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5
        ) as client:
            raw = client.get(_UNCERTAIN_SCAN_CURSOR_KEY)
        return uuid.UUID(raw.decode() if isinstance(raw, bytes) else str(raw)) if raw else None
    except (RedisError, ValueError):
        logger.warning("post-publish uncertain scan cursor unreadable; scanning from the start")
        return None


def _save_uncertain_scan_cursor(cursor: uuid.UUID | None) -> None:
    try:
        with redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=5, socket_timeout=5
        ) as client:
            if cursor is None:
                client.delete(_UNCERTAIN_SCAN_CURSOR_KEY)
            else:
                client.set(_UNCERTAIN_SCAN_CURSOR_KEY, str(cursor))
    except RedisError:
        logger.warning("post-publish uncertain scan cursor not saved; next run may rescan")


def uncertain_only(marker: object) -> bool:
    """표시의 구조화 지적이 모두 UNCERTAIN(확신 부족)인가. 구조를 모르면 아니다."""

    findings = marker.get("structured_findings") if isinstance(marker, dict) else None
    if not findings:
        return False
    return all(
        isinstance(finding, dict) and str(finding.get("severity", "")).upper() == "UNCERTAIN"
        for finding in findings
    )


def settled_long_enough(marker: object, now: datetime, days: int) -> bool:
    """마지막 검수에서 `days`일이 지났는가. 시각을 읽을 수 없으면 아니다.

    숫자 시각(`checked_at_epoch`)이 있으면 그것이 정본이다(SQL 판정과 같다). 없으면 옛 ISO 문자열
    `checked_at`을 읽는다.
    """

    epoch = marker.get(_EPOCH_KEY) if isinstance(marker, dict) else None
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        return now.timestamp() - epoch >= days * 86400
    try:
        checked = datetime.fromisoformat(str((marker or {}).get("checked_at")))
    except (TypeError, ValueError, AttributeError):
        return False
    if checked.tzinfo is None:
        return False
    return now - checked >= timedelta(days=days)


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
        # 묵음 판정을 SQL이 할 수 있도록 쓰는 쪽이 형식을 정한 숫자 시각(초).
        _EPOCH_KEY: int(now.timestamp()),
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
        store_ai_review(summary, review.payload())
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
        remember_blocking_review_hashes(summary, review.candidate_sha256)
        summary[POST_PUBLISH_FLAG_KEY] = _flag_payload(
            review, review.candidate_sha256, now, summary.get(POST_PUBLISH_FLAG_KEY)
        )
        if critical_alerts_enabled():
            _request_critical_alert(summary, item, now)
        item.essence_check_summary = summary
        return "FLAGGED"
    # 차단 지적이 없는 PASS(비차단 SOFT 포함). 현재 본문에 묶인 검수라 `ai_review`도 갱신해
    # 편집으로 낡은 PASS를 현재 해시로 되돌린다 — 이후 되돌림·재발행 게이트가 같은 기록을 본다.
    store_ai_review(summary, review.payload())
    summary.pop(POST_PUBLISH_FLAG_KEY, None)
    summary.pop(AI_REVIEW_DEFERRED_KEY, None)
    item.essence_check_summary = summary
    item.post_publish_reviewed_at = now
    item.post_publish_reviewed_by = POST_PUBLISH_AI_REVIEWER
    return "REVIEWED"


# ── D35 Critical 알림 — 글당 평생 한 번의 알림 의도(2026-10-14 Codex 2차 C·D·E) ─────────────────
# 알림 의도는 Critical 판정을 저장하는 같은 커밋에 글 행(`POST_PUBLISH_CRITICAL_ALERT_KEY`)에 남고, 판정
# 스냅샷(제목·지적·본문 hash)을 싣는다. 전달은 이후의 FLAG·PASS·교정 상태와 무관하다. 한 번 요청된 글은
# 본문이 바뀌어도 다시 요청하지 않는다(대표 결정 "글당 한 번"). 전달할 수 없는 의도(공개 중이 아님·
# 병원 없음·반복 실패)는 닫아 큐를 막지 않는다 — 대기 조회는 미전달·미종결 의도만, 가장 오래된 것부터 본다.
_ALERT_BATCH = 50
_ALERT_MAX_ATTEMPTS = 5
# 배포 전부터 남은 Critical 공개 글(알림 의도가 없는 FLAGGED)을 한 통의 요약으로 알릴 때의 상한.
_BACKLOG_SCAN_LIMIT = 500
_BACKLOG_DIGEST_LINES = 30
POST_PUBLISH_CRITICAL_DIGEST_NOTIFICATION_TYPE = "POST_PUBLISH_CRITICAL_DIGEST"


def _alert_snapshot(item: ContentItem, marker: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(getattr(item, "title", None) or "")[:200],
        "findings": [str(message)[:240] for message in (marker.get("findings") or [])][:3],
        "candidate_sha256": marker.get("candidate_sha256"),
        "checked_at": marker.get("checked_at"),
    }


def _request_critical_alert(summary: dict[str, Any], item: ContentItem, now: datetime) -> bool:
    """표시가 Critical이고 이 글에 알림 의도가 한 번도 없었으면 의도를 적는다(호출부의 커밋)."""

    if POST_PUBLISH_CRITICAL_ALERT_KEY in summary:
        return False
    marker = summary.get(POST_PUBLISH_FLAG_KEY)
    if not isinstance(marker, dict) or not flag_is_critical(marker, item):
        return False
    summary[POST_PUBLISH_CRITICAL_ALERT_KEY] = {
        "requested_at": now.isoformat(),
        "snapshot": _alert_snapshot(item, marker),
        "attempts": 0,
        "critical_alert_sent_at": None,
        "closed_at": None,
    }
    return True


def _pending_critical_alert_stmt(limit: int):
    """미전달·미종결 알림 의도 — 가장 오래된 요청부터. 종결·전달된 의도는 자리를 차지하지 않는다."""

    alert = ContentItem.essence_check_summary[POST_PUBLISH_CRITICAL_ALERT_KEY]
    return (
        select(ContentItem)
        .where(
            alert["requested_at"].as_string().is_not(None),
            alert["critical_alert_sent_at"].as_string().is_(None),
            alert["closed_at"].as_string().is_(None),
        )
        .order_by(alert["requested_at"].as_string(), ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def _deliver_critical_alert(db, item_id, run_async, *, now: datetime | None = None) -> str:
    """알림 의도 하나를 전달하거나 닫는다. ``SENT``·``CLOSED``·``RETRY``·``SKIPPED``를 돌려준다."""

    observed = now or datetime.now(UTC)
    locked = _lock_item(db, item_id)
    summary = dict(locked.essence_check_summary or {}) if locked is not None else {}
    alert = summary.get(POST_PUBLISH_CRITICAL_ALERT_KEY)
    if (
        locked is None
        or not isinstance(alert, dict)
        or alert.get("critical_alert_sent_at")
        or alert.get("closed_at")
    ):
        db.rollback()
        return "SKIPPED"
    hospital = getattr(locked, "hospital", None)
    if locked.status != ContentStatus.PUBLISHED or hospital is None:
        summary[POST_PUBLISH_CRITICAL_ALERT_KEY] = {
            **alert,
            "closed_at": observed.isoformat(),
            "closed_reason": "not_public",
        }
        locked.essence_check_summary = summary
        db.commit()
        return "CLOSED"
    try:
        _open_critical_incident(locked, hospital, run_async, snapshot=alert.get("snapshot") or {})
    except Exception:  # noqa: BLE001 - 한 글의 실패가 큐를 멈추지 않는다(시도를 세고 상한에서 닫는다).
        logger.exception("post-publish critical alert failed for %s", item_id)
        db.rollback()
        relocked = _lock_item(db, item_id)
        current = dict(relocked.essence_check_summary or {}) if relocked is not None else {}
        pending = current.get(POST_PUBLISH_CRITICAL_ALERT_KEY)
        if relocked is None or not isinstance(pending, dict):
            db.rollback()
            return "RETRY"
        attempts = int(pending.get("attempts") or 0) + 1
        updated = {**pending, "attempts": attempts}
        if attempts >= _ALERT_MAX_ATTEMPTS:
            updated.update(closed_at=observed.isoformat(), closed_reason="delivery_failed")
        current[POST_PUBLISH_CRITICAL_ALERT_KEY] = updated
        relocked.essence_check_summary = current
        db.commit()
        return "CLOSED" if attempts >= _ALERT_MAX_ATTEMPTS else "RETRY"
    summary[POST_PUBLISH_CRITICAL_ALERT_KEY] = {
        **alert,
        "critical_alert_sent_at": observed.isoformat(),
    }
    locked.essence_check_summary = summary
    db.commit()
    return "SENT"


def deliver_pending_critical_alerts(db, run_async, *, now: datetime | None = None) -> int:
    """미전달 알림 의도를 가장 오래된 것부터 보낸다. 보낸 수."""

    sent = 0
    for item_id in [
        row.id
        for row in db.execute(_pending_critical_alert_stmt(_ALERT_BATCH)).unique().scalars().all()
    ]:
        if _deliver_critical_alert(db, item_id, run_async, now=now) == "SENT":
            sent += 1
    return sent


def ensure_critical_alert(db, item_id, run_async, *, now: datetime | None = None) -> str:
    """이 글에 알림 의도가 없고 지금 표시가 Critical이면 의도를 남기고, 미전달 의도를 보낸다."""

    observed = now or datetime.now(UTC)
    locked = _lock_item(db, item_id)
    if locked is not None and locked.status == ContentStatus.PUBLISHED:
        summary = dict(locked.essence_check_summary or {})
        if _request_critical_alert(summary, locked, observed):
            locked.essence_check_summary = summary
            db.commit()
        else:
            db.rollback()
    else:
        db.rollback()
    return _deliver_critical_alert(db, item_id, run_async, now=observed)


def _alert_safely(db, item_id, run_async) -> None:
    try:
        ensure_critical_alert(db, item_id, run_async)
    except Exception:  # noqa: BLE001 - 알림 실패가 저장된 판정을 되돌리지 않는다(의도는 남아 다시 보낸다).
        db.rollback()
        logger.exception("post-publish critical alert failed for %s", item_id)


def _backlog_stmt(limit: int):
    """알림 의도가 없는 FLAGGED 공개 글 — 배포 전부터 남은 판정(교정 상태와 무관)."""

    summary = ContentItem.essence_check_summary
    return (
        select(ContentItem)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            summary[POST_PUBLISH_FLAG_KEY]["status"].as_string() == POST_PUBLISH_FLAGGED,
            summary[POST_PUBLISH_CRITICAL_ALERT_KEY].as_string().is_(None),
        )
        .order_by(ContentItem.published_at, ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def build_critical_backlog_digest(items: list[ContentItem], now: datetime):
    """"마케터 검토 필요 N건" 요약 한 통(최대 30줄, 그다음 "외 N건")."""

    from app.services.notification_contracts import NotificationIntent
    from app.services.notification_labels import prefixed_for_event
    from app.services.notification_milestone_rendering import (
        RenderedSlackMessage,
        action_block,
        admin_url,
        chunk_lines,
        header_block,
        safe_text,
        section_block,
        validated_message,
    )

    kind = POST_PUBLISH_CRITICAL_DIGEST_NOTIFICATION_TYPE
    total = len(items)
    lines = [
        f"• *{safe_text(str(getattr(item.hospital, 'name', '') or '병원'), 60)}* · "
        f"「{safe_text(str(item.title or ''), 80)}」"
        for item in items[:_BACKLOG_DIGEST_LINES]
    ]
    if total > _BACKLOG_DIGEST_LINES:
        lines.append(f"외 {total - _BACKLOG_DIGEST_LINES}건")
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations")
    title = f"마케터 검토 필요 {total}건"
    blocks = [
        header_block("post_publish_critical_digest_header", prefixed_for_event(kind, title)),
        section_block(
            "post_publish_critical_digest_summary",
            "공개된 글의 사후 검수에서 의료 안전 지적이 남은 글입니다. 글은 그대로 공개되어 있습니다 — "
            "각 병원의 콘텐츠 탭에서 지적 문장을 확인해 주세요.",
        ),
        *(
            section_block(f"post_publish_critical_digest_{index}", chunk)
            for index, chunk in enumerate(chunk_lines(lines))
        ),
        action_block("post_publish_critical_digest_action", action_url, "운영 센터 열기"),
    ]
    message = validated_message(
        RenderedSlackMessage(prefixed_for_event(kind, title), tuple(blocks), action_url),
        settings.ADMIN_BASE_URL,
    )
    identity = hashlib.sha256(
        ",".join(sorted(str(item.id) for item in items)).encode("utf-8")
    ).hexdigest()[:16]
    return NotificationIntent(
        dedupe_key=f"post-publish-critical-digest:{now.date().isoformat()}:{identity}",
        notification_type=kind,
        message=message,
    )


def send_critical_backlog_digest(db, *, now: datetime | None = None) -> int:
    """알림 의도가 없는 Critical FLAGGED 공개 글을 요약 한 통으로 알리고 의도를 '보냄'으로 적는다.

    배포 첫 실행에서 기존 백로그를 조용히 빼지도, 글마다 쏟아내지도 않는다. 그 뒤로 새 Critical은 판정
    저장 때 의도가 생기므로 글마다 알린다. 의료 내용이 없는 FLAGGED는 대상이 아니다. 알린 글 수.
    """

    from app.services.notification_store import enqueue_notification_sync

    observed = now or datetime.now(UTC)
    rows = list(db.execute(_backlog_stmt(_BACKLOG_SCAN_LIMIT)).unique().scalars().all())
    critical = [
        item
        for item in rows
        if flag_is_critical((item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY), item)
    ]
    if not critical:
        return 0
    enqueue_notification_sync(db, build_critical_backlog_digest(critical, observed))
    for item in critical:
        summary = dict(item.essence_check_summary or {})
        marker = summary.get(POST_PUBLISH_FLAG_KEY) or {}
        summary[POST_PUBLISH_CRITICAL_ALERT_KEY] = {
            "requested_at": observed.isoformat(),
            "snapshot": _alert_snapshot(item, marker),
            "attempts": 0,
            "critical_alert_sent_at": observed.isoformat(),
            "closed_at": None,
            "via": "BACKLOG_DIGEST",
        }
        item.essence_check_summary = summary
    db.commit()
    return len(critical)


_NEEDS_HUMAN_COPY = {
    "TITLE_FINDING": "제목에 지적이 있습니다. 제목은 자동으로 바꾸지 않습니다.",
    "NOT_CORRECTABLE": "지적이 문장 하나를 고치는 방식으로는 풀 수 없는 내용입니다.",
    "PARTLY_UNCORRECTABLE": "지적 일부는 문장 하나를 고치는 방식으로는 풀 수 없는 내용입니다.",
    "blocked": "자동으로 고친 본문이 독립 재검수를 통과하지 못했습니다.",
    "SENIOR_REREVIEW_HARD": "상위 검수 모델이 안전 지적을 다시 확인했습니다.",
}
_NEEDS_HUMAN_DEFAULT_COPY = "자동 교정이 안전 검사를 통과하지 못했습니다."


def flag_is_critical(marker: object, content: object = None) -> bool:
    """사후 검수 표시에 Critical 지적이 남았는가(`finding_criticality` 허용 목록 — 본문으로 인용 문장을
    확인한다). 구조를 모르거나 본문이 없으면 Critical이다."""

    findings = marker.get("structured_findings") if isinstance(marker, dict) else None
    return flag_findings_are_critical(findings, content)


def _open_critical_incident(
    item: ContentItem,
    hospital: Hospital,
    run_async,
    *,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """D35: 공개 글의 Critical 판정 — 글은 그대로 두고 "마케터 검토 필요" 사고를 연다(판정 스냅샷 문구)."""

    from app.services.ops_incident_alerts import open_ops_incident

    snapshot = snapshot or {}
    title = snapshot.get("title") or item.title
    findings = snapshot.get("findings") or []
    detail = f" 지적: {findings[0]}" if findings else ""
    run_async(
        open_ops_incident(
            pipeline=POST_PUBLISH_CRITICAL_INCIDENT_PIPELINE,
            object_type=POST_PUBLISH_INCIDENT_OBJECT,
            object_id=str(item.id),
            incident_type=POST_PUBLISH_CRITICAL_INCIDENT_TYPE,
            safe_error_code=POST_PUBLISH_CRITICAL_INCIDENT_TYPE,
            problem=(
                f"마케터 검토 필요: 공개된 글 「{title}」의 사후 검수에서 의료 안전 지적이 "
                f"나왔습니다.{detail}"
            ),
            customer_impact="글은 그대로 공개되어 있습니다. 자동으로 내리지 않았습니다.",
            next_action=(
                "콘텐츠 탭에서 지적 문장을 확인해 주세요. 고쳐야 하면 직접 고치거나 비공개로 돌리고, "
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


def critical_alerts_enabled() -> bool:
    """D35 공개 글 Critical 알림을 쓰는가(`POST_PUBLISH_CRITICAL_ALERTS_ENABLED`, 기본 꺼짐)."""

    return bool(settings.POST_PUBLISH_CRITICAL_ALERTS_ENABLED)


# 알림이 꺼져 있을 때의 종전(main) 처리 — 조용한 POST_PUBLISH_REVIEW_FLAGGED 사고.
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


def _close_noncritical_flag(db, item_id, reviewed_hash: object, now: datetime) -> bool:
    """Critical이 아닌 지적만 남은 공개 글을 조용히 닫는다. 그 사이 표시·본문이 바뀌었으면 쓰지 않는다."""

    locked = _lock_item(db, item_id)
    summary = locked.essence_check_summary if locked is not None else None
    marker = summary.get(POST_PUBLISH_FLAG_KEY) if isinstance(summary, dict) else None
    if (
        locked is None
        or locked.status != ContentStatus.PUBLISHED
        or locked.post_publish_reviewed_at is not None
        or not isinstance(marker, dict)
        or marker.get("status") != POST_PUBLISH_FLAGGED
        or marker.get("candidate_sha256") != reviewed_hash
        or flag_is_critical(marker, locked)
    ):
        db.rollback()
        return False
    locked.essence_check_summary = {
        **summary,
        POST_PUBLISH_FLAG_KEY: {
            **marker,
            "closed": {"reason": "NONCRITICAL", "at": now.isoformat()},
        },
    }
    locked.post_publish_reviewed_at = now
    locked.post_publish_reviewed_by = POST_PUBLISH_NONCRITICAL_CLOSER
    db.commit()
    return True


def _settle_flag(
    db,
    item: ContentItem,
    hospital: Hospital,
    run_async,
    *,
    needs_human_reason: str | None = None,
    now: datetime | None = None,
) -> None:
    """FLAGGED로 남은 공개 글의 끝 처리(D35). 글은 어느 경우에도 내리지 않는다.

    - Critical: "마케터 검토 필요" 사고(글당 한 번, Slack) — 판정 저장 때 이미 보냈으면 그대로다.
    - Critical 아님: 사고 없음. 자동 교정이 끝났으면(`needs_human_reason`) 조용히 닫는다 — 단 UNCERTAIN만
      남았고 상위 재검수를 아직 받지 않았으면 그 재검수(며칠 뒤)를 기다린다.
    옛 조용한 사고(`POST_PUBLISH_REVIEW_FLAGGED`)는 더 열지 않고 닫는다.
    """

    if not critical_alerts_enabled():
        # 종전(main) 그대로 — 조용한 사고를 열거나 문구만 갱신한다. 닫지도 알리지도 않는다.
        _open_flag_incident(item, hospital, run_async, needs_human_reason=needs_human_reason)
        return
    marker = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY)
    if not isinstance(marker, dict) or marker.get("status") != POST_PUBLISH_FLAGGED:
        return
    _recover_flag_incident(item, hospital, run_async)
    if flag_is_critical(marker, item):
        # 글당 평생 한 번 — 의도가 이미 있으면(보냈든 닫혔든) 다시 만들지 않는다(`ensure_critical_alert`).
        ensure_critical_alert(db, item.id, run_async, now=now)
        return
    awaiting_senior = uncertain_only(marker) and not marker.get(POST_PUBLISH_SENIOR_REREVIEW_KEY)
    if needs_human_reason and not awaiting_senior:
        _close_noncritical_flag(
            db, item.id, marker.get("candidate_sha256"), now or datetime.now(UTC)
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
    if outcome == "FLAGGED" and critical_alerts_enabled():
        # Critical 판정은 교정 결과·장애·하루 상한과 무관하게 지금 알린다(글당 한 번).
        _alert_safely(db, item_id, run_async)
    return outcome


def senior_rereview_flagged(db, item, hospital, philosophy, run_async, now: datetime) -> str:
    """UNCERTAIN만 남은 공개 글을 검수 모델로 한 번 더 본다. PASS면 확인 완료로 닫고 사고를 닫는다.

    FLAGGED면 표시에 재검수 사실(`senior_rereview`)을 같은 커밋에 남겨 다시 사지 않는다. 그 재검수가
    HARD를 내면 조용한 FLAGGED 사고의 문구만 갱신한다(사후검수 지적은 Slack·할 일이 아니다).
    """

    item_id = item.id
    try:
        review = run_async(
            review_generated_content(
                hospital=hospital,
                philosophy=philosophy,
                content=_candidate_content(item),
                content_brief=getattr(item, "content_brief", None),
                model=None,  # 검수 모델(CLAUDE_MODEL_REVIEW)
            )
        )
    except Exception as error:  # noqa: BLE001 - 한 글의 실패가 하루 스윕을 멈추지 않는다.
        logger.warning(
            "senior post-publish re-review failed for %s: %s", item_id, type(error).__name__
        )
        db.rollback()
        return "UNAVAILABLE"
    if review.status == ContentAiReviewStatus.UNAVAILABLE:
        db.rollback()
        return "UNAVAILABLE"
    locked = _lock_item(db, item_id)
    if locked is None:
        db.rollback()
        return "SKIPPED"
    outcome = apply_review_outcome(locked, review, now=now)
    if outcome == "FLAGGED":
        summary = dict(locked.essence_check_summary)
        marker = dict(summary[POST_PUBLISH_FLAG_KEY])
        marker[POST_PUBLISH_SENIOR_REREVIEW_KEY] = {
            "at": now.isoformat(),
            "model": settings.CLAUDE_MODEL_REVIEW,
        }
        summary[POST_PUBLISH_FLAG_KEY] = marker
        locked.essence_check_summary = summary
    db.commit()
    if outcome == "REVIEWED":
        _recover_flag_incident(locked, hospital, run_async)
    elif outcome == "FLAGGED" and (
        critical_alerts_enabled()
        or not uncertain_only(locked.essence_check_summary[POST_PUBLISH_FLAG_KEY])
    ):
        # 상위 재검수까지 받았다 — Critical이면 마케터 검토 사고, 아니면 조용히 닫는다(D35).
        # 알림이 꺼져 있으면 종전처럼 HARD가 남았을 때만 조용한 사고 문구를 갱신한다.
        try:
            _settle_flag(
                db,
                locked,
                hospital,
                run_async,
                needs_human_reason="SENIOR_REREVIEW_HARD",
                now=now,
            )
        except Exception:  # noqa: BLE001 - 인시던트 기록 실패가 저장된 표시를 되돌리지 않는다.
            logger.exception("post-publish flag incident failed for %s", item_id)
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
        _settle_flag(db, recorded, hospital, run_async, needs_human_reason=reason, now=now)
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


# 알림이 꺼져 있을 때(종전) 교정 결과가 이미 사고를 처리한 경우.
_LEGACY_INCIDENT_HANDLED = frozenset({"CORRECTED", "REVIEWED", "NEEDS_HUMAN", "BLOCKED", "REJECTED"})


def _bump(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


@celery_app.task(
    name="app.workers.post_publish_ai_review.review_post_publish_samples",
    soft_time_limit=2400,
    time_limit=2700,
)
def review_post_publish_samples() -> dict[str, int]:
    """선발행(D37) 글을 먼저 자기 예산으로 검수하고, FLAGGED 공개 글을 자동 교정하고, 미확인 사후
    표본을 하루 상한 안에서 독립 검수한다."""

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

    # 기존 단계(교정·상위 재검수·표본)의 시간 예산은 D37 단계가 끝난 뒤부터 센다 — D37은 자기 600초를 쓴다.
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

    with SyncSessionLocal() as db, cost_guard.cost_lane(cost_guard.LANE_POST_PUBLISH):
        # 0) 배포 전부터 남은 Critical 공개 글을 요약 한 통으로 알리고(글마다 쏟아내지 않는다), 미전달
        # 알림 의도를 가장 오래된 것부터 보낸다(사고 기록 실패 뒤 재시도·종결).
        try:
            if critical_alerts_enabled():
                send_critical_backlog_digest(db)
        except Exception:  # noqa: BLE001 - 요약 실패가 하루 검수를 멈추지 않는다(다음 실행이 다시 본다).
            db.rollback()
            logger.exception("post-publish critical backlog digest failed")
        try:
            if critical_alerts_enabled():
                deliver_pending_critical_alerts(db, _run_async)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("post-publish critical alert delivery failed")
        # 0b) 검수 장애로 판정 없이 먼저 발행된 글(D37) — 실제 첫 단계다. 자기 시간·연속 장애·상한을 쓰고
        # 뒤 단계(교정·상위 재검수·표본)와 나누지 않는다. 공개 중인데 아무 검수도 받지 않은 글이다.
        seen: set[uuid.UUID] = set()
        if cap > 0:
            deferred_started = time.monotonic()
            deferred_done = deferred_unavailable = 0
            for item in list(db.execute(_deferred_review_stmt(cap)).unique().scalars().all()):
                if (
                    deferred_done >= cap
                    or deferred_unavailable >= _CONSECUTIVE_UNAVAILABLE_LIMIT
                    or time.monotonic() - deferred_started > _DEFERRED_RUN_BUDGET_SECONDS
                ):
                    break
                hospital = item.hospital
                if hospital.id not in philosophies:
                    philosophies[hospital.id] = _generation_philosophy_sync(db, hospital.id)
                if philosophies[hospital.id] is None:
                    counts["skipped"] += 1
                    continue
                deferred_done += 1
                seen.add(item.id)
                item_id = item.id
                outcome = _review_and_apply(
                    db, item, hospital, philosophies[hospital.id], _run_async, datetime.now(UTC)
                )
                deferred_unavailable = deferred_unavailable + 1 if outcome == "UNAVAILABLE" else 0
                _bump(counts, f"deferred_{outcome.lower()}")
                if outcome == "REVIEWED":
                    _recover_flag_incident(db.get(ContentItem, item_id), hospital, _run_async)
                elif outcome == "FLAGGED" and (
                    not critical_alerts_enabled() or correction_cap <= 0
                ):
                    # 표본 경로에서 교정이 아직 맡지 않은 FLAGGED(main의 DEFERRED)와 같은 끝 처리 —
                    # 알림이 꺼져 있으면 조용한 사고를 연다. 교정은 바로 뒤 1단계가 FLAGGED 글로 집고,
                    # 교정되면 그 사고를 닫는다.
                    try:
                        _settle_flag(db, db.get(ContentItem, item_id), hospital, _run_async)
                    except Exception:  # noqa: BLE001 - 인시던트 기록 실패가 저장된 표시를 되돌리지 않는다.
                        logger.exception("post-publish flag incident failed for %s", item_id)
        started = time.monotonic()
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
        # 1b) 교정으로 못 푼 UNCERTAIN만 남은 글 — 며칠 뒤 검수 모델로 한 번 더 본다(PASS면 닫힌다).
        rereview_days = int(settings.POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS)
        if correction_cap > 0 and rereview_days > 0:
            rereview_now = datetime.now(UTC)
            selection = _uncertain_settled_items(
                db,
                correction_cap * _FLAG_SCAN_MULTIPLIER,
                now=rereview_now,
                days=rereview_days,
                after_id=_load_uncertain_scan_cursor(),
            )
            _save_uncertain_scan_cursor(selection.next_cursor)
            for item in selection.items:
                if corrections >= correction_cap or out_of_budget():
                    break
                hospital = item.hospital
                if hospital.id not in philosophies:
                    philosophies[hospital.id] = _generation_philosophy_sync(db, hospital.id)
                if philosophies[hospital.id] is None:
                    counts["skipped"] += 1
                    continue
                result = senior_rereview_flagged(
                    db, item, hospital, philosophies[hospital.id], _run_async, rereview_now
                )
                # 검수를 끝내지 못한 시도는 하루 상한을 쓰지 않는다(연속 불가는 out_of_budget이 끊는다).
                if result not in ("UNAVAILABLE", "SKIPPED"):
                    corrections += 1
                tally(result)
                _bump(counts, f"senior_rereview_{result.lower()}")
        if cap <= 0:
            logger.info("post-publish AI review sweep finished: %s", counts)
            return counts
        # 비공개 글(드물다)을 먼저 — restore가 막혀 있는 글이라 표본보다 급하다. 선발행 단계가 본 글은 뺀다.
        # 교정 단계의 연속 장애 계수는 main처럼 이어진다(교정 검수가 연달아 못 보면 표본도 접는다).
        candidates = list(db.execute(_withheld_stmt(cap)).unique().scalars().all())
        candidates += list(
            db.execute(_review_stmt(cap * _SCAN_MULTIPLIER)).unique().scalars().all()
        )
        candidates = [item for item in candidates if not (item.id in seen or seen.add(item.id))]
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
                # Critical 알림은 판정을 저장할 때 이미 나갔다(`_review_and_apply`). 교정이 이 글을 더
                # 맡을 수 없을 때만 끝 처리(비-Critical 조용한 종결 판단)를 한다.
                settle = (
                    result == "SKIPPED" or correction_cap <= 0
                    if critical_alerts_enabled()
                    # 종전(main): 교정이 사고를 처리하지 않은 결과면 조용한 사고를 연다.
                    else result not in _LEGACY_INCIDENT_HANDLED
                )
                if settle:
                    try:
                        _settle_flag(db, locked, hospital, _run_async)
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
