"""Admin API — 외부 파이프라인 감시 진입점.

Cloud Scheduler가 5분마다(하트비트) 그리고 매일 08:30 KST(발행 확인)에 이 경로를
직접 호출한다. Celery Beat/Worker가 죽어도 호출자가 살아 있어야 하므로 이 라우터는
어떤 태스크도 dispatch하지 않고, Slack도 outbox drain이 아니라 webhook으로 직접 보낸다.

인증은 공유 admin 키 또는 이 감시 전용 토큰(`X-Watchdog-Token`) 중 하나다. 전용
토큰을 두는 이유는 스케줄러에 admin 키를 넣지 않기 위해서다 — 이 토큰으로는 읽기
점검과 알림 전송만 가능하고 운영 데이터를 바꿀 수 없다.
"""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.security import api_key_header, verify_admin_key
from app.services import pipeline_watchdog
from app.services.pipeline_watchdog import (
    AlertDecision,
    WatchdogReport,
    utcnow,
    watchdog_session,
)

router = APIRouter(prefix="/admin/watchdog", tags=["Admin — Watchdog"])

watchdog_token_header = APIKeyHeader(name="X-Watchdog-Token", auto_error=False)


async def verify_watchdog_access(
    token: Annotated[str | None, Security(watchdog_token_header)] = None,
    key: Annotated[str | None, Security(api_key_header)] = None,
) -> str:
    """감시 전용 토큰이 맞으면 통과하고, 아니면 기존 admin 키 검증으로 넘긴다."""
    configured = settings.PIPELINE_WATCHDOG_TOKEN.strip()
    candidate = (token or "").strip()
    if configured and candidate and secrets.compare_digest(
        candidate.encode("utf-8"), configured.encode("utf-8")
    ):
        return "watchdog-token"
    # 토큰이 없거나 틀리면 admin 키가 유일한 다른 길이다(실패 시 401).
    return await verify_admin_key(key)


def _evaluate_now() -> WatchdogReport:
    with watchdog_session() as db:
        return pipeline_watchdog.evaluate(db, now=utcnow())


def _evaluate_and_decide() -> tuple[WatchdogReport, tuple[AlertDecision, ...]]:
    report = _evaluate_now()
    return report, pipeline_watchdog.decide_alerts(report, now=report.observed_at)


def _payload(report: WatchdogReport) -> dict[str, Any]:
    payload = report.as_dict()
    # 토큰이 비어 있어도 API는 뜬다(부팅을 막지 않는다). 대신 그 사실이 여기 보인다 —
    # 비어 있으면 Cloud Scheduler 호출이 401이고 외부 감시가 실제로는 꺼져 있다.
    payload["token_configured"] = bool(settings.PIPELINE_WATCHDOG_TOKEN.strip())
    return payload


@router.get("/pipeline", dependencies=[Depends(verify_watchdog_access)])
async def read_pipeline_watchdog() -> dict[str, Any]:
    """현재 파이프라인 생존 판정을 그대로 돌려준다(부작용 없음)."""
    report = await run_in_threadpool(_evaluate_now)
    return _payload(report)


@router.post("/pipeline/alert", dependencies=[Depends(verify_watchdog_access)])
async def run_pipeline_watchdog_alert() -> dict[str, Any]:
    """판정 후 필요한 경우에만 수신자별로 Slack 한 건씩 직접 보낸다."""
    report, decisions = await run_in_threadpool(_evaluate_and_decide)
    delivered = await pipeline_watchdog.deliver_all(decisions)
    return {
        "report": _payload(report),
        "alerts": [
            {
                "audience": decision.audience,
                "kind": decision.kind,
                "sent": decision.send,
                "delivered": was_delivered,
                "reason": decision.reason,
            }
            for decision, was_delivered in zip(decisions, delivered, strict=True)
        ],
    }
