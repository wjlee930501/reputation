"""V0 측정 체크포인트 — 재시도가 이미 끝난 측정을 다시 사지 않게 한다.

배경(2026-09-01 아키텍처 리뷰 §2-3, §7 6행):
`trigger_v0_report`는 15질의 × 2플랫폼 × 5반복 = **150건의 유료 LLM 호출**을 낸 뒤
PDF 렌더 → GCS 업로드 → DB 커밋 → Slack 순으로 진행한다. 측정 **이후** 단계에서
예외가 나면 태스크는 `self.retry`로 맨 위부터 다시 시작했다. QueryMatrix는 멱등이라
재사용됐지만 측정은 아니었다 — 재시도 2회까지 포함하면 같은 진단 한 건에 450호출,
비용 가드 예약도 3번 잡혔다. 실패 원인(WeasyPrint·GCS·DB)은 측정과 아무 상관이 없는데
가장 비싼 단계를 통째로 되풀이한 것이다.

그래서 완료된 측정을 체크포인트로 남기고, 같은 V0 요청의 재시도는 그 결과를 읽어
PDF 단계부터 재개한다. 재사용 판정은 두 단계다:

1. **lineage 정합** — OperationRun id가 태스크 헤더로 이미 전달되고 있고(`operation_run_id`),
   Celery의 `Task.retry`는 `request.headers`를 그대로 재발행한다. 그 id를 측정 실행의
   `config`에 새겨 두면 "같은 V0 요청이 만든 측정"을 스키마 변경 없이 정확히 지목할 수 있다.
2. **fallback** — 헤더가 없는 legacy 디스패치에서는 "6시간 이내에 끝난 V0 측정 중
   아직 리포트로 소비되지 않은 최신 실행"을 쓴다.

두 경로 모두 다음을 반드시 만족해야 재사용한다:
  - status가 COMPLETED 또는 PARTIAL (= 성공 측정이 1건 이상 있다)
  - SovRecord가 실제로 남아 있다 (레코드 없는 run으로 언급률을 만들면 허위 숫자가 된다)
  - 그 측정이 시작된 뒤에 만들어진 V0 리포트가 없다 (= 아직 소비되지 않았다)
마지막 조건이 "AE가 상태를 되돌려 새로 요청한 V0"가 옛 측정을 물려받는 것을 막는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.models.report import V0_REPORT_TYPE, MonthlyReport
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services.sov_engine import QUERY_INTENT_LOCAL

#: MeasurementRun.config["source"] 값 — V0 경로가 만든 측정만 재사용 대상이다.
V0_MEASUREMENT_SOURCE = "trigger_v0_report"

#: 체크포인트 유효 시간. Celery 재시도는 120초 간격 2회이고 태스크 자체는 최대 35분이라
#: 6시간이면 같은 요청의 모든 재시도를 넉넉히 덮는다. 그보다 오래된 측정은 "지금의 진단"
#: 이라고 부를 수 없으므로 다시 측정한다.
V0_CHECKPOINT_MAX_AGE_SECONDS = 6 * 3600

#: 재사용 가능한 상태. FAILED는 성공 측정이 0건이라 재사용해도 리포트를 만들 수 없다.
REUSABLE_RUN_STATUSES = ("COMPLETED", "PARTIAL")

#: 후보 스캔 상한 — 6시간 창 안의 V0 측정이 이보다 많을 수는 없다.
_CANDIDATE_SCAN_LIMIT = 20

#: 리포트/알림이 기대하는 플랫폼 표기 순서.
_PLATFORM_ORDER = {"chatgpt": 0, "gemini": 1}


@dataclass(frozen=True)
class V0Checkpoint:
    """이미 끝난 V0 측정에서 되살린, PDF 단계가 필요로 하는 전부."""

    run_id: uuid.UUID
    records: list[dict[str, Any]]
    success_count: int
    failure_count: int
    failure_summary: dict[str, Any] | None
    platforms: list[str]
    repeat_count: int


def v0_measurement_run_config(
    *, repeat_count: int, operation_run_id: uuid.UUID | None
) -> dict[str, Any]:
    """`_start_measurement_run`에 넘길 config — 재사용 판정의 근거를 함께 새긴다."""
    config: dict[str, Any] = {
        "source": V0_MEASUREMENT_SOURCE,
        "repeat_count": repeat_count,
    }
    if operation_run_id is not None:
        config["operation_run_id"] = str(operation_run_id)
    return config


def _config_of(run: MeasurementRun) -> Mapping[str, Any]:
    config = run.config
    return config if isinstance(config, Mapping) else {}


def _is_v0_run(run: MeasurementRun) -> bool:
    return _config_of(run).get("source") == V0_MEASUREMENT_SOURCE


def _run_operation_run_id(run: MeasurementRun) -> str | None:
    value = _config_of(run).get("operation_run_id")
    return value if isinstance(value, str) and value else None


def _has_sov_records(db, run_id: uuid.UUID) -> bool:
    count = db.execute(
        select(func.count()).select_from(SovRecord).where(SovRecord.measurement_run_id == run_id)
    ).scalar_one()
    return bool(count)


def _already_consumed_by_report(db, run: MeasurementRun) -> bool:
    """이 측정이 시작된 뒤 만들어진 V0 리포트가 있으면 이미 소비된 측정이다."""
    started = run.started_at or run.created_at
    stmt = (
        select(func.count())
        .select_from(MonthlyReport)
        .where(
            MonthlyReport.hospital_id == run.hospital_id,
            MonthlyReport.report_type == V0_REPORT_TYPE,
        )
    )
    if started is not None:
        stmt = stmt.where(MonthlyReport.created_at >= started)
    return bool(db.execute(stmt).scalar_one())


def find_reusable_v0_measurement_run(
    db,
    hospital_id: uuid.UUID,
    *,
    operation_run_id: uuid.UUID | None,
    now: datetime | None = None,
) -> MeasurementRun | None:
    """같은 V0 요청이 이미 완료한 측정 실행. 없으면 None(= 새로 측정한다)."""
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=V0_CHECKPOINT_MAX_AGE_SECONDS)
    candidates = (
        db.execute(
            select(MeasurementRun)
            .where(
                MeasurementRun.hospital_id == hospital_id,
                MeasurementRun.status.in_(REUSABLE_RUN_STATUSES),
                MeasurementRun.completed_at.isnot(None),
                MeasurementRun.completed_at >= cutoff,
            )
            .order_by(MeasurementRun.completed_at.desc())
            .limit(_CANDIDATE_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )
    wanted = str(operation_run_id) if operation_run_id is not None else None
    for run in candidates:
        if not _is_v0_run(run):
            continue
        # lineage가 있는 요청은 **정확히 자기 실행**만 재사용한다. 다른 요청의 측정을
        # 물려받으면 AE가 새로 누른 진단이 옛 숫자를 되돌려준다.
        if wanted is not None and _run_operation_run_id(run) != wanted:
            continue
        if not _has_sov_records(db, run.id):
            continue
        if _already_consumed_by_report(db, run):
            continue
        return run
    return None


def _record_to_result(record: SovRecord, query_intent: str | None) -> dict[str, Any]:
    """SovRecord → `calculate_sov`가 읽는 dict. 저장된 사실만 되돌린다."""
    return {
        "platform": record.ai_platform,
        "is_mentioned": record.is_mentioned,
        "verdict": record.mention_verdict,
        "mention_rank": record.mention_rank,
        "sentiment": record.mention_sentiment,
        "raw_response": record.raw_response or "",
        "measurement_status": record.measurement_status,
        "failure_reason": record.failure_reason,
        "query_intent": query_intent or QUERY_INTENT_LOCAL,
    }


def platforms_from_records(records: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for record in records:
        platform = record.get("platform")
        if isinstance(platform, str) and platform and platform not in seen:
            seen.append(platform)
    return sorted(seen, key=lambda name: (_PLATFORM_ORDER.get(name, 99), name))


def load_v0_checkpoint(db, run: MeasurementRun, *, default_repeat_count: int) -> V0Checkpoint:
    """완료된 측정 실행에서 PDF 단계가 쓰는 값을 되살린다."""
    rows = db.execute(
        select(SovRecord, QueryMatrix.query_intent)
        .outerjoin(QueryMatrix, SovRecord.query_id == QueryMatrix.id)
        .where(SovRecord.measurement_run_id == run.id)
    ).all()
    records = [_record_to_result(record, intent) for record, intent in rows]
    stored_repeat = _config_of(run).get("repeat_count")
    repeat_count = (
        stored_repeat
        if isinstance(stored_repeat, int)
        and not isinstance(stored_repeat, bool)
        and stored_repeat > 0
        else default_repeat_count
    )
    error_summary = run.error_summary if isinstance(run.error_summary, Mapping) else None
    return V0Checkpoint(
        run_id=run.id,
        records=records,
        success_count=int(run.success_count or 0),
        failure_count=int(run.failure_count or 0),
        failure_summary=dict(error_summary) if error_summary else None,
        platforms=platforms_from_records(records),
        repeat_count=repeat_count,
    )
