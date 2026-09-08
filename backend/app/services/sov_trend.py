"""주간 AI 답변 언급률 추이 — admin 추이 화면과 병원 현황이 같은 집계를 쓴다.

추이 SQL이 화면마다 따로 있으면 같은 병원의 언급률이 화면마다 갈린다. 확정/미언급/실패/
미확정 판정 조건(`sov_engine.record_is_confirmed`의 SQL 등가물)도 여기 한 곳에만 둔다.
"""

from __future__ import annotations

import uuid

import arrow
from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sov import QueryMatrix, SovRecord
from app.services.sov_engine import MENTION_RATE_INTENTS, VERDICT_AMBIGUOUS

WEEKLY_TREND_WEEKS = 12


# ── SQL 집계 조건 ────────────────────────────────────────────────
# sov_engine.record_is_confirmed / record_is_ambiguous의 SQL 등가물. 두 함수가 쓰는
# 조건과 어긋나면 admin 화면과 월간 리포트·우선순위 엔진의 언급률이 다시 갈린다
# (PRD F3-7, sov_engine.record_is_confirmed 참고).
#
# mention_verdict에 대한 부등호 비교는 `is_distinct_from`을 쓴다 — 3값 도입 이전
# 레거시 행은 verdict가 NULL이고, SQL에서 `verdict != 'AMBIGUOUS'`는 NULL과 비교하면
# NULL(=거짓 취급)이 되어 레거시 행이 통째로 분모에서 빠진다. `IS DISTINCT FROM`만
# NULL을 "AMBIGUOUS가 아님"으로 취급해 Python의 `verdict == VERDICT_AMBIGUOUS`와
# 동일하게 동작한다.
def _status_success_clause():
    return func.upper(func.coalesce(SovRecord.measurement_status, "SUCCESS")) == "SUCCESS"


def _confirmed_clause():
    status_success = _status_success_clause()
    return and_(
        status_success,
        SovRecord.mention_verdict.is_distinct_from(VERDICT_AMBIGUOUS),
        SovRecord.is_mentioned.is_not(None),
    )


def _mentioned_clause():
    return and_(_confirmed_clause(), SovRecord.is_mentioned.is_(True))


def _failed_clause():
    return ~_status_success_clause()


def _ambiguous_clause():
    status_success = _status_success_clause()
    return and_(
        status_success,
        or_(SovRecord.mention_verdict == VERDICT_AMBIGUOUS, SovRecord.is_mentioned.is_(None)),
    )


async def weekly_mention_trend(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    weeks: int = WEEKLY_TREND_WEEKS,
) -> list[dict]:
    """최근 `weeks`주 주간 AI 답변 언급률 추이를 한 쿼리로.

    `sov_pct`는 nullable — 성공 측정이 0건인 주는 None(측정 안 됨)이며 0.0(실제 미언급)과
    다르다. 측정 실패/미측정 주간을 '언급률 0%'로 보고하면 원장 보고에 허위 수치가 들어간다
    (`sov_engine.calculate_sov`의 반환 계약과 동일).
    """
    now = arrow.now("Asia/Seoul")
    buckets_meta = []
    for index in range(weeks - 1, -1, -1):
        week_end = now.shift(weeks=-index)
        week_start = week_end.shift(weeks=-1)
        buckets_meta.append((week_start.datetime, week_end.datetime, week_start.format("YYYY-MM-DD")))

    window_start = buckets_meta[0][0]
    window_end = buckets_meta[-1][1]
    # 언급률 분모는 LOCAL(지역 의도) 질문만 쓴다 — 월간 리포트의 calculate_sov와 같은
    # 기준이어야 Admin 추이와 원장 리포트의 숫자가 갈리지 않는다. INFO(지역 없는 의학
    # 설명) 질문은 AI가 특정 의원 이름을 댈 이유가 없어 병원이 무엇을 하든 0으로 고정이다.
    #
    # 전체 행(raw_response 포함)을 로드해 파이썬으로 주차별로 나누는 대신, SQL에서
    # 바로 주차 버킷(0=window_start가 속한 주 ... weeks-1=이번 주)으로 묶어 집계한다.
    # 현재 화면의 "주"는 캘린더 주(월요일 기준)가 아니라 조회 시점(now) 기준 7일 롤링
    # 구간이므로 `date_trunc('week', ...)`는 이 경계를 그대로 반영하지 못한다(요청 시각에
    # 따라 캘린더 주 경계와 어긋난다) — 대신 window_start로부터 경과한 정수 주를
    # 계산해 그 롤링 경계를 SQL에서 그대로 재현한다.
    week_seconds = 7 * 24 * 3600
    week_offset = cast(
        func.floor(func.extract("epoch", SovRecord.measured_at - window_start) / week_seconds),
        Integer,
    ).label("week_offset")

    trend_stmt = (
        select(
            week_offset,
            func.count(SovRecord.id).filter(_confirmed_clause()).label("total_count"),
            func.count(SovRecord.id).filter(_mentioned_clause()).label("mention_count"),
            func.count(SovRecord.id).filter(_failed_clause()).label("failure_count"),
            func.count(SovRecord.id).filter(_ambiguous_clause()).label("ambiguous_count"),
        )
        .select_from(SovRecord)
        .join(QueryMatrix, SovRecord.query_id == QueryMatrix.id)
        .where(
            SovRecord.hospital_id == hospital_id,
            SovRecord.measured_at >= window_start,
            SovRecord.measured_at < window_end,
            QueryMatrix.query_intent.in_(tuple(MENTION_RATE_INTENTS)),
        )
        .group_by(week_offset)
    )
    buckets = {int(row.week_offset): row for row in (await db.execute(trend_stmt)).all()}

    result = []
    for index, (_start_dt, _end_dt, label) in enumerate(buckets_meta):
        bucket = buckets.get(index)
        total = bucket.total_count if bucket else 0
        mentioned = bucket.mention_count if bucket else 0
        result.append(
            {
                "week_start": label,
                "sov_pct": round(mentioned / total * 100, 1) if total > 0 else None,
                "mention_count": mentioned,
                "total_count": total,
                "failure_count": bucket.failure_count if bucket else 0,
                "ambiguous_count": bucket.ambiguous_count if bucket else 0,
            }
        )
    return result


def latest_mention_rate(trend: list[dict]) -> float | None:
    """가장 최근에 실제로 측정된 주의 언급률. 측정이 없으면 None(0%가 아니다)."""
    for point in reversed(trend):
        if point["sov_pct"] is not None:
            return point["sov_pct"]
    return None
