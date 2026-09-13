import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import joinedload

from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus

NIGHTLY_GENERATION_CAP = 50
NIGHTLY_GENERATION_CLAIM_TTL_HOURS = 2
# 병원 간 라운드로빈을 할 수 있을 만큼만 후보를 더 읽는다. 상한만큼만 읽으면 정렬 순서상
# 앞선 한 병원이 그 안을 다 채운 뒤라 섞을 것이 남지 않는다. 읽고도 claim하지 않은 행은
# 커밋과 함께 잠금이 풀려 다음 스윕이 그대로 다시 본다.
NIGHTLY_GENERATION_SELECT_LIMIT = NIGHTLY_GENERATION_CAP * 2
# 지금 막 claim된 슬롯은 "잠긴 일감"이 아니라 진행 중인 일감이다. per-item 태스크의
# 벽시계 한계(1,000초)에 큐 대기 여유를 더한 이 시간이 지나도 끝나지 않은 claim만
# 이전 실행이 죽어 남긴 것으로 본다.
NIGHTLY_GENERATION_IN_FLIGHT_GRACE = timedelta(minutes=30)

# 생성 결과를 되쓸 수 있는 상태. 그 외(CANCELLED/PUBLISHED 등)는 운영자·발행 파이프라인이
# 이미 확정한 상태이므로 야간 배치가 덮어쓰면 안 된다.
GENERATION_WRITE_BACK_STATUSES = (
    ContentStatus.DRAFT,
    ContentStatus.REJECTED,
    ContentStatus.READY,
)


def write_back_generated_content(
    db,
    *,
    item_id,
    values: dict[str, Any],
    expected_revision: int | None = None,
    expected_claim_token: uuid.UUID | None = None,
) -> int:
    """생성 결과를 **상태 가드와 함께** 쓴다. 반환값은 갱신된 행 수.

    0이면 생성이 도는 동안 운영자가 상태를 바꾼 것(취소 등)이므로 호출부는 결과를 버려야 한다.

    왜 ORM 객체를 직접 변경하지 않는가: claim 커밋 시점에 행 잠금이 풀리고 세션은
    `expire_on_commit=False`라, 추적 객체에 먼저 값을 넣으면 SQLAlchemy가 다음
    execute/commit 앞에서 autoflush로 그것을 먼저 써버려 가드가 무력화된다.
    반드시 이 함수 하나로만 쓰고, 추적 객체는 이후 refresh 한다.
    """
    predicates = [
        ContentItem.id == item_id,
        ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
    ]
    if expected_revision is not None:
        predicates.append(ContentItem.content_revision == expected_revision)
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    guarded_values = dict(values)
    guarded_values["content_revision"] = ContentItem.content_revision + 1
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(**guarded_values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def write_back_generated_image(
    db,
    *,
    item_id,
    expected_title: str | None,
    values: dict[str, Any],
    expected_revision: int | None = None,
    expected_claim_token: uuid.UUID | None = None,
) -> int:
    """Persist a generated image only while its source title still matches."""
    title_clause = (
        ContentItem.title.is_(None)
        if expected_title is None
        else ContentItem.title == expected_title
    )
    predicates = [
        ContentItem.id == item_id,
        ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
        title_clause,
    ]
    if expected_revision is not None:
        predicates.append(ContentItem.content_revision == expected_revision)
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    guarded_values = dict(values)
    guarded_values.update(
        {
            "content_revision": ContentItem.content_revision + 1,
            "generation_claimed_at": None,
            "generation_claim_token": None,
        }
    )
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(**guarded_values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def write_back_published_image(
    db,
    *,
    item_id,
    expected_title: str | None,
    expected_revision: int,
    values: dict[str, Any],
) -> int:
    """공개 중인 글의 대표 이미지 자체를 바꾼다. 판(content_revision)은 올리지 않는다.

    빌려온 이미지를 그 글의 주제 이미지로 교체하는 사후 스윕이 쓴다. 본문 후보는 그대로이므로
    판을 올리지 않고(올리면 후보 hash에 매인 검수·인증이 통째로 무효가 된다), 대신 제목과
    판이 그대로일 때만 저장한다 — 그 사이 편집이 있었다면 그 편집이 자기 경로로 다시 요청한다.
    새 인증 필드(내용 hash·주제 hash·정책 버전·검수 시각)와 재사용 marker 해제는 호출부가
    한 `values`에 담아 **한 UPDATE로** 쓴다. 나눠 쓰면 그 사이에 marker만 풀린 미인증
    이미지가 공개 판정에 노출된다.
    """
    title_clause = (
        ContentItem.title.is_(None)
        if expected_title is None
        else ContentItem.title == expected_title
    )
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status == ContentStatus.PUBLISHED,
            title_clause,
            ContentItem.content_revision == expected_revision,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def write_back_published_image_certificate(
    db,
    *,
    item_id,
    expected_title: str | None,
    expected_revision: int,
    values: dict[str, Any],
) -> int:
    """공개 중인 글의 이미지 인증만 갱신한다. 판(content_revision)·claim은 건드리지 않는다.

    `write_back_generated_image`는 생성 상태(DRAFT/REJECTED/READY)만 대상이라 공개 글에는
    0행을 돌려준다. 공개 글의 재인증은 상태·제목·판이 그대로일 때만 저장한다 — 그 사이
    편집이 있었다면 그 편집이 다시 재인증을 요청한다.
    """
    # NULL 제목은 `= NULL`로 비교하면 항상 거짓이라, 바뀐 것이 없는데도 0행이 나와
    # CANCELLED로 끝나고 sweep이 같은 글을 다시 사는 고리가 된다.
    title_clause = (
        ContentItem.title.is_(None)
        if expected_title is None
        else ContentItem.title == expected_title
    )
    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.status == ContentStatus.PUBLISHED,
            title_clause,
            ContentItem.content_revision == expected_revision,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


# STEP 7 자동 생성은 공개 활성화가 끝난 병원만 대상으로 한다. PENDING_DOMAIN
# pre-warm은 공개되지 않을 초안에 비용을 쓰고 STEP 5/6 순서를 우회하므로 제외한다.
NIGHTLY_GENERATION_HOSPITAL_STATUSES = (HospitalStatus.ACTIVE,)


def _nightly_generation_claim_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)


def _nightly_generation_claim_filter(claim_cutoff: datetime):
    return or_(
        ContentItem.generation_claimed_at.is_(None),
        ContentItem.generation_claimed_at < claim_cutoff,
    )


def claim_generation_lease(
    db,
    item_id,
    *,
    now: datetime | None = None,
) -> tuple[ContentItem, uuid.UUID] | None:
    """Atomically claim one content row before any provider work."""

    observed_at = now or datetime.now(timezone.utc)
    item = db.execute(
        select(ContentItem)
        .where(ContentItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if item is None or item.status not in GENERATION_WRITE_BACK_STATUSES:
        db.rollback()
        return None
    claim_cutoff = observed_at - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
    if (
        item.generation_claim_token is not None
        and item.generation_claimed_at is not None
        and item.generation_claimed_at >= claim_cutoff
    ):
        db.rollback()
        return None
    claim_token = uuid.uuid4()
    item.generation_claimed_at = observed_at
    item.generation_claim_token = claim_token
    db.commit()
    return item, claim_token


def load_claimed_generation_item(
    db,
    item_id,
    claim_token: uuid.UUID,
    *,
    now: datetime | None = None,
) -> ContentItem | None:
    """Return the row only while *this* lease is still the current, unexpired one.

    배치가 claim한 뒤 per-item 태스크가 실행되기까지 큐 대기가 있고, 그 사이 운영자가
    취소하거나 lease가 만료돼 다른 스윕이 같은 슬롯을 다시 인수할 수 있다. 토큰이
    바뀌었거나 lease가 만료됐으면 공급자 호출을 한 번도 하지 않고 물러난다 — 늦게
    도착한 실행이 새 소유자의 결과를 덮어쓰지 못하게 하는 것과 같은 계약이다.
    """

    observed_at = now or datetime.now(timezone.utc)
    item = db.execute(
        select(ContentItem)
        .where(ContentItem.id == item_id)
        .options(joinedload(ContentItem.hospital))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if item is None or item.status not in GENERATION_WRITE_BACK_STATUSES:
        return None
    if item.generation_claim_token != claim_token:
        return None
    claimed_at = item.generation_claimed_at
    if claimed_at is None:
        return None
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    if claimed_at < observed_at - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS):
        return None
    return item


def _needs_generation_recovery():
    """Select missing fragments and stored defects the writer can repair.

    A newly approved Essence archives the version attached to an existing body,
    so that body must re-enter the scheduled writer once. FAQ schema fields and
    empty required reference lists are also writer-owned output, rather than
    permanent 07:45 publication blockers.
    """
    faq_needs_repair = and_(
        ContentItem.content_type == ContentType.FAQ,
        or_(
            ContentItem.faq_question.is_(None),
            func.right(func.trim(ContentItem.faq_question), 1) != "?",
            ContentItem.faq_answer_summary.is_(None),
            func.length(func.trim(ContentItem.faq_answer_summary)) == 0,
        ),
    )
    # JSONB ``null`` is a scalar (distinct from SQL NULL), and PostgreSQL raises
    # if jsonb_array_length receives it. CASE keeps the function on array values
    # while treating legacy/malformed shapes as empty and therefore repairable.
    reference_count = case(
        (
            func.jsonb_typeof(ContentItem.references_list) == "array",
            func.jsonb_array_length(ContentItem.references_list),
        ),
        else_=0,
    )
    references_need_repair = and_(
        ContentItem.content_type.in_(
            (
                ContentType.FAQ,
                ContentType.DISEASE,
                ContentType.TREATMENT,
                ContentType.COLUMN,
                ContentType.HEALTH,
                ContentType.LOCAL,
            )
        ),
        or_(
            ContentItem.references_list.is_(None),
            reference_count == 0,
        ),
    )
    body_uses_unapproved_essence = or_(
        ContentItem.content_philosophy_id.is_(None),
        ContentItem.content_philosophy.has(
            HospitalContentPhilosophy.status != PhilosophyStatus.APPROVED
        ),
    )
    ai_review = ContentItem.essence_check_summary["ai_review"]
    unresolved_ai_review = and_(
        or_(
            ai_review["status"].as_string() == "UNAVAILABLE",
            and_(
                ai_review["status"].as_string() == "REVISE",
                or_(
                    ai_review["blocking"].as_boolean().is_(True),
                    ai_review["schema_version"].as_string().is_(None),
                ),
            ),
        ),
    )
    return or_(
        ContentItem.body.is_(None),
        ContentItem.image_url.is_(None),
        ContentItem.image_policy_verified_at.is_(None),
        faq_needs_repair,
        references_need_repair,
        body_uses_unapproved_essence,
        unresolved_ai_review,
        and_(
            ContentItem.essence_status.is_not(None),
            ContentItem.essence_status != "ALIGNED",
        ),
    )


def _nightly_generation_stmt(window_start, window_end, claim_cutoff: datetime | None = None):
    claim_cutoff = claim_cutoff or _nightly_generation_claim_cutoff()
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date >= window_start,
            ContentItem.scheduled_date <= window_end,
            ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
            _needs_generation_recovery(),
            Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
            Hospital.site_live.is_(True),
            _nightly_generation_claim_filter(claim_cutoff),
        )
        .order_by(
            ContentItem.carried_over_from.is_not(None).desc(),
            ContentItem.scheduled_date,
            ContentItem.sequence_no,
        )
        .options(joinedload(ContentItem.hospital))
        .with_for_update(skip_locked=True, of=ContentItem)
        .limit(NIGHTLY_GENERATION_SELECT_LIMIT + 1)
    )


def interleave_by_hospital(items: list[ContentItem]) -> list[ContentItem]:
    """Round-robin the ordered candidates by hospital before the cap is applied.

    우선순위(이월 → 예정일 → 순번)는 병원 안에서 그대로 유지되고, 병원 간에는 한 편씩
    번갈아 나간다. 섞지 않으면 이월 슬롯이 많은 병원 하나가 상한 50개를 통째로 가져가
    나머지 병원은 아침까지 한 편도 받지 못한다.
    """

    groups: dict[Any, list[ContentItem]] = {}
    for item in items:
        groups.setdefault(item.hospital_id, []).append(item)
    ordered: list[ContentItem] = []
    queues = list(groups.values())
    while queues:
        queues = [queue for queue in queues if queue]
        for queue in queues:
            ordered.append(queue.pop(0))
    return ordered


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
                ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
                _needs_generation_recovery(),
                Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
                Hospital.site_live.is_(True),
                _nightly_generation_claim_filter(claim_cutoff),
            )
        ).scalar_one()
        truncated_count = max(int(overflow) - NIGHTLY_GENERATION_CAP, 1)
    claimed_items = interleave_by_hospital(items)[:NIGHTLY_GENERATION_CAP]
    for item in claimed_items:
        # SQLAlchemy에 영속화되지 않는 시도 메타데이터. 새 배치가 만료 claim을
        # 인수했는지와 finally가 해제할 정확한 lease 시각을 호출부에 전달한다.
        item._generation_reclaimed_stale = getattr(item, "generation_claimed_at", None) is not None
        claim_token = uuid.uuid4()
        item.generation_claimed_at = now
        item.generation_claim_token = claim_token
        item._generation_claim_token = claim_token
    if claimed_items:
        db.commit()
    return claimed_items, truncated_count


def release_unfinished_claims(
    db,
    item_ids: list,
    *,
    expected_claimed_at: datetime | None = None,
    expected_claim_token: uuid.UUID | None = None,
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
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(generation_claimed_at=None, generation_claim_token=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def release_generation_claim(db, item_id, claim_token: uuid.UUID) -> int:
    """Release only the caller's lease, regardless of the item's current defects."""

    result = db.execute(
        update(ContentItem)
        .where(
            ContentItem.id == item_id,
            ContentItem.generation_claim_token == claim_token,
        )
        .values(generation_claimed_at=None, generation_claim_token=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def load_stuck_claims(db, window_start, window_end) -> list[ContentItem]:
    """아직 만료되지 않은 claim 때문에 이번 배치가 건너뛴 슬롯.

    배치가 빈손으로 끝났을 때 "정말 할 일이 없는 것"과 "직전 실행이 죽어 claim이
    잠긴 것"을 구분하기 위한 값이다. 구분하지 않으면 한 달치 유실도 조용히 성공으로 보고된다.
    팬아웃 뒤에는 방금 배포된 per-item 태스크가 정상적으로 claim을 들고 있으므로,
    유예 시간 안의 claim은 진행 중인 일감으로 보고 제외한다.
    """
    return list(
        db.execute(_stuck_claims_stmt(window_start, window_end)).scalars().all()
    )


def _stuck_claims_stmt(
    window_start,
    window_end,
    claim_cutoff: datetime | None = None,
    in_flight_cutoff: datetime | None = None,
):
    now = datetime.now(timezone.utc)
    claim_cutoff = claim_cutoff or now - timedelta(hours=NIGHTLY_GENERATION_CLAIM_TTL_HOURS)
    in_flight_cutoff = in_flight_cutoff or now - NIGHTLY_GENERATION_IN_FLIGHT_GRACE
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.scheduled_date >= window_start,
            ContentItem.scheduled_date <= window_end,
            ContentItem.status.in_(GENERATION_WRITE_BACK_STATUSES),
            _needs_generation_recovery(),
            Hospital.status.in_(NIGHTLY_GENERATION_HOSPITAL_STATUSES),
            Hospital.site_live.is_(True),
            ContentItem.generation_claimed_at.isnot(None),
            ContentItem.generation_claimed_at >= claim_cutoff,
            ContentItem.generation_claimed_at <= in_flight_cutoff,
        )
        .options(joinedload(ContentItem.hospital))
    )
