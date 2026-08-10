from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import joinedload

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus

NIGHTLY_GENERATION_CAP = 50
NIGHTLY_GENERATION_CLAIM_TTL_HOURS = 2

GENERATION_CATCHUP_DAYS = 7

# 생성 결과를 되쓸 수 있는 상태. 그 외(CANCELLED/PUBLISHED 등)는 운영자·발행 파이프라인이
# 이미 확정한 상태이므로 야간 배치가 덮어쓰면 안 된다.
GENERATION_WRITE_BACK_STATUSES = (ContentStatus.DRAFT, ContentStatus.REJECTED)


def write_back_generated_content(db, *, item_id, values: dict[str, Any]) -> int:
    """생성 결과를 **상태 가드와 함께** 쓴다. 반환값은 갱신된 행 수.

    0이면 생성이 도는 동안 운영자가 상태를 바꾼 것(취소 등)이므로 호출부는 결과를 버려야 한다.

    왜 ORM 객체를 직접 변경하지 않는가: claim 커밋 시점에 행 잠금이 풀리고 세션은
    `expire_on_commit=False`라, 추적 객체에 먼저 값을 넣으면 SQLAlchemy가 다음
    execute/commit 앞에서 autoflush로 그것을 먼저 써버려 가드가 무력화된다.
    반드시 이 함수 하나로만 쓰고, 추적 객체는 이후 refresh 한다.
    """
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


# 야간 생성 대상 병원 상태 — PAUSED/ONBOARDING 병원은 생성 비용을 발생시키지 않도록 제외.
NIGHTLY_GENERATION_HOSPITAL_STATUSES = (HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN)


def _nightly_generation_claim_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)


def _nightly_generation_claim_filter(claim_cutoff: datetime):
    return or_(
        ContentItem.generation_claimed_at.is_(None),
        ContentItem.generation_claimed_at < claim_cutoff,
    )


def _needs_generation_recovery():
    return or_(
        ContentItem.body.is_(None),
        ContentItem.image_url.is_(None),
        ContentItem.essence_check_summary["blocking"].as_boolean().is_(True),
    )


def _nightly_generation_stmt(window_start, window_end, claim_cutoff: datetime | None = None):
    claim_cutoff = claim_cutoff or _nightly_generation_claim_cutoff()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date >= window_start,
            ContentItem.scheduled_date <= window_end,
            ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
            _needs_generation_recovery(),
            Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
            _nightly_generation_claim_filter(claim_cutoff),
        )
        .order_by(
            ContentItem.carried_over_from.is_not(None).desc(),
            ContentItem.scheduled_date,
            ContentItem.sequence_no,
        )
        .options(joinedload(ContentItem.hospital))
        .with_for_update(skip_locked=True, of=ContentItem)
        .limit(NIGHTLY_GENERATION_CAP + 1)
    )


def _load_nightly_generation_batch(db, window_start, window_end) -> tuple[list, int]:
    now = datetime.now(timezone.utc)
    claim_cutoff = now - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
    result = db.execute(_nightly_generation_stmt(window_start, window_end, claim_cutoff))
    items = list(result.scalars().all())
    truncated_count = 0
    if len(items) > NIGHTLY_GENERATION_CAP:
        overflow = db.execute(
            select(func.count())
            .select_from(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                ContentItem.scheduled_date >= window_start,
                ContentItem.scheduled_date <= window_end,
                ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
                _needs_generation_recovery(),
                Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
                _nightly_generation_claim_filter(claim_cutoff),
            )
        ).scalar_one()
        truncated_count = max(int(overflow) - NIGHTLY_GENERATION_CAP, 1)
    claimed_items = items[:NIGHTLY_GENERATION_CAP]
    for item in claimed_items:
        # SQLAlchemy에 영속화되지 않는 시도 메타데이터. 새 배치가 만료 claim을
        # 인수했는지와 finally가 해제할 정확한 lease 시각을 호출부에 전달한다.
        item._generation_reclaimed_stale = getattr(item, "generation_claimed_at", None) is not None
        item.generation_claimed_at = now
    if claimed_items:
        db.commit()
    return claimed_items, truncated_count


def release_unfinished_claims(
    db,
    item_ids: list,
    *,
    expected_claimed_at: datetime | None = None,
) -> int:
    """생성되지 않은 채 남은 claim을 즉시 해제한다. 반환값은 해제된 건수.

    claim은 커밋으로 내구화되는데(잠금 해제를 위해 필요하다) 실패 시 해제 경로가
    없었다. 워커가 중간에 죽으면 남은 슬롯이 TTL(2시간)까지 잠기고, 그 사이 재배달된
    실행은 claim 필터에 걸려 아무것도 못 잡은 채 "생성할 것 없음"으로 **성공 종료**한다.
    다음 기회는 다음날 밤이라 슬롯이 하루 밀리고, 그 사이 08:00 자동 발행은 body가
    없어 아무것도 발행하지 못한다. 그래서 끝날 때 반드시 되돌린다.
    """
    if not item_ids:
        return 0
    predicates = [
        ContentItem.id.in_(item_ids),
        _needs_generation_recovery(),
        ContentItem.generation_claimed_at.isnot(None),
    ]
    if expected_claimed_at is not None:
        predicates.append(ContentItem.generation_claimed_at == expected_claimed_at)
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(generation_claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def load_stuck_claims(db, window_start, window_end) -> list[ContentItem]:
    """아직 만료되지 않은 claim 때문에 이번 배치가 건너뛴 슬롯.

    배치가 빈손으로 끝났을 때 "정말 할 일이 없는 것"과 "직전 실행이 죽어 claim이
    잠긴 것"을 구분하기 위한 값이다. 구분하지 않으면 한 달치 유실도 조용히 성공으로 보고된다.
    """
    claim_cutoff = _nightly_generation_claim_cutoff()
    return list(
        db.execute(
            select(ContentItem)
            .join(Hospital, ContentItem.hospital_id == Hospital.id)
            .where(
                ContentItem.scheduled_date >= window_start,
                ContentItem.scheduled_date <= window_end,
                ContentItem.status.in_([ContentStatus.DRAFT, ContentStatus.REJECTED]),
                _needs_generation_recovery(),
                Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
                ContentItem.generation_claimed_at.isnot(None),
                ContentItem.generation_claimed_at >= claim_cutoff,
            )
            .options(joinedload(ContentItem.hospital))
        ).scalars().all()
    )
