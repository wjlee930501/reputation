"""Durable publication notification intent, projection, and delivery stamp."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol, TypedDict, assert_never

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operations import NotificationOutbox, NotificationOutboxState
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
)
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

PUBLISH_NOTIFICATION_TYPE = "CONTENT_PUBLISHED"
MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE = (
    "MISSING_APPROVED_ESSENCE_DIGEST"
)
GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE = "GENERATION_BLOCKED_DIGEST"
GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE = (
    "GENERATION_REJECTION_WEEKLY_ROLLUP"
)
_DEDUPE_PREFIX = f"{PUBLISH_NOTIFICATION_TYPE}:"
_MISSING_ESSENCE_DIGEST_DEDUPE_PREFIX = (
    f"{MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE}:"
)
_GENERATION_BLOCKED_DIGEST_DEDUPE_PREFIX = (
    f"{GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE}:"
)
_GENERATION_REJECTION_WEEKLY_ROLLUP_DEDUPE_PREFIX = (
    f"{GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE}:"
)
# Slack Block Kit truncates long sections; keep the digest inside one readable block.
_DIGEST_MAX_HOSPITALS = 12
_DIGEST_MAX_ITEMS_PER_HOSPITAL = 5
# 수율 줄은 병원당 한 줄이라 차단 요약보다 조금 더 보여 준다. 나머지는 "그 외 N개".
_YIELD_MAX_HOSPITALS = 15
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PublishedItem(Protocol):
    id: uuid.UUID
    hospital_id: uuid.UUID
    title: str | None
    published_at: datetime | None


class HospitalIdentity(Protocol):
    id: uuid.UUID
    name: str


class PublishNotificationProjection(TypedDict):
    state: str
    label: str
    problem: str | None
    publication_impact: str
    next_action: str
    notification_id: str | None
    safe_error_code: str | None


@dataclass(frozen=True, slots=True)
class PublishNotificationIdentity:
    content_id: uuid.UUID
    published_at: datetime


PublishNotificationState = Literal[
    "PENDING", "SENDING", "RETRYING", "HOLD", "SENT", "FAILED"
]


class HospitalYieldView(Protocol):
    """주간 요약이 읽는 수율 사실의 모양(`services/content_yield.HospitalYieldFact`)."""

    hospital_name: str
    due: int
    published: int
    published_with_reused_image: int
    retrying: int
    operator_required: int


def _yield_lines(yield_facts: Sequence[HospitalYieldView]) -> tuple[list[str], int, int]:
    """병원별 '발행 n/예정 m' 한 줄. 부족분이 큰 병원이 위로 온다.

    계약도 발행도 없는 병원은 줄을 만들지 않는다 — 대표가 보는 것은 계약이 있는
    병원의 이행이지 빈 행의 목록이 아니다. `재시도 중`은 자동 복구가 소유한 수이고
    `조치 필요`만 사람의 일이다(두 수를 한 줄에 같이 두는 이유).
    """

    active = [fact for fact in yield_facts if fact.due or fact.published]
    ranked = sorted(
        active,
        key=lambda fact: (-(fact.due - fact.published), fact.hospital_name),
    )
    shown = ranked[:_YIELD_MAX_HOSPITALS]
    lines = [
        f"• *{_publish_safe_text(fact.hospital_name, 100)}* "
        f"발행 {fact.published}/{fact.due} "
        f"(재사용 이미지 {fact.published_with_reused_image}, "
        f"재시도 중 {fact.retrying}, 조치 필요 {fact.operator_required})"
        for fact in shown
    ]
    hidden = len(ranked) - len(shown)
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}개")
    return (
        lines,
        sum(fact.published for fact in active),
        sum(fact.due for fact in active),
    )


def build_publish_notification_intent(
    item: PublishedItem, hospital: HospitalIdentity
) -> NotificationIntent:
    """Build one publication-cycle intent with one safe Admin action."""

    if item.published_at is None:
        raise NotificationPayloadError("PUBLISHED_AT_REQUIRED")
    action_url = admin_url(
        settings.ADMIN_BASE_URL,
        f"/hospitals/{hospital.id}/content?content={item.id}",
    )
    hospital_name = _publish_safe_text(hospital.name, 100)
    title = _publish_safe_text(item.title or "제목 없는 콘텐츠", 180)
    details = (
        "무슨 문제인지: 콘텐츠가 공개되어 운영 확인이 필요합니다.\n"
        "고객 영향: 확인 전까지 잘못된 정보가 공개 화면에 남아 있을 수 있습니다.\n"
        "지금 할 일: Admin에서 공개된 글의 내용과 이미지를 확인해 주세요.\n"
        "처리 기한: 오늘 중"
    )
    message = validated_message(
        RenderedSlackMessage(
            "무슨 문제인지: 콘텐츠 공개 확인 필요 · "
            "고객 영향: 공개 정보 확인 전 · 지금 할 일: Admin 검토 · 처리 기한: 오늘 중",
            (
                header_block("publish_header", "콘텐츠 공개 확인"),
                section_block("publish_identity", f"*{hospital_name}*\n{title}"),
                section_block("publish_context", details),
                action_block("publish_action", action_url, "Admin에서 공개 내용 확인"),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=_publish_dedupe_key(item.id, item.published_at),
        notification_type=PUBLISH_NOTIFICATION_TYPE,
        message=message,
        hospital_id=hospital.id,
        max_attempts=3,
    )


def build_missing_approved_essence_digest_intent(
    cycle_date: date,
    skipped_outcomes: Sequence[Mapping[str, object]],
) -> NotificationIntent:
    """Build one neutral summary for a Seoul nightly onboarding skip cycle."""

    if not skipped_outcomes:
        raise NotificationPayloadError("MISSING_ESSENCE_DIGEST_ITEMS_REQUIRED")
    hospital_ids = {
        str(outcome["hospital_id"])
        for outcome in skipped_outcomes
        if outcome.get("hospital_id") is not None
    }
    if not hospital_ids:
        raise NotificationPayloadError("MISSING_ESSENCE_DIGEST_HOSPITALS_REQUIRED")
    hospital_count = len(hospital_ids)
    item_count = len(skipped_outcomes)
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=onboarding")
    summary = f"온보딩 병원 {hospital_count}곳 · 글 {item_count}건"
    message = validated_message(
        RenderedSlackMessage(
            f"온보딩 생성 요약 · {summary} · 승인 기준이 없어 생성을 건너뜀",
            (
                header_block("missing_essence_digest_header", "온보딩 생성 요약"),
                section_block(
                    "missing_essence_digest_summary",
                    f"*{summary}*\n승인 기준이 없어 생성을 건너뜀.",
                ),
                action_block(
                    "missing_essence_digest_action",
                    action_url,
                    "온보딩 현황 확인",
                ),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        dedupe_key=f"{_MISSING_ESSENCE_DIGEST_DEDUPE_PREFIX}{cycle_date.isoformat()}",
        notification_type=MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


IMAGE_REUSE_NEXT_ACTION = (
    "이미지 생성 공급자 크레딧·할당량과 비용 가드 한도를 확인해 주세요. "
    "새 이미지가 생성되면 대체 이미지는 자동으로 교체됩니다."
)
_IMAGE_FAILURE_CLASS_LABELS = {
    "COST_GUARD": "비용 가드 한도",
    "PROVIDER_QUOTA": "공급자 한도·크레딧 오류",
    "POLICY_REJECTED": "정책 검사 거절",
    "PROVIDER_ERROR": "공급자 오류",
}


# 대표 이미지를 만들지 못한 글이 무엇으로 나갔는지. 조치가 다르지 않으므로 메시지는
# 하나지만, 첫 글이라 병원 대표 이미지를 쓴 경우는 빌릴 원본이 아예 없었다는 뜻이라
# 운영자가 읽는 문구를 구분한다.
HOSPITAL_FALLBACK_IMAGE_SOURCE = "HOSPITAL_HERO"
_IMAGE_SOURCE_LABELS = {
    HOSPITAL_FALLBACK_IMAGE_SOURCE: "병원 대표 이미지 사용",
    "REUSED_ARTICLE": "재사용 발행",
}


def _image_source_kind(outcome: Mapping[str, object]) -> str:
    reused_from = str(outcome.get("reused_from") or "")
    if reused_from == HOSPITAL_FALLBACK_IMAGE_SOURCE:
        return HOSPITAL_FALLBACK_IMAGE_SOURCE
    return "REUSED_ARTICLE"


def _image_reuse_section(
    reused_outcomes: Sequence[Mapping[str, object]],
) -> tuple[str, list[str]]:
    """Group image-substituted publications by hospital and source with their cause."""

    hospitals: dict[tuple[str, str, str], list[str]] = {}
    for outcome in reused_outcomes:
        key = (
            str(outcome.get("hospital_id") or ""),
            str(outcome.get("hospital_name") or "이름 미확인 병원"),
            _image_source_kind(outcome),
        )
        failure_class = str(outcome.get("image_failure_class") or "PROVIDER_ERROR")
        hospitals.setdefault(key, []).append(
            _IMAGE_FAILURE_CLASS_LABELS.get(failure_class, _IMAGE_FAILURE_CLASS_LABELS["PROVIDER_ERROR"])
        )
    lines = []
    for (_hospital_id, hospital_name, source_kind), labels in sorted(hospitals.items()):
        dominant = Counter(labels).most_common(1)[0][0]
        source_label = _IMAGE_SOURCE_LABELS[source_kind]
        lines.append(
            f"• *{_publish_safe_text(hospital_name, 100)}* {source_label} {len(labels)}건 · {dominant}"
        )
    identity = sorted(
        f"{str(outcome.get('hospital_id') or '')}:{str(outcome.get('content_id') or '')}"
        f":{str(outcome.get('image_failure_class') or '')}:{_image_source_kind(outcome)}"
        for outcome in reused_outcomes
    )
    return "\n".join(identity), lines


def build_generation_blocked_digest_intent(
    cycle_date: date,
    batch: str,
    blocked_outcomes: Sequence[Mapping[str, object]],
    reused_outcomes: Sequence[Mapping[str, object]] = (),
) -> NotificationIntent:
    """Build one blocked-publication summary for a Seoul morning batch.

    Per-item incidents stay in the database because they drive the Admin retry
    controls. Slack gets one grouped message instead of one page per content item.
    The cycle and batch locate the observation, but unchanged blocker identities
    deliberately share one durable notification key across observations.
    """

    if not blocked_outcomes and not reused_outcomes:
        raise NotificationPayloadError("GENERATION_BLOCKED_DIGEST_ITEMS_REQUIRED")
    entries = [
        (
            str(outcome.get("hospital_id") or ""),
            str(outcome.get("hospital_name") or "이름 미확인 병원"),
            str(outcome.get("content_id") or ""),
            str(outcome.get("scheduled_date") or ""),
            str(outcome.get("code") or "UNKNOWN"),
            str(outcome.get("cause") or "자동 생성 작업이 완료되지 않았습니다."),
            _generation_blocked_display_title(outcome.get("title"), outcome.get("code")),
            str(outcome.get("attempt_fingerprint") or ""),
        )
        for outcome in blocked_outcomes
    ]
    identity = sorted(
        {
            f"{hospital_id}:{content_id}:{scheduled_date}:{code}:{cause}:{attempt_fingerprint}"
            for (
                hospital_id,
                _,
                content_id,
                scheduled_date,
                code,
                cause,
                _,
                attempt_fingerprint,
            ) in entries
        }
    )
    reuse_identity, reuse_lines = _image_reuse_section(reused_outcomes)
    digest = hashlib.sha256(
        "\n".join([*identity, reuse_identity]).encode()
    ).hexdigest()[:32]
    hospitals: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for (
        hospital_id,
        hospital_name,
        _content_id,
        _scheduled_date,
        code,
        cause,
        title,
        _attempt_fingerprint,
    ) in entries:
        hospitals.setdefault((hospital_id, hospital_name), []).append((title, code, cause))
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=incidents&status=OPEN")
    shown = sorted(hospitals.items())[:_DIGEST_MAX_HOSPITALS]
    hidden = len(hospitals) - len(shown)
    lines = []
    for (_hospital_id, hospital_name), items in shown:
        detail = " · ".join(
            f"{_publish_safe_text(title, 60)}({_publish_safe_text(cause, 80)})"
            for title, _code, cause in items[:_DIGEST_MAX_ITEMS_PER_HOSPITAL]
        )
        remainder = len(items) - min(len(items), _DIGEST_MAX_ITEMS_PER_HOSPITAL)
        if remainder > 0:
            detail = f"{detail} · 그 외 {remainder}건"
        lines.append(
            f"• *{_publish_safe_text(hospital_name, 100)}* 차단 {len(items)}건\n  {detail}"
        )
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}곳")
    summary = f"병원 {len(hospitals)}곳 · 글 {len(entries)}건"
    if not entries:
        # 차단이 하나도 없는 배치다 — 요약 줄은 대체 발행 건수만 말한다. 빌린 것과 병원
        # 대표 이미지를 쓴 것을 한 수로 세되, 어느 쪽인지는 아래 섹션 줄이 말한다.
        summary = f"대표 이미지 대체 발행 {len(reused_outcomes)}건"
    blocks = [
        header_block("generation_blocked_digest_header", "자동 발행 차단 요약"),
        section_block("generation_blocked_digest_summary", f"*{summary}*"),
    ]
    if lines:
        blocks.append(section_block("generation_blocked_digest_items", "\n".join(lines)))
    if reuse_lines:
        # 새 Slack 메시지를 만들지 않는다 — 같은 08:00 요약 안의 한 섹션이다.
        blocks.append(
            section_block(
                "generation_blocked_digest_image_reuse",
                "*대표 이미지 대체 발행*\n"
                + "\n".join(reuse_lines)
                + f"\n{IMAGE_REUSE_NEXT_ACTION}",
            )
        )
    blocks.append(
        action_block("generation_blocked_digest_action", action_url, "운영센터에서 모아보기")
    )
    message = validated_message(
        RenderedSlackMessage(
            f"무슨 문제인지: 자동 발행 차단 {summary} · "
            "고객 영향: 예정 글이 공개되지 않음 · "
            "지금 할 일: 운영센터에서 차단 항목 조치 · 처리 기한: 오늘 중",
            tuple(blocks),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        # A due slot remains the same operational state across morning batches and
        # calendar days. Re-page only when its schedule, safe cause, blocker code,
        # tenant identity, or persisted generation-attempt fingerprint changes.
        dedupe_key=f"{_GENERATION_BLOCKED_DIGEST_DEDUPE_PREFIX}v2:{digest}",
        notification_type=GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


def build_generation_rejection_weekly_rollup_intent(
    week_start: date,
    rejected_outcomes: Sequence[Mapping[str, object]],
    yield_facts: Sequence[HospitalYieldView] = (),
) -> NotificationIntent:
    """Build one hospital-and-reason summary for unresolved rejection episodes.

    수율 줄은 **같은 메시지 맨 위**에 붙는다. 새 Slack 메시지·새 주기를 만들지
    않는다(docs/ops/slack-notification-policy.md). 차단이 0건이어도 계약 예정 슬롯이
    있으면 이 한 건은 나간다 — 대표가 "이번 주에 몇 편 나왔는가"를 볼 곳이 여기뿐이다.
    """

    yield_lines, published_total, due_total = _yield_lines(yield_facts)
    if not rejected_outcomes and not yield_lines:
        raise NotificationPayloadError("GENERATION_REJECTION_WEEKLY_ITEMS_REQUIRED")
    hospitals: dict[tuple[str, str], dict[str, int]] = {}
    item_count = 0
    for outcome in rejected_outcomes:
        hospital_id = str(outcome.get("hospital_id") or "")
        hospital_name = str(outcome.get("hospital_name") or "이름 미확인 병원")
        reason = str(outcome.get("reason") or "생성 검수 차단 원인을 확인해야 합니다.")
        counts = hospitals.setdefault((hospital_id, hospital_name), {})
        counts[reason] = counts.get(reason, 0) + 1
        item_count += 1

    shown = sorted(hospitals.items())[:_DIGEST_MAX_HOSPITALS]
    hidden = len(hospitals) - len(shown)
    lines: list[str] = []
    for (_hospital_id, hospital_name), reason_counts in shown:
        details = " · ".join(
            f"{_publish_safe_text(reason, 110)} {count}건"
            for reason, count in sorted(reason_counts.items())[:_DIGEST_MAX_ITEMS_PER_HOSPITAL]
        )
        remaining_reasons = len(reason_counts) - min(
            len(reason_counts), _DIGEST_MAX_ITEMS_PER_HOSPITAL
        )
        if remaining_reasons > 0:
            details = f"{details} · 그 외 원인 {remaining_reasons}개"
        lines.append(f"• *{_publish_safe_text(hospital_name, 100)}*\n  {details}")
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}곳")

    week_end = week_start + timedelta(days=6)
    blocked_summary = (
        f"병원 {len(hospitals)}곳 · 차단 {item_count}건" if item_count else "차단 없음"
    )
    yield_summary = f"발행 {published_total}/{due_total}" if yield_lines else ""
    summary = " · ".join(part for part in (yield_summary, blocked_summary) if part)
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=incidents&status=OPEN")
    yield_blocks = (
        (
            section_block(
                "generation_rejection_weekly_yield_summary",
                f"*계약 예정 대비 발행 · {yield_summary}*",
            ),
            *(
                section_block(f"generation_rejection_weekly_yield_{index}", chunk)
                for index, chunk in enumerate(chunk_lines(yield_lines), start=1)
            ),
        )
        if yield_lines
        else ()
    )
    blocked_blocks = (
        *(
            section_block(f"generation_rejection_weekly_items_{index}", chunk)
            for index, chunk in enumerate(chunk_lines(lines), start=1)
        ),
    )
    next_action = (
        "운영센터에서 원인과 승인 자료 확인"
        if item_count
        else "운영센터에서 병원별 발행 수율 확인"
    )
    message = validated_message(
        RenderedSlackMessage(
            f"무슨 문제인지: 주간 콘텐츠 발행 현황 {summary} · "
            "고객 영향: 차단된 원고가 자동 발행 준비를 마치지 못함 · "
            f"지금 할 일: {next_action} · 처리 기한: 이번 주",
            (
                header_block("generation_rejection_weekly_header", "주간 콘텐츠 발행 요약"),
                section_block(
                    "generation_rejection_weekly_summary",
                    f"*{week_start.isoformat()}–{week_end.isoformat()} · {summary}*",
                ),
                *yield_blocks,
                *blocked_blocks,
                action_block(
                    "generation_rejection_weekly_action",
                    action_url,
                    "운영센터에서 원인 확인",
                ),
            ),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        # A calendar-week key guarantees at most one rollup even if RedBeat or an
        # operator dispatches the task twice. Per-item incident fingerprints still
        # suppress tick-level repeats before they reach this projection.
        dedupe_key=(
            f"{_GENERATION_REJECTION_WEEKLY_ROLLUP_DEDUPE_PREFIX}"
            f"{week_start.isoformat()}"
        ),
        notification_type=GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE,
        message=message,
        max_attempts=3,
    )


def _generation_blocked_display_title(title: object, code: object) -> str:
    visible_title = str(title or "").strip()
    if visible_title:
        return visible_title
    if str(code or "") == "GENERATION_REJECTED":
        return "생성 검수 게이트 거절"
    return "제목 없는 콘텐츠"


def _publish_safe_text(value: str, limit: int) -> str:
    return (
        safe_text(value, limit)
        .replace("[storage path redacted]", "[경로 숨김]")
        .replace("[email redacted]", "[이메일 숨김]")
        .replace("[phone redacted]", "[연락처 숨김]")
    )


def enqueue_missing_approved_essence_digest_sync(
    db: Session,
    cycle_date: date,
    skipped_outcomes: Sequence[Mapping[str, object]],
) -> NotificationOutbox:
    """Add at most one onboarding skip digest for the Seoul calendar date."""

    intent = build_missing_approved_essence_digest_intent(cycle_date, skipped_outcomes)
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def enqueue_generation_blocked_digest_sync(
    db: Session,
    cycle_date: date,
    batch: str,
    blocked_outcomes: Sequence[Mapping[str, object]],
    reused_outcomes: Sequence[Mapping[str, object]] = (),
) -> NotificationOutbox | None:
    """Add at most one digest for an unchanged blocked-publication set."""

    if not blocked_outcomes and not reused_outcomes:
        return None
    intent = build_generation_blocked_digest_intent(
        cycle_date, batch, blocked_outcomes, reused_outcomes=reused_outcomes
    )
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def enqueue_generation_rejection_weekly_rollup_sync(
    db: Session,
    week_start: date,
    rejected_outcomes: Sequence[Mapping[str, object]],
    *,
    yield_facts: Sequence[HospitalYieldView] = (),
) -> NotificationOutbox | None:
    """Add at most one weekly rollup for a Seoul calendar week.

    주 시작일 하나가 중복 키이므로 재디스패치해도 그 주의 outbox 행은 하나다.
    차단이 없어도 계약 예정 슬롯이 있으면 수율 한 건으로 나간다. 둘 다 없으면
    보낼 사실이 없으므로 아무 행도 만들지 않는다.
    """

    if not rejected_outcomes and not any(
        fact.due or fact.published for fact in yield_facts
    ):
        return None
    intent = build_generation_rejection_weekly_rollup_intent(
        week_start, rejected_outcomes, yield_facts
    )
    existing = db.execute(
        select(NotificationOutbox).where(NotificationOutbox.dedupe_key == intent.dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return _enqueue_notification_sync(db, intent)


def _enqueue_notification_sync(db: Session, intent: NotificationIntent) -> NotificationOutbox:
    now = datetime.now(UTC)
    row = NotificationOutbox(
        hospital_id=intent.hospital_id,
        incident_id=intent.incident_id,
        operation_run_id=intent.operation_run_id,
        dedupe_key=intent.dedupe_key,
        notification_type=intent.notification_type,
        channel=intent.channel,
        state=NotificationOutboxState.PENDING.value,
        payload=intent.message.payload(),
        fallback_text=intent.message.fallback_text,
        max_attempts=intent.max_attempts,
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row


def parse_publish_notification_identity(key: str) -> PublishNotificationIdentity | None:
    if not key.startswith(_DEDUPE_PREFIX):
        return None
    parts = key.split(":")
    if len(parts) != 3:
        return None
    try:
        return PublishNotificationIdentity(
            content_id=uuid.UUID(parts[1]),
            published_at=_EPOCH + timedelta(microseconds=int(parts[2])),
        )
    except (ValueError, OverflowError):
        return None


def project_publish_notification(
    state: PublishNotificationState | None,
    *,
    notification_id: uuid.UUID | None,
    safe_error_code: str | None,
) -> PublishNotificationProjection:
    identity = str(notification_id) if notification_id else None
    common = {
        "publication_impact": "콘텐츠 발행에는 영향이 없습니다.",
        "notification_id": identity,
        "safe_error_code": safe_error_code,
    }
    match state:
        case NotificationOutboxState.SENT.value:
            return {"state": "SENT", "label": "Slack 전달 완료", "problem": None,
                    "next_action": "공개된 글에 문제가 없는지 확인해 주세요.", **common}
        case NotificationOutboxState.FAILED.value:
            return {"state": "FAILED", "label": "Slack 전달 실패",
                    "problem": "Slack 운영 알림 전송에 실패했습니다.",
                    "next_action": "운영센터에서 실패 원인을 확인하고 알림을 다시 시도해 주세요.", **common}
        case NotificationOutboxState.HOLD.value:
            return {"state": "HOLD", "label": "전송 결과 확인 필요",
                    "problem": "Slack 수신 여부를 자동으로 확정하지 못했습니다.",
                    "next_action": "Slack 수신 내역을 확인한 뒤 운영센터에서 다음 조치를 선택해 주세요.", **common}
        case NotificationOutboxState.RETRYING.value:
            return {"state": "RETRYING", "label": "Slack 재시도 예정", "problem": None,
                    "next_action": "자동 재시도를 기다려 주세요.", **common}
        case NotificationOutboxState.PENDING.value | NotificationOutboxState.SENDING.value:
            return {"state": state, "label": "Slack 전달 대기", "problem": None,
                    "next_action": "잠시 후 자동으로 전달됩니다.", **common}
        case None:
            return {"state": "NOT_REQUIRED", "label": "자동 관제 중",
                    "problem": None,
                    "next_action": "문제가 감지된 항목만 예외 큐에 표시됩니다.", **common}
        case unreachable:
            assert_never(unreachable)


def _publish_dedupe_key(content_id: uuid.UUID, published_at: datetime) -> str:
    return f"{_DEDUPE_PREFIX}{content_id}:{_publication_epoch_micros(published_at)}"


def _publication_epoch_micros(published_at: datetime) -> int:
    normalized = published_at.astimezone(UTC)
    elapsed = normalized - _EPOCH
    return ((elapsed.days * 86_400) + elapsed.seconds) * 1_000_000 + elapsed.microseconds
