"""계약 대비 실제 발행 수율을 한 기간·병원 단위로 읽는 집계.

대표가 물어보는 것은 하나다 — "버전업 뒤에 글이 더 나왔는가". 그 답은 지금까지
차단 코드와 인시던트 안에만 있었고, 코드를 읽을 수 있는 사람만 답할 수 있었다.
여기서는 같은 DB 사실을 **계약 예정 대비 발행**이라는 한 줄로 접는다.

읽기 전용이다. 상태를 바꾸지 않고, 공급자를 부르지 않으며, 기간·병원 수에 비례한
쿼리 2개(대상 병원 1개 + 그 병원들의 콘텐츠 행 1개)만 쓴다.

세 가지를 섞지 않는다.

- **계약 예정(due)**: 그 기간의 `scheduled_date` 슬롯 중 취소되지 않은 것. 계약
  이행의 분모다.
- **발행(published)**: `first_published_at`이 그 기간에 있는 글. 현재 공개 판이
  반려로 내려갔어도 처음 발행한 사실은 남는다(`published_at`이 아니라 이 값을 쓰는
  이유다). 지연 발행은 예정 주가 아니라 실제 발행 주에 잡힌다 — 분자와 분모의 기간
  정의가 서로 다를 수 있고, 그래서 발행이 예정보다 많은 주도 정상이다.
- **차단·재시도·조치 필요**: 예정됐지만 아직 발행되지 않은 슬롯의 현재 상태.
  `RETRYING`은 자동 복구가 소유한 상태이므로 `operator_required`에 넣지 않는다.

Slack에는 enum·오류 코드를 내보내지 않는다. 차단 사유는 `generation_safe_cause`가
만든 운영자 문구를 키로 쓴다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import HospitalServiceInterval
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_incident_control import generation_safe_cause
from app.workers.generation_retry_policy import (
    SAMPLE_EXHAUSTED_DAY_LIMIT,
    GenerationRetryClass,
)

KST = ZoneInfo("Asia/Seoul")

# 자동 복구가 아직 소유한 재시도 클래스. 사람의 할 일이 아니다.
_RETRYING_CLASSES = frozenset(
    {
        GenerationRetryClass.SAMPLE_RECOVERABLE.value,
        GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
    }
)
# 재사용 이미지 흔적을 남기는 시도 조각 키. 컬럼이 비어 있어도(사후 교체 전 저장 실패
# 등) 요약이 남아 있으면 재사용 발행으로 읽는다.
_IMAGE_REUSE_SUMMARY_KEY = "image_reuse"


@dataclass(frozen=True, slots=True)
class HospitalYieldFact:
    """한 병원의 한 기간 수율 사실. 비율이 아니라 셈한 수만 담는다."""

    hospital_id: uuid.UUID
    hospital_name: str
    period_start: date
    period_end: date  # 기간 다음 날(KST) — 경계는 [start, end)
    due: int = 0
    published: int = 0
    published_with_reused_image: int = 0
    retrying: int = 0
    operator_required: int = 0
    # 운영자 문구 → 건수. 예정됐지만 아직 발행되지 않은 슬롯만 센다.
    blocked_by_cause: dict[str, int] = field(default_factory=dict)

    @property
    def blocked(self) -> int:
        return sum(self.blocked_by_cause.values())


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """KST 달력 경계를 UTC 순간으로 바꾼다. 끝은 열린 구간이다."""

    if period_end <= period_start:
        raise ValueError("CONTENT_YIELD_PERIOD_INVALID")
    starts_at = datetime.combine(period_start, time.min, tzinfo=KST).astimezone(UTC)
    ends_at = datetime.combine(period_end, time.min, tzinfo=KST).astimezone(UTC)
    return starts_at, ends_at


def eligible_hospitals_stmt(period_start: date, period_end: date) -> Select:
    """그 기간에 계약이 있었던 병원.

    현재 `ACTIVE` 목록만 쓰면 기간 중 중지된 병원이 조용히 사라져 수율이 실제보다
    좋아 보인다. 월간 리포트의 대상 판정(`monthly_period.eligible_hospital_ids`)과
    같은 서비스 구간 겹침 규칙을 쓰고, 현재 운영 중인 병원을 합집합으로 더한다.
    """

    starts_at, ends_at = _period_bounds(period_start, period_end)
    served = (
        select(HospitalServiceInterval.hospital_id)
        .where(
            HospitalServiceInterval.started_at < ends_at,
            or_(
                HospitalServiceInterval.ended_at.is_(None),
                HospitalServiceInterval.ended_at > starts_at,
            ),
        )
        .distinct()
    )
    return (
        select(Hospital.id, Hospital.name)
        .where(
            or_(
                Hospital.status == HospitalStatus.ACTIVE,
                Hospital.id.in_(served),
            )
        )
        .order_by(Hospital.name.asc(), Hospital.id.asc())
    )


def yield_rows_stmt(
    hospital_ids: Sequence[uuid.UUID], period_start: date, period_end: date
) -> Select:
    """예정 슬롯과 실제 발행을 한 번에 읽는다.

    지연 발행은 예정일이 기간 밖이므로 `scheduled_date` 조건만으로는 보이지 않는다.
    두 조건의 합집합을 한 쿼리로 읽고 파이썬에서 각각 센다.
    """

    starts_at, ends_at = _period_bounds(period_start, period_end)
    return (
        select(
            ContentItem.hospital_id,
            ContentItem.scheduled_date,
            ContentItem.status,
            ContentItem.first_published_at,
            ContentItem.image_reused_from_content_id,
            ContentItem.essence_check_summary,
        )
        .where(
            ContentItem.hospital_id.in_(list(hospital_ids)),
            or_(
                and_(
                    ContentItem.scheduled_date >= period_start,
                    ContentItem.scheduled_date < period_end,
                    ContentItem.status != ContentStatus.CANCELLED,
                ),
                and_(
                    ContentItem.first_published_at.is_not(None),
                    ContentItem.first_published_at >= starts_at,
                    ContentItem.first_published_at < ends_at,
                ),
            ),
        )
        .order_by(ContentItem.hospital_id, ContentItem.scheduled_date)
    )


def _attempt(summary: Any) -> Mapping[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    attempt = summary.get(GENERATION_ATTEMPT_KEY)
    return attempt if isinstance(attempt, Mapping) else {}


def _exhausted_days(attempt: Mapping[str, Any]) -> int:
    try:
        return int(attempt.get("exhausted_days") or 0)
    except (TypeError, ValueError):
        return 0


def _reused_image(row: Any) -> bool:
    if row.image_reused_from_content_id is not None:
        return True
    summary = row.essence_check_summary
    return isinstance(summary, Mapping) and bool(summary.get(_IMAGE_REUSE_SUMMARY_KEY))


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, ContentStatus) else str(status or "")


def fold_yield_rows(
    hospitals: Sequence[tuple[uuid.UUID, str]],
    rows: Iterable[Any],
    *,
    period_start: date,
    period_end: date,
    now: datetime | None = None,
) -> list[HospitalYieldFact]:
    """행을 병원별 사실로 접는다. 순수 함수 — 세션도 시계도 필요 없다."""

    starts_at, ends_at = _period_bounds(period_start, period_end)
    buckets: dict[uuid.UUID, dict[str, Any]] = {
        hospital_id: {
            "name": name,
            "due": 0,
            "published": 0,
            "reused": 0,
            "retrying": 0,
            "operator_required": 0,
            "causes": {},
        }
        for hospital_id, name in hospitals
    }

    for row in rows:
        bucket = buckets.get(row.hospital_id)
        if bucket is None:
            continue
        first_published_at = row.first_published_at
        published_in_period = (
            first_published_at is not None and starts_at <= first_published_at < ends_at
        )
        if published_in_period:
            bucket["published"] += 1
            if _reused_image(row):
                bucket["reused"] += 1

        due_in_period = (
            period_start <= row.scheduled_date < period_end
            and _status_value(row.status) != ContentStatus.CANCELLED.value
        )
        if not due_in_period:
            continue
        bucket["due"] += 1
        if _status_value(row.status) == ContentStatus.PUBLISHED.value:
            continue

        attempt = _attempt(row.essence_check_summary)
        reason = str(attempt.get("reason") or "")
        if not reason:
            continue
        cause = generation_safe_cause(reason)
        bucket["causes"][cause] = bucket["causes"].get(cause, 0) + 1
        retry_class = str(attempt.get("retry_class") or "")
        exhausted = _exhausted_days(attempt) >= SAMPLE_EXHAUSTED_DAY_LIMIT
        if retry_class == GenerationRetryClass.OPERATOR_REQUIRED.value or exhausted:
            bucket["operator_required"] += 1
        elif retry_class in _RETRYING_CLASSES:
            bucket["retrying"] += 1

    return [
        HospitalYieldFact(
            hospital_id=hospital_id,
            hospital_name=bucket["name"],
            period_start=period_start,
            period_end=period_end,
            due=bucket["due"],
            published=bucket["published"],
            published_with_reused_image=bucket["reused"],
            retrying=bucket["retrying"],
            operator_required=bucket["operator_required"],
            blocked_by_cause=dict(sorted(bucket["causes"].items())),
        )
        for hospital_id, bucket in buckets.items()
    ]


def compute_content_yield(
    db: Session, *, period_start: date, period_end: date
) -> list[HospitalYieldFact]:
    """동기 세션(워커)용 집계. 경계는 KST `[period_start, period_end)`."""

    hospitals = [
        (row[0], row[1])
        for row in db.execute(eligible_hospitals_stmt(period_start, period_end)).all()
    ]
    if not hospitals:
        return []
    rows = db.execute(
        yield_rows_stmt([hospital_id for hospital_id, _ in hospitals], period_start, period_end)
    ).all()
    return fold_yield_rows(
        hospitals, rows, period_start=period_start, period_end=period_end
    )


async def compute_content_yield_async(
    db: AsyncSession, *, period_start: date, period_end: date
) -> list[HospitalYieldFact]:
    """Admin API(비동기 세션)용 같은 집계. 두 경로가 같은 순수 함수로 접힌다."""

    hospitals = [
        (row[0], row[1])
        for row in (
            await db.execute(eligible_hospitals_stmt(period_start, period_end))
        ).all()
    ]
    if not hospitals:
        return []
    rows = (
        await db.execute(
            yield_rows_stmt(
                [hospital_id for hospital_id, _ in hospitals], period_start, period_end
            )
        )
    ).all()
    return fold_yield_rows(
        hospitals, rows, period_start=period_start, period_end=period_end
    )


def kst_week_start(observed: date) -> date:
    """그 날짜가 속한 KST 주(월요일 시작)의 첫날."""

    return observed - timedelta(days=observed.weekday())
