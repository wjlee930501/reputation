"""정체 감지기(Full-Automatic W10, 설계 §3.5) — 멈춘 임박 슬롯을 다음 계단으로 민다.

48시간 안(예정일 ≤ 오늘+2, 뒤로는 발행 catch-up 7일)에 예정된 미발행 슬롯이 생성 작업을 아직
필요로 하는데(`_needs_generation_recovery`) 살아 있는 claim이 없고 6시간 동안 진전이 없으면, 사람을
부르지 않고 **기존 정책의** 다음 계단을 한 번 부른다. 새 예산·새 정책을 만들지 않는다:

- S0 시도 기록을 읽을 수 없음(`LEGACY_SPEND_UNKNOWN`): D22 교체 함수(`apply_legacy_budget_replacement_sync`).
- S1 다음 시도 시각이 미래인데 예정일 ≤ 내일: 시각을 지금으로 당긴다. 하루 예산·물러서기 판정
  (`retry_is_due`)은 그대로라 당겨도 예산 밖 구매는 없다.
- S2/S3 주제 교체 후보(`topic_swap_fallback.exhausted_body_sample_reason`): 교체 pass를 그 창에 돌린다.
- S4 비-Critical 일일 갱신 대상(`noncritical_renewal.renewal_due` — 교체·평생 상한 소진, 오늘 구간 없음):
  같은 pass의 갱신 단계가 오늘 구간을 열고 곧바로 생성으로 보낸다. Critical 원인은 대상이 아니다.
  현재 주제의 작가 예산을 다 쓴 슬롯(`current_topic_writers_exhausted`)은 당기지도 보내지도 않고, 교체
  예산이 남았으면 곧바로 교체(`_swap_one`), 없으면 기존 사다리에 맡긴다 — 살 수 없는 생성은 보내지 않는다.
- 그 밖에 지금 다시 시도할 수 있는 슬롯(`tasks._generation_retry_is_eligible`): claim하고 글 단위
  생성 태스크로 보낸다(복구 스윕과 같은 경로, `notify=False`).

멱등: 행동마다 `CONTENT_STALL_ACTION` 실행 기록을 `CONTENT_STALL:{글}:{계단}:{KST 날짜}` 키로 남기고,
같은 키가 있으면 다시 하지 않는다 — 슬롯·계단·날짜당 한 번이라 무한 루프가 없다. Slack은 만들지
않는다. 계단이 끝까지 소진된 Critical 원인의 알림은 기존 생성 사고 규칙이 소유하고, 비-Critical
원인은 사람에게 가지 않고 일일 갱신이 잇는다.
15분 `reconcile_essence_snapshots` 틱에 붙어 돈다(RedBeat 판 불변).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.services.post_publish_review_policy import (
    auto_publish_catchup_start,
    auto_publish_hold,
)
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    LEGACY_SPEND_UNKNOWN_CODE,
    current_topic_writers_exhausted,
    read_generation_attempt,
)
from app.workers.generation_retry_policy import GenerationRetryClass
from app.workers.nightly_generation_batch import (
    GENERATION_WRITE_BACK_STATUSES,
    NIGHTLY_GENERATION_CLAIM_TTL_HOURS,
    NIGHTLY_GENERATION_HOSPITAL_STATUSES,
    _needs_generation_recovery,
    _nightly_generation_claim_filter,
    claim_generation_lease,
    generation_claim_is_active,
)

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
STALL_ACTOR = "system:stall-detector"
STALL_OPERATION_TYPE = "CONTENT_STALL_ACTION"
STALL_LOOKAHEAD_DAYS = 2
STALL_IDLE = timedelta(hours=6)
STALL_SCAN_LIMIT = 200
# 한 틱이 생성 태스크로 보내는 슬롯 상한. 나머지는 다음 틱(15분)이 잇는다.
STALL_DISPATCH_LIMIT = 10

RUNG_LEGACY = "LEGACY_RESET"
RUNG_PULL = "PULL_RETRY"
RUNG_SWAP = "TOPIC_SWAP"
RUNG_DISPATCH = "DISPATCH"
RUNG_RENEWAL = "DAILY_RENEWAL"


@dataclass
class StallReport:
    stalled: int = 0
    actions: dict[str, int] = field(default_factory=dict)

    def count(self, rung: str) -> None:
        self.actions[rung] = self.actions.get(rung, 0) + 1

    def as_dict(self) -> dict[str, int]:
        return {
            "stall_detected": self.stalled,
            **{f"stall_{k.lower()}": v for k, v in self.actions.items()},
        }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def last_progress_at(item: ContentItem) -> datetime | None:
    """슬롯이 마지막으로 움직인 시각 — 생성·본문 저장·claim·시도 기록 중 가장 늦은 것."""

    attempt = read_generation_attempt(item)
    moments = [
        _aware(getattr(item, "generated_at", None)),
        _aware(getattr(item, "body_updated_at", None)),
        _aware(getattr(item, "generation_claimed_at", None)),
        _aware(getattr(item, "created_at", None)),
        _parse(attempt.get("observed_at")),
    ]
    history = getattr(item, "topic_swap_history", None)
    if isinstance(history, list):
        moments.extend(
            _parse(entry.get("swapped_at")) for entry in history if isinstance(entry, dict)
        )
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def is_stalled(item: ContentItem, *, now: datetime) -> bool:
    if generation_claim_is_active(item, now=now):
        return False
    progressed = last_progress_at(item)
    return progressed is None or progressed < now - STALL_IDLE


def stall_window(now: datetime) -> tuple[date, date]:
    today = now.astimezone(KST).date()
    return auto_publish_catchup_start(today), today + timedelta(days=STALL_LOOKAHEAD_DAYS)


def find_stalled_items(db: Session, *, now: datetime) -> list[ContentItem]:
    window_start, window_end = stall_window(now)
    claim_cutoff = now - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
    hold = auto_publish_hold()
    rows = (
        db.execute(
            select(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                ContentItem.scheduled_date >= window_start,
                ContentItem.scheduled_date <= window_end,
                ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
                ContentItem.first_published_at.is_(None),
                Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
                Hospital.site_live.is_(True),
                _nightly_generation_claim_filter(claim_cutoff),
                _needs_generation_recovery(),
            )
            .order_by(ContentItem.scheduled_date, ContentItem.sequence_no, ContentItem.id)
            .options(joinedload(ContentItem.hospital))
            .limit(STALL_SCAN_LIMIT)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .unique()
        .all()
    )
    return [item for item in rows if not hold.holds(item.hospital_id) and is_stalled(item, now=now)]


def _claim_action(db: Session, item: ContentItem, rung: str, *, now: datetime) -> bool:
    """이 슬롯·계단·KST 날짜의 첫 행동이면 실행 기록을 남기고 True. 커밋한다."""

    key = f"CONTENT_STALL:{item.id}:{rung}:{now.astimezone(KST):%Y%m%d}"
    exists = db.scalar(
        select(OperationRun.id).where(
            OperationRun.operation_type == STALL_OPERATION_TYPE,
            OperationRun.idempotency_key == key,
        )
    )
    if exists is not None:
        return False
    db.add(
        OperationRun(
            id=uuid.uuid4(),
            hospital_id=item.hospital_id,
            operation_type=STALL_OPERATION_TYPE,
            state=OperationRunState.SUCCEEDED.value,
            idempotency_key=key,
            total_count=1,
            success_count=1,
            request_payload={
                "content_id": str(item.id),
                "rung": rung,
                "actor": STALL_ACTOR,
                "scheduled_date": item.scheduled_date.isoformat() if item.scheduled_date else None,
            },
            requested_at=now,
            started_at=now,
            completed_at=now,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _retry_pull_due(item: ContentItem, *, now: datetime) -> bool:
    attempt = read_generation_attempt(item)
    if attempt.get("retry_class") not in (
        GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
    ):
        return False
    due = _parse(attempt.get("next_retry_at"))
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)
    return (
        due is not None
        and due > now
        and item.scheduled_date is not None
        and item.scheduled_date <= tomorrow
    )


def _pull_retry(db: Session, item_id: uuid.UUID, *, now: datetime) -> bool:
    item = db.execute(
        select(ContentItem)
        .where(ContentItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        item is None
        or generation_claim_is_active(item, now=now)
        or not _retry_pull_due(item, now=now)
    ):
        db.rollback()
        return False
    summary = dict(item.essence_check_summary or {})
    attempt = dict(summary.get(GENERATION_ATTEMPT_KEY) or {})
    attempt["next_retry_at"] = now.isoformat()
    attempt["pulled_by"] = STALL_ACTOR
    summary[GENERATION_ATTEMPT_KEY] = attempt
    item.essence_check_summary = summary
    db.commit()
    return True


def _legacy_reset(db: Session, item: ContentItem, *, now: datetime) -> bool:
    from app.services.legacy_budget_replacement import apply_legacy_budget_replacement_sync

    result = apply_legacy_budget_replacement_sync(
        db,
        item.id,
        actor=STALL_ACTOR,
        reason="정체 감지기: 시도 기록을 읽을 수 없어 멈춘 임박 슬롯",
        idempotency_key=f"stall-detector:{item.id}",
        now=now,
    )
    if result.outcome == "REPLACED":
        db.commit()
        return True
    db.rollback()
    return False


def _swap_writer_exhausted(db: Session, item_id: uuid.UUID, *, now: datetime) -> bool:
    """작가 예산을 다 쓴 슬롯의 주제를 한 번 바꾼다(교체 pass와 같은 `_swap_one`·경계)."""

    from app.workers.topic_swap_fallback import (
        BUDGET_EXHAUSTED_REASON,
        _swap_one,
        topic_swap_budget_left,
    )

    item = db.execute(
        select(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
            ContentItem.first_published_at.is_(None),
            ContentItem.human_edited_at.is_(None),
        )
        .options(joinedload(ContentItem.hospital))
        .with_for_update(of=ContentItem)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        item is None
        or generation_claim_is_active(item, now=now)
        or not current_topic_writers_exhausted(read_generation_attempt(item))
        or not topic_swap_budget_left(item)
    ):
        db.rollback()
        return False
    entry, label = _swap_one(db, item, BUDGET_EXHAUSTED_REASON, now=now)
    if entry is None:
        db.rollback()
        logger.info("stall detector could not swap writer-exhausted slot %s: %s", item_id, label)
        return False
    db.commit()
    return True


def run_stall_detector(db: Session, *, now: datetime | None = None) -> StallReport:
    """한 번의 점검. 행동은 슬롯·계단·날짜당 한 번."""

    from app.workers import tasks
    from app.workers.generation_batch_run import GenerationBatchRecorder
    from app.workers.noncritical_renewal import renewal_due, renewal_waiting
    from app.workers.topic_swap_fallback import (
        exhausted_body_sample_reason,
        swap_exhausted_topics,
        topic_swap_budget_left,
    )

    observed = now or datetime.now(UTC)
    report = StallReport()
    stalled = find_stalled_items(db, now=observed)
    report.stalled = len(stalled)
    if not stalled:
        db.rollback()
        return report
    window_start, window_end = stall_window(observed)

    swap_needed = False
    swap_ids: list[uuid.UUID] = []
    dispatch_ids: list[uuid.UUID] = []
    for item in stalled:
        attempt = read_generation_attempt(item)
        if attempt.get("reason") == LEGACY_SPEND_UNKNOWN_CODE:
            if _claim_action(db, item, RUNG_LEGACY, now=observed) and _legacy_reset(
                db, item, now=observed
            ):
                report.count(RUNG_LEGACY)
            continue
        if tasks.legacy_tier_rereview_due(item):
            # 등급 없는 옛 차단 검수 — 교체·당기기보다 먼저 v3 재검수 한 번을 사러 보낸다(누락 방지 3).
            # 생성 경로가 표본 예산 밖에서 본문 hash당 한 번만 산다(`tasks.legacy_tier_rereview_due`).
            dispatch_ids.append(item.id)
            continue
        if exhausted_body_sample_reason(item) is not None:
            if _claim_action(db, item, RUNG_SWAP, now=observed):
                swap_needed = True
                swap_ids.append(item.id)
                report.count(RUNG_SWAP)
            continue
        if renewal_due(item, observed):
            # 교체·평생 상한을 다 쓴 비-Critical 슬롯 — 오늘의 일일 갱신 구간을 열고 곧바로 생성으로 보낸다.
            # 갱신 단계는 교체 pass 안에서 돈다(구간은 슬롯·KST 날짜당 한 번).
            if _claim_action(db, item, RUNG_RENEWAL, now=observed):
                swap_needed = True
                swap_ids.append(item.id)
                report.count(RUNG_RENEWAL)
            continue
        if renewal_waiting(item, observed):
            # 오늘 구간을 쓴 비-Critical 슬롯 — 다음 갱신까지 당기지도 보내지도 않는다(살 것이 없다).
            continue
        if current_topic_writers_exhausted(attempt):
            # 현재 주제가 작가 결과를 더 살 수 없다 — 당기거나 생성으로 보내면 공급자 호출 없이
            # `WRITER_RESULTS_EXHAUSTED`로 끝난다. 교체 예산이 남았으면 곧바로 교체 계단으로,
            # 없으면 기존 사다리(소진 일수·사람의 일)에 맡긴다.
            if (
                topic_swap_budget_left(item)
                and _claim_action(db, item, RUNG_SWAP, now=observed)
                and _swap_writer_exhausted(db, item.id, now=observed)
            ):
                swap_needed = True
                swap_ids.append(item.id)
                report.count(RUNG_SWAP)
            continue
        if _retry_pull_due(item, now=observed):
            if _claim_action(db, item, RUNG_PULL, now=observed) and _pull_retry(
                db, item.id, now=observed
            ):
                report.count(RUNG_PULL)
        dispatch_ids.append(item.id)

    if swap_needed:
        # 교체 pass는 그 창의 모든 후보를 자기 규칙(상한·이력·공개/사람 편집 경계)대로 다룬다.
        swap_exhausted_topics(db, window_start=window_start, window_end=window_end)
        # 바뀐 주제는 다음 예약 스윕을 기다리지 않고 같은 점검에서 시도한다(교체 직후 예산 판정은
        # 생성 경로가 한다 — 적격이 아니면 `_dispatch`가 건너뛴다).
        dispatch_ids.extend(swap_ids)

    dispatched = _dispatch(
        db, tasks, GenerationBatchRecorder, dispatch_ids, observed, window_start, window_end
    )
    for _ in range(dispatched):
        report.count(RUNG_DISPATCH)
    if report.actions:
        logger.info("content stall detector acted: %s", report.as_dict())
    return report


def _dispatch(db, tasks, recorder_cls, item_ids, observed, window_start, window_end) -> int:
    if not item_ids:
        return 0
    eligible = tasks._generation_retry_is_eligible(db)
    recorder = None
    dispatched = 0
    for item_id in item_ids:
        if dispatched >= STALL_DISPATCH_LIMIT:
            break
        item = db.execute(
            select(ContentItem)
            .where(ContentItem.id == item_id)
            .options(joinedload(ContentItem.hospital))
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if item is None or not eligible(item):
            continue
        if current_topic_writers_exhausted(read_generation_attempt(item)):
            # 살 수 없는 생성은 보내지 않는다(교체가 후보를 찾지 못한 슬롯 포함).
            continue
        if not _claim_action(db, item, RUNG_DISPATCH, now=observed):
            continue
        leased = claim_generation_lease(db, item_id, now=observed)
        if leased is None:
            continue
        leased_item, token = leased
        leased_item._generation_claim_token = token
        if recorder is None:
            recorder = recorder_cls(db, f"stall:{observed:%Y%m%dT%H%M}", window_start, window_end)
        if tasks._dispatch_generation_item(db, recorder, leased_item, notify=False):
            dispatched += 1
    if recorder is not None:
        recorder.finish()
    return dispatched
