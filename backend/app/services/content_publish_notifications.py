"""Durable publication notification intent, projection, and delivery stamp."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.operations import (
    Incident,
    NotificationOutbox,
    NotificationOutboxState,
    OperationRun,
    OperationRunState,
)
from app.services.notification_contracts import (
    NotificationIntent,
    NotificationPayloadError,
)
from app.services.notification_copy import REFERENCES_OPERATOR_DECIDES_COPY_CODES, blocker_copy
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

MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE = (
    "MISSING_APPROVED_ESSENCE_DIGEST"
)
GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE = "GENERATION_BLOCKED_DIGEST"
GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE = (
    "GENERATION_REJECTION_WEEKLY_ROLLUP"
)
# 새로 막힌 글을 이미 알렸다는 기록(`_newly_blocked_outcomes`)의 실행 종류.
_BLOCKED_NOTICE_OPERATION = "GENERATION_BLOCKED_NOTICE"
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
class HospitalYieldView(Protocol):
    """주간 요약이 읽는 수율 사실의 모양(`services/content_yield.HospitalYieldFact`)."""

    hospital_name: str
    due: int
    published: int
    published_with_reused_image: int
    retrying: int
    topic_swapped: int
    operator_required: int


def _yield_lines(yield_facts: Sequence[HospitalYieldView]) -> tuple[list[str], int, int]:
    """병원별 '발행 n/예정 m' 한 줄. 부족분이 큰 병원이 위로 온다.

    계약도 발행도 없는 병원은 줄을 만들지 않는다 — 대표가 보는 것은 계약이 있는
    병원의 이행이지 빈 행의 목록이 아니다. `재시도 중`과 `주제 교체`는 자동 복구가
    소유한 수이고 `조치 필요`만 사람의 일이다(세 수를 한 줄에 같이 두는 이유).
    """

    active = [fact for fact in yield_facts if fact.due or fact.published]
    ranked = sorted(
        active,
        key=lambda fact: (-(fact.due - fact.published), fact.hospital_name),
    )
    shown = ranked[:_YIELD_MAX_HOSPITALS]
    lines = []
    for fact in shown:
        details = [f"발행 {fact.published}/{fact.due}편"]
        if fact.retrying:
            details.append(f"자동 재시도 {fact.retrying}편")
        if fact.operator_required:
            details.append(f"확인 필요 {fact.operator_required}편")
        if fact.published_with_reused_image:
            details.append(f"대체 이미지 사용 {fact.published_with_reused_image}편")
        lines.append(f"• *{_publish_safe_text(fact.hospital_name, 80)}* — " + " · ".join(details))
    hidden = len(ranked) - len(shown)
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}개")
    return (
        lines,
        sum(fact.published for fact in active),
        sum(fact.due for fact in active),
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
            prefixed_for_event(MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE, f"온보딩 생성 요약 · {summary} · 승인 기준이 없어 생성을 건너뜀"),
            (
                header_block("missing_essence_digest_header", prefixed_for_event(MISSING_APPROVED_ESSENCE_DIGEST_NOTIFICATION_TYPE, "온보딩 생성 요약")),
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
    "승인된 대체 이미지로 발행했습니다. 추가 조치는 필요하지 않습니다. "
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
            str(outcome.get("episode_seq") or ""),
        )
        for outcome in blocked_outcomes
    ]
    # 식별은 (병원, 글, 차단 코드, 사고 epoch)다. 예정일·원인 문구·시도 지문은 같은 차단 안에서도
    # 움직이므로 넣지 않는다 — 넣으면 새 글 하나가 더해질 때마다 이미 알린 글까지 전부 다시 나간다.
    identity = sorted(
        {
            f"{hospital_id}:{content_id}:{code}:{episode}"
            for (hospital_id, _, content_id, _, code, _, _, episode) in entries
        }
    )
    _reuse_identity, reuse_lines = _image_reuse_section(reused_outcomes)
    digest = hashlib.sha256(
        "\n".join([*identity, ""]).encode()
    ).hexdigest()[:32]
    hospitals: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for (
        (
            hospital_id,
            hospital_name,
            _content_id,
            _scheduled_date,
            code,
            cause,
            title,
            _episode,
        ),
        outcome,
    ) in zip(entries, blocked_outcomes, strict=True):
        # `copy_code`는 같은 차단 코드에 다른 문구만 고른다 — 식별자(dedupe)는 그대로 코드다.
        copy_code = str(outcome.get("copy_code") or code)
        hospitals.setdefault((hospital_id, hospital_name), []).append((title, copy_code, cause))
    for items in hospitals.values():
        # 사람이 정해야 하는 글의 조치를 맨 앞에 둔다. 나머지는 게이트 순서 그대로이고(안정 정렬),
        # 편수·제목 줄은 순서와 무관하다.
        items.sort(key=lambda item: item[1] not in REFERENCES_OPERATOR_DECIDES_COPY_CODES)
    action_url = admin_url(settings.ADMIN_BASE_URL, "/operations?queue=incidents&status=OPEN")
    shown = sorted(hospitals.items())[:_DIGEST_MAX_HOSPITALS]
    hidden = len(hospitals) - len(shown)
    lines = []
    for (_hospital_id, hospital_name), items in shown:
        counts = Counter(blocker_copy(code).title for _title, code, _cause in items)
        detail = " · ".join(f"{label} {count}편" for label, count in sorted(counts.items()))
        # 서로 다른 조치는 개수 상한 없이 모두 싣는다 — 잘린 조치는 운영자가 볼 방법이 없다.
        actions = list(dict.fromkeys(blocker_copy(code).action for _title, code, _cause in items))
        lines.append(f"• *{_publish_safe_text(hospital_name, 80)}* — 발행 보류 {len(items)}편\n  {detail}\n  {' '.join(actions)}")
    if hidden > 0:
        lines.append(f"• 그 외 {hidden}곳")
    summary = f"병원 {len(hospitals)}곳 · 글 {len(entries)}건"
    if not entries:
        # 차단이 하나도 없는 배치다 — 요약 줄은 대체 발행 건수만 말한다. 빌린 것과 병원
        # 대표 이미지를 쓴 것을 한 수로 세되, 어느 쪽인지는 아래 섹션 줄이 말한다.
        summary = f"대표 이미지 대체 발행 {len(reused_outcomes)}건"
    blocks = [
        header_block("generation_blocked_digest_header", prefixed_for_event(GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE, "[조치 필요] 발행을 마치지 못한 글" if entries else "[자동 처리] 대체 이미지 발행")),
        section_block("generation_blocked_digest_summary", f"*{summary}*"),
    ]
    for index, chunk in enumerate(chunk_lines(lines)):
        blocks.append(section_block(f"generation_blocked_digest_items_{index}", chunk))
    if reuse_lines and not entries:
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
            prefixed_for_event(GENERATION_BLOCKED_DIGEST_NOTIFICATION_TYPE, (f"[조치 필요] {summary} | " + " / ".join(_publish_safe_text(name, 60) for (_id, name), _items in shown)
             + " | 콘텐츠에서 해당 글의 사유를 확인해 주세요.") if entries else
            f"[자동 처리] {summary} | 추가 조치가 필요하지 않습니다."),
            tuple(blocks),
            action_url,
        ),
        settings.ADMIN_BASE_URL,
    )
    return NotificationIntent(
        # 같은 차단은 아침 배치와 날짜가 바뀌어도 같은 운영 상태다. 키는 새로 막힌 글의
        # (병원, 글, 코드, 사고 epoch)에서만 나온다 — `enqueue_generation_blocked_digest_sync`가
        # 이미 알린 글을 걸러 보내므로, 글 하나가 더해져도 앞서 알린 글이 다시 나가지 않는다.
        dedupe_key=f"{_GENERATION_BLOCKED_DIGEST_DEDUPE_PREFIX}v3:{digest}",
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
        if outcome.get("requires_action") is False:
            continue
        hospital_id = str(outcome.get("hospital_id") or "")
        hospital_name = str(outcome.get("hospital_name") or "이름 미확인 병원")
        reason = blocker_copy(outcome.get("code")).title
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
        lines.append(f"• *{_publish_safe_text(hospital_name, 100)}*\n  {details}\n  콘텐츠에서 차단 사유와 병원 근거 자료를 확인해 주세요.")
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
                f"*지난주 예정 글의 발행 실적 · {yield_summary}*",
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
        else "추가 조치 없음"
    )
    message = validated_message(
        RenderedSlackMessage(
            prefixed_for_event(GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE, f"[주간 요약] {week_start.isoformat()}~{week_end.isoformat()} · {summary} | {next_action}"),
            (
                header_block("generation_rejection_weekly_header", prefixed_for_event(GENERATION_REJECTION_WEEKLY_ROLLUP_NOTIFICATION_TYPE, "주간 콘텐츠 발행 요약")),
                section_block(
                    "generation_rejection_weekly_summary",
                    f"*{week_start.isoformat()}–{week_end.isoformat()} · {summary}*",
                ),
                *yield_blocks,
                section_block("generation_rejection_weekly_scope", "현재 확인할 일입니다. 아래 미해결 항목은 지난주 발행 대상과 기간이 다를 수 있습니다."
                    if item_count else "현재 확인이 필요한 생성 차단 항목은 없습니다."),
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


def _blocked_episode(db: Session, outcome: Mapping[str, object]) -> int:
    """이 글의 **이 차단**을 연 사고의 epoch — 주제 교체·복구 뒤 다시 막히면 올라간다.

    차단 코드가 같은 사고만 본다: 글 단위 사고, 없으면 병원 단위(승인 근거 없음 등) 사고.
    코드가 같은 사고가 없을 때만 그 글의 가장 높은 epoch로 물러난다. 병원 안의 다른 코드
    사고가 epoch를 올려도 이 글의 알림은 다시 나가지 않는다.
    """

    given = outcome.get("episode_seq")
    if isinstance(given, int):
        return given
    try:
        hospital_id = uuid.UUID(str(outcome.get("hospital_id")))
    except ValueError:
        return 0
    content_key = str(outcome.get("content_id"))
    base = (
        Incident.hospital_id == hospital_id,
        Incident.source_type == "CONTENT_GENERATION",
    )
    code = outcome.get("code")
    scopes: list[tuple] = []
    if code:
        scopes.append((Incident.source_id == content_key, Incident.safe_error_code == code))
        scopes.append((Incident.source_id == str(hospital_id), Incident.safe_error_code == code))
    scopes.append((Incident.source_id == content_key,))
    for scope in scopes:
        epoch = db.scalar(select(func.max(Incident.episode_seq)).where(*base, *scope))
        if epoch:
            return int(epoch)
    return 0


def _newly_blocked_outcomes(
    db: Session, blocked_outcomes: Sequence[Mapping[str, object]]
) -> list[Mapping[str, object]]:
    """(글, 차단 코드, 사고 epoch)마다 한 번만 알린다 — 이미 알린 글은 걸러 낸다.

    같은 글이 막힌 채 이어지는 동안은 아침마다·재시도 지문이 바뀔 때마다 다시 나갈 이유가 없고,
    계속 열린 항목은 월요일 주간 요약(GENERATION_REJECTION_WEEKLY_ROLLUP)이 맡는다. 알림 기록은
    `GENERATION_BLOCKED_NOTICE` 실행 한 줄이며 도메인 트랜잭션과 함께 커밋·롤백된다.
    """

    fresh: list[Mapping[str, object]] = []
    seen: set[str] = set()
    now = datetime.now(UTC)
    for outcome in blocked_outcomes:
        episode = _blocked_episode(db, outcome)
        # 차단 코드가 바뀐 같은 글은 다른 문제이므로 다시 알린다.
        key = f"{outcome.get('content_id')}:{outcome.get('code')}:{episode}"
        if key in seen:
            continue
        seen.add(key)
        already = db.scalar(
            select(OperationRun.id).where(
                OperationRun.operation_type == _BLOCKED_NOTICE_OPERATION,
                OperationRun.idempotency_key == key,
            )
        )
        if already is not None:
            continue
        db.add(
            OperationRun(
                operation_type=_BLOCKED_NOTICE_OPERATION,
                state=OperationRunState.SUCCEEDED.value,
                idempotency_key=key,
                attempt_count=1,
                started_at=now,
                completed_at=now,
            )
        )
        fresh.append({**outcome, "episode_seq": episode})
    return fresh


def enqueue_generation_blocked_digest_sync(
    db: Session,
    cycle_date: date,
    batch: str,
    blocked_outcomes: Sequence[Mapping[str, object]],
    reused_outcomes: Sequence[Mapping[str, object]] = (),
) -> NotificationOutbox | None:
    """Add one digest for the items that became blocked since the last one.

    The digest names only newly blocked (content, incident epoch) pairs. A set that is
    still blocked tomorrow is not re-announced; one new item pages only itself.
    """

    # 대체 이미지 발행만으로는 알리지 않는다 — 막힌 글이 새로 생긴 요약에 덧붙을 뿐이다.
    if not blocked_outcomes:
        return None
    fresh = _newly_blocked_outcomes(db, blocked_outcomes)
    if not fresh:
        return None
    intent = build_generation_blocked_digest_intent(
        cycle_date, batch, fresh, reused_outcomes=reused_outcomes
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
