"""
Admin API — AI 답변 언급률 분석
GET /admin/hospitals/{id}/sov/trend    — 주간 AI 답변 언급률 추이 (최근 12주)
GET /admin/hospitals/{id}/sov/queries  — 쿼리별 멘션율
GET /admin/hospitals/{id}/sov/measurement-runs — 최근 측정 실행 목록
"""
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

import arrow
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services import sov_engine
from app.services.sov_engine import MENTION_RATE_INTENTS, VERDICT_AMBIGUOUS

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — AI Answer Mention Rate"])

MEASUREMENT_METHOD_DISPLAY_LABELS = {
    "OPENAI_RESPONSE": "AI 답변 측정",
    "OPENAI_SEARCH": "AI 검색 측정",
    "CHATGPT_SEARCH": "ChatGPT 검색 측정",
    "OPENAI_CHAT_COMPLETIONS": "OpenAI 모델 응답 측정",
    "OPENAI_RESPONSES_WEB_SEARCH": "ChatGPT Search 유사 측정",
}
MEASUREMENT_RUN_STATUS_DISPLAY_LABELS = {
    "PENDING": "대기",
    "RUNNING": "실행 중",
    "COMPLETED": "완료",
    "FAILED": "실패",
    "PARTIAL": "일부 완료",
}
PLATFORM_DISPLAY_LABELS = {
    "CHATGPT": "ChatGPT",
    "GEMINI": "Gemini",
    "GOOGLE_AI_OVERVIEW": "Google AI Overview",
    "PERPLEXITY": "Perplexity",
    "UNKNOWN": "미확인",
}


def _display_label(labels: dict[str, str], value: str | None) -> str | None:
    if value is None:
        return None
    return labels.get(str(value).upper(), str(value))


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


@router.get("/{hospital_id}/sov/measurement-runs")
async def get_sov_measurement_runs(
    hospital_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
):
    """최근 AI 답변 언급률 측정 실행 목록."""
    await _get_hospital_or_404(db, hospital_id)

    safe_limit = max(1, min(limit, 100))
    stmt = (
        select(MeasurementRun)
        .options(selectinload(MeasurementRun.sov_records))
        .where(MeasurementRun.hospital_id == hospital_id)
        .order_by(MeasurementRun.created_at.desc())
        .limit(safe_limit)
    )
    runs = (await db.execute(stmt)).scalars().all()
    return [_serialize_measurement_run(run) for run in runs]


@router.get("/{hospital_id}/sov/trend")
async def get_sov_trend(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    최근 12주 주간 AI 답변 언급률 추이.
    Returns: [{week_start, sov_pct, mention_count, total_count}, ...]
    sov_pct는 nullable — 성공 측정이 0건인 주는 None(측정 안 됨)이며 0.0(실제 미언급)과 다르다.
    """
    await _get_hospital_or_404(db, hospital_id)

    now = arrow.now("Asia/Seoul")
    weeks = []
    for i in range(11, -1, -1):
        week_end = now.shift(weeks=-i)
        week_start = week_end.shift(weeks=-1)
        weeks.append((week_start.datetime, week_end.datetime, week_start.format("YYYY-MM-DD")))

    window_start = weeks[0][0]
    window_end = weeks[-1][1]
    # 언급률 분모는 LOCAL(지역 의도) 질문만 쓴다 — 월간 리포트의 calculate_sov와 같은
    # 기준이어야 Admin 추이와 원장 리포트의 숫자가 갈리지 않는다. INFO(지역 없는 의학
    # 설명) 질문은 AI가 특정 의원 이름을 댈 이유가 없어 병원이 무엇을 하든 0으로 고정이다.
    #
    # 12주 전체 행(raw_response 포함)을 로드해 파이썬으로 주차별로 나누는 대신, SQL에서
    # 바로 주차 버킷(0=window_start가 속한 주 ... 11=이번 주)으로 묶어 집계한다.
    # 현재 화면의 "주"는 캘린더 주(월요일 기준)가 아니라 조회 시점(now) 기준 7일 롤링
    # 구간이므로 `date_trunc('week', ...)`는 이 경계를 그대로 반영하지 못한다(요청 시각에
    # 따라 캘린더 주 경계와 어긋난다) — 대신 window_start로부터 경과한 정수 주(0~11)를
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
    for index, (_start_dt, _end_dt, label) in enumerate(weeks):
        bucket = buckets.get(index)
        total = bucket.total_count if bucket else 0
        mentioned = bucket.mention_count if bucket else 0
        failure_count = bucket.failure_count if bucket else 0
        ambiguous_count = bucket.ambiguous_count if bucket else 0
        # 성공 측정 0건이면 None — 측정 실패/미측정 주간을 '언급률 0%'로 보고하면
        # 원장 보고에 허위 수치가 들어간다 (sov_engine.calculate_sov의 반환 계약과 동일).
        sov_pct = round(mentioned / total * 100, 1) if total > 0 else None
        result.append({
            "week_start": label,
            "sov_pct": sov_pct,
            "mention_count": mentioned,
            "total_count": total,
            "failure_count": failure_count,
            "ambiguous_count": ambiguous_count,
        })

    return result


@router.get("/{hospital_id}/sov/queries")
async def get_sov_queries(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    쿼리별 멘션율.
    Returns: [{query_text, mention_rate, mention_count, total_count, last_measured_at}, ...]
    mention_rate(쿼리별·플랫폼별 모두)는 nullable — 성공 측정 0건이면 None이다.
    """
    await _get_hospital_or_404(db, hospital_id)

    # 활성 쿼리 목록
    q_stmt = select(QueryMatrix).where(
        QueryMatrix.hospital_id == hospital_id,
        QueryMatrix.is_active,
    )
    queries = (await db.execute(q_stmt)).scalars().all()
    if not queries:
        return []

    query_ids = [q.id for q in queries]
    # raw_response(AI 응답 원문) 포함 전체 행을 로드해 파이썬으로 그룹핑하는 대신,
    # 쿼리×플랫폼별로 SQL에서 바로 집계한다. 플랫폼별 집계가 필요하므로(응답 화면의
    # platform_breakdown) query_id 하나로만 묶지 않고 ai_platform도 함께 GROUP BY한다 —
    # 그룹 수는 쿼리 수 × 플랫폼 수(보통 2~3개)로 원본 행 수보다 훨씬 작다.
    agg_stmt = (
        select(
            SovRecord.query_id,
            SovRecord.ai_platform,
            func.count(SovRecord.id).filter(_confirmed_clause()).label("total_count"),
            func.count(SovRecord.id).filter(_mentioned_clause()).label("mention_count"),
            func.count(SovRecord.id).filter(_failed_clause()).label("failure_count"),
            func.count(SovRecord.id).filter(_ambiguous_clause()).label("ambiguous_count"),
            func.max(SovRecord.measured_at).label("last_measured_at"),
        )
        .where(
            SovRecord.hospital_id == hospital_id,
            SovRecord.query_id.in_(query_ids),
        )
        .group_by(SovRecord.query_id, SovRecord.ai_platform)
    )
    agg_rows = (await db.execute(agg_stmt)).all()

    rows_by_query: dict = defaultdict(list)
    for row in agg_rows:
        rows_by_query[row.query_id].append(row)

    result = []
    for q in queries:
        rows = rows_by_query.get(q.id, [])
        total = sum(row.total_count for row in rows)
        mentioned = sum(row.mention_count for row in rows)
        failure_count = sum(row.failure_count for row in rows)
        ambiguous_count = sum(row.ambiguous_count for row in rows)
        # 전부 실패했거나 아직 측정 전인 쿼리는 None — 0%로 표기하면 '언급되지 않았다'는
        # 사실이 아닌 진단이 되어 보완 작업 우선순위까지 왜곡된다.
        mention_rate = round(mentioned / total * 100, 1) if total > 0 else None
        last_measured = max(
            (row.last_measured_at for row in rows if row.last_measured_at is not None),
            default=None,
        )
        result.append({
            "query_id": str(q.id),
            "query_text": q.query_text,
            "mention_rate": mention_rate,
            "mention_count": mentioned,
            "total_count": total,
            "failure_count": failure_count,
            "ambiguous_count": ambiguous_count,
            "platform_breakdown": _build_platform_breakdown(rows),
            "last_measured_at": last_measured.isoformat() if last_measured else None,
        })

    # 미측정(None)은 언급률 순위를 매길 근거가 없으므로 내림차순에서도 항상 맨 뒤로 보낸다.
    # (None과 float을 그대로 비교하면 TypeError가 난다.)
    return sorted(
        result,
        key=lambda x: (x["mention_rate"] is not None, x["mention_rate"] or 0),
        reverse=True,
    )


# ── 헬퍼 ─────────────────────────────────────────────────────────
async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    h = await db.get(Hospital, hospital_id)
    if not h:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return h


def _is_successful_measurement(record: Any) -> bool:
    """분모에 들어갈 자격 — 응답을 받았고 **판정까지 확정**된 측정.

    AMBIGUOUS(동명 기관 가능성 등으로 확정 불가)는 측정 자체는 성공했지만 분모가
    아니다(PRD F3-7). 여기서 걸러야 하는 이유는 아래 집계들이 전부
    `sum(1 for r in successful if r.is_mentioned)` 형태이기 때문이다 — None은 falsy라
    보류가 조용히 '미언급'으로 분모에 남는다.
    """
    return sov_engine.record_is_confirmed(record)


def _is_failed_measurement(record: Any) -> bool:
    """응답 실패만 센다. 판정 보류는 실패가 아니므로 실패 건수에 넣지 않는다 —
    섞으면 공급자 장애와 이름 모호성을 같은 칸에서 보게 된다."""
    status = getattr(record, "measurement_status", None)
    return not (status is None or str(status).upper() == "SUCCESS")


def _build_platform_breakdown(agg_rows: list[Any]) -> dict[str, dict[str, Any]]:
    """쿼리 하나의 (query_id, ai_platform)별 SQL 집계 행들로부터 플랫폼별 분해를 만든다.

    각 행은 get_sov_queries의 agg_stmt(GROUP BY query_id, ai_platform)에서 이미
    성공/언급/실패/판정보류를 SQL FILTER로 나눠 계산해 왔으므로 여기서는 재집계 없이
    표시용 라벨과 mention_rate만 붙인다.
    """
    breakdown: dict[str, dict[str, Any]] = {}
    for row in agg_rows:
        platform = str(getattr(row, "ai_platform", None) or "UNKNOWN").upper()
        total = row.total_count
        # 해당 플랫폼 측정이 전부 실패한 경우 None — 실패를 0% 언급으로 뒤바꾸지 않는다.
        mention_rate = round(row.mention_count / total * 100, 1) if total else None
        breakdown[platform] = {
            "platform_label": _display_label(PLATFORM_DISPLAY_LABELS, platform),
            "mention_count": row.mention_count,
            "total_count": total,
            "failure_count": row.failure_count,
            "ambiguous_count": row.ambiguous_count,
            "mention_rate": mention_rate,
        }
    return dict(sorted(breakdown.items()))


def _serialize_measurement_run(run: MeasurementRun) -> dict[str, Any]:
    query_count = run.query_count or 0
    success_count = run.success_count or 0
    failure_count = run.failure_count or 0
    ambiguous_count = sum(
        1 for record in getattr(run, "sov_records", []) if sov_engine.record_is_ambiguous(record)
    )
    return {
        "id": str(run.id),
        "hospital_id": str(run.hospital_id),
        "run_label": run.run_label,
        "measurement_method": run.measurement_method,
        "status": run.status,
        "display": {
            "measurement_method_label": _display_label(MEASUREMENT_METHOD_DISPLAY_LABELS, run.measurement_method),
            "status_label": _display_label(MEASUREMENT_RUN_STATUS_DISPLAY_LABELS, run.status),
        },
        "query_count": query_count,
        "success_count": success_count,
        "ambiguous_count": ambiguous_count,
        "failure_count": failure_count,
        # 측정 건이 0이면 비율을 만들 수 없다. 0.0으로 채우면 "전부 실패한 실행"이
        # "실패율 0.0%"로 표시되어 sov_pct/mention_rate에서 없앤 허위 숫자가 그대로 남는다.
        # (_finish_measurement_run은 측정이 한 건도 안 생기면 query_count=0, status=FAILED로 마감한다.)
        "success_rate": round(success_count / query_count * 100, 1) if query_count else None,
        "failure_rate": round(failure_count / query_count * 100, 1) if query_count else None,
        "started_at": _iso_or_none(run.started_at),
        "completed_at": _iso_or_none(run.completed_at),
        "model_name": run.model_name,
        "search_mode": run.search_mode,
        "config": run.config,
        "error_summary": run.error_summary,
        "created_at": _iso_or_none(run.created_at),
        "updated_at": _iso_or_none(run.updated_at),
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
