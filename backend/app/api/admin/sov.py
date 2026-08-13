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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.hospital import Hospital
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services import sov_engine
from app.services.sov_engine import MENTION_RATE_INTENTS

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
    all_rows_stmt = (
        select(SovRecord)
        .join(QueryMatrix, SovRecord.query_id == QueryMatrix.id)
        .where(
            SovRecord.hospital_id == hospital_id,
            SovRecord.measured_at >= window_start,
            SovRecord.measured_at < window_end,
            QueryMatrix.query_intent.in_(tuple(MENTION_RATE_INTENTS)),
        )
    )
    all_rows = (await db.execute(all_rows_stmt)).scalars().all()

    result = []
    for start_dt, end_dt, label in weeks:
        rows = [r for r in all_rows if start_dt <= r.measured_at < end_dt]
        successful_rows = [r for r in rows if _is_successful_measurement(r)]
        total = len(successful_rows)
        mentioned = sum(1 for r in successful_rows if r.is_mentioned)
        failure_count = sum(1 for r in rows if _is_failed_measurement(r))
        ambiguous_count = sum(1 for r in rows if sov_engine.record_is_ambiguous(r))
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

    query_ids = [q.id for q in queries]
    all_records_stmt = select(SovRecord).where(
        SovRecord.hospital_id == hospital_id,
        SovRecord.query_id.in_(query_ids),
    )
    all_records_result = await db.execute(all_records_stmt)
    all_records = all_records_result.scalars().all()
    records_by_query: dict = defaultdict(list)
    for r in all_records:
        records_by_query[r.query_id].append(r)

    result = []
    for q in queries:
        records = records_by_query[q.id]
        successful_records = [r for r in records if _is_successful_measurement(r)]
        total = len(successful_records)
        mentioned = sum(1 for r in successful_records if r.is_mentioned)
        failure_count = sum(1 for r in records if _is_failed_measurement(r))
        ambiguous_count = sum(1 for r in records if sov_engine.record_is_ambiguous(r))
        # 전부 실패했거나 아직 측정 전인 쿼리는 None — 0%로 표기하면 '언급되지 않았다'는
        # 사실이 아닌 진단이 되어 보완 작업 우선순위까지 왜곡된다.
        mention_rate = round(mentioned / total * 100, 1) if total > 0 else None
        last_measured = max((r.measured_at for r in records), default=None)
        result.append({
            "query_id": str(q.id),
            "query_text": q.query_text,
            "mention_rate": mention_rate,
            "mention_count": mentioned,
            "total_count": total,
            "failure_count": failure_count,
            "ambiguous_count": ambiguous_count,
            "platform_breakdown": _build_platform_breakdown(records),
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
    status = getattr(record, "measurement_status", None)
    if not (status is None or str(status).upper() == "SUCCESS"):
        return False
    return getattr(record, "mention_verdict", None) != sov_engine.VERDICT_AMBIGUOUS


def _is_failed_measurement(record: Any) -> bool:
    """응답 실패만 센다. 판정 보류는 실패가 아니므로 실패 건수에 넣지 않는다 —
    섞으면 공급자 장애와 이름 모호성을 같은 칸에서 보게 된다."""
    status = getattr(record, "measurement_status", None)
    return not (status is None or str(status).upper() == "SUCCESS")


def _build_platform_breakdown(records: list[Any]) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}
    for record in records:
        platform = str(getattr(record, "ai_platform", None) or "UNKNOWN").upper()
        bucket = breakdown.setdefault(
            platform,
            {
                "platform_label": _display_label(PLATFORM_DISPLAY_LABELS, platform),
                "mention_count": 0,
                "total_count": 0,
                "failure_count": 0,
                "ambiguous_count": 0,
                "mention_rate": None,
            },
        )
        if _is_successful_measurement(record):
            bucket["total_count"] += 1
            if getattr(record, "is_mentioned", False):
                bucket["mention_count"] += 1
        elif sov_engine.record_is_ambiguous(record):
            # 판정 보류는 실패가 아니다 — 실패로 접으면 운영 화면이 공급자 장애로
            # 오독하고, PRD F3-7의 '별도 집계' 요구도 깨진다.
            bucket["ambiguous_count"] += 1
        else:
            bucket["failure_count"] += 1

    for bucket in breakdown.values():
        total = bucket["total_count"]
        # 해당 플랫폼 측정이 전부 실패한 경우 None — 실패를 0% 언급으로 뒤바꾸지 않는다.
        bucket["mention_rate"] = round(bucket["mention_count"] / total * 100, 1) if total else None
    return dict(sorted(breakdown.items()))


def _serialize_measurement_run(run: MeasurementRun) -> dict[str, Any]:
    query_count = run.query_count or 0
    success_count = run.success_count or 0
    failure_count = run.failure_count or 0
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
