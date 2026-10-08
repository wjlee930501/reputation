"""일회성 정리: 죽은 개발 채널(`SLACK_DEV`)에 쌓인 지난 알림과 채널 사고를 닫는다.

2026-09-19부터 개발 채널 웹훅이 302를 돌려줘 그 채널의 알림이 한 번도 전달되지 않았다.
운영 채널 하나로 운영하기로 하면서(2026-10 대표 결정) 다음 둘만 정리한다:

1. `SLACK_DEV`의 HOLD/FAILED 알림 중 그 알림이 말하던 사고가 이미 복구·확인된 것 —
   지난 사실이므로 다시 보내지 않고 종결(`STALE_NOT_RESENT`)로 표시한다.
2. 개발 웹훅이 비어 있으면(=단일 채널 모드) 채널 단위 전송 사고를 정상으로 닫는다.

그 밖에는 아무것도 일괄로 닫지 않는다. 기본은 dry-run이며 `--confirm`일 때만 쓴다::

    python -m app.utils.notification_channel_cleanup            # 무엇을 바꿀지 보기만
    python -m app.utils.notification_channel_cleanup --confirm  # 적용
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.operations import (
    Incident,
    IncidentState,
    NotificationOutbox,
    NotificationOutboxState,
)
from app.services.audit_log import write_audit_log
from app.services.incident_types import SLACK_DEVELOPER_CHANNEL
from app.services.notification_channel_health import (
    load_unhealthy_channels,
    recover_channel_incident,
)

STALE_CODE = "STALE_NOT_RESENT"
_STALE_MESSAGE = "이미 해결된 사고의 지난 알림이라 다시 보내지 않았습니다."
_CLOSED_STATES = (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
_ACTOR = "system:notification_channel_cleanup"


def _stale_rows_query():
    closed_incident = (
        select(Incident.id)
        .where(Incident.id == NotificationOutbox.incident_id, Incident.state.in_(_CLOSED_STATES))
        .exists()
    )
    return select(NotificationOutbox.id).where(
        NotificationOutbox.channel == SLACK_DEVELOPER_CHANNEL,
        NotificationOutbox.state.in_(
            (NotificationOutboxState.HOLD.value, NotificationOutboxState.FAILED.value)
        ),
        NotificationOutbox.safe_error_code.is_distinct_from(STALE_CODE),
        closed_incident,
    )


async def run_cleanup(
    sessions: async_sessionmaker[AsyncSession],
    *,
    confirm: bool,
    developer_webhook_url: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed = now or datetime.now(UTC)
    single_channel = not developer_webhook_url.strip()
    async with sessions() as db:
        stale_ids = list((await db.scalars(_stale_rows_query())).all())
        channel_incident = (await load_unhealthy_channels(db)).get(SLACK_DEVELOPER_CHANNEL)
        report: dict[str, Any] = {
            "confirm": confirm,
            "single_channel_mode": single_channel,
            "stale_developer_rows": len(stale_ids),
            "developer_channel_incident_open": channel_incident is not None,
            "developer_channel_incident_resolved": False,
        }
        if not confirm:
            return report
        if stale_ids:
            await db.execute(
                update(NotificationOutbox)
                .where(NotificationOutbox.id.in_(stale_ids))
                .values(
                    state=NotificationOutboxState.FAILED.value,
                    next_attempt_at=None,
                    safe_error_code=STALE_CODE,
                    safe_error_message=_STALE_MESSAGE,
                    version=NotificationOutbox.version + 1,
                    updated_at=observed,
                )
            )
            await write_audit_log(
                db,
                action="notification_stale_rows_closed",
                actor=_ACTOR,
                target_type="notification_outbox",
                detail={"channel": SLACK_DEVELOPER_CHANNEL, "count": len(stale_ids)},
            )
        if single_channel and channel_incident is not None:
            report["developer_channel_incident_resolved"] = await recover_channel_incident(
                db,
                channel_incident,
                now=observed,
                reason="developer webhook removed; single-channel mode",
            )
        await db.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="죽은 개발 채널의 지난 알림·채널 사고 정리")
    parser.add_argument("--confirm", action="store_true", help="실제로 적용한다(기본은 dry-run)")
    args = parser.parse_args(argv)
    from app.core.database import get_async_sessionmaker  # noqa: PLC0415

    report = asyncio.run(
        run_cleanup(
            get_async_sessionmaker(),
            confirm=args.confirm,
            developer_webhook_url=settings.SLACK_WEBHOOK_URL_DEV,
        )
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
