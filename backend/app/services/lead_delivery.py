"""리포트 메일 발송 오케스트레이션 (설계 §5-4).

`mailer`가 "어떻게 보내나"라면 여기는 "언제·몇 번·무엇을 근거로 보내나"다.
핵심은 **의도를 부수효과보다 먼저 커밋한다**는 순서 하나다.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import SalesLead
from app.models.lead_diagnosis import (
    DeliveryStatus,
    LeadDelivery,
    LeadDiagnosis,
    ReportStatus,
)
from app.services import lead_report_token, mailer

logger = logging.getLogger(__name__)

EVENT_REPORT = "REPORT"
CHANNEL_EMAIL = "EMAIL"

# n번째 시도까지의 대기 시간(최초 시도 기준). 전부 24시간 안에 들어가야 한다 —
# Resend가 idempotency key를 24시간만 보관하므로, 그 밖의 재시도는 중복 발송이 된다.
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=4))
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

# 이 창을 넘긴 SENDING은 자동으로 건드리지 않는다. "보냈는지 알 수 없는 상태"를
# 자동 재발송하는 것보다 사람이 한 번 확인하는 쪽이 옳다.
IDEMPOTENCY_WINDOW = timedelta(hours=24)


def next_attempt_due_at(delivery: LeadDelivery) -> datetime | None:
    """다음 재시도 시각. 시도가 소진됐으면 None."""
    attempt = delivery.attempt or 1
    if attempt >= MAX_ATTEMPTS:
        return None
    return delivery.created_at + RETRY_DELAYS[attempt - 1]


async def _existing_delivery(db: AsyncSession, diagnosis_id) -> LeadDelivery | None:
    return (
        await db.execute(
            select(LeadDelivery).where(
                LeadDelivery.diagnosis_id == diagnosis_id,
                LeadDelivery.event == EVENT_REPORT,
            )
        )
    ).scalar_one_or_none()


async def deliver_report(db: AsyncSession, diagnosis: LeadDiagnosis) -> dict:
    """리포트 링크를 메일로 보낸다. 진단당 1통이며 재시도해도 같은 행·같은 키를 쓴다."""
    if diagnosis.report_status != ReportStatus.READY.value:
        return {"skipped": "report_not_ready"}

    lead = (
        await db.execute(select(SalesLead).where(SalesLead.id == diagnosis.lead_id))
    ).scalar_one_or_none()
    recipient = (lead.email or "").strip() if lead else ""
    if not recipient or recipient == "[purged]":
        # 파기 후 재발송 시도는 정상 경로다 — 실패로 기록하지 않는다.
        return {"skipped": "no_recipient"}

    delivery = await _existing_delivery(db, diagnosis.id)
    if delivery is None:
        # ── 1단계: 의도를 먼저 커밋한다.
        delivery = LeadDelivery(
            id=uuid.uuid4(),
            lead_id=diagnosis.lead_id,
            diagnosis_id=diagnosis.id,
            channel=CHANNEL_EMAIL,
            event=EVENT_REPORT,
            status=DeliveryStatus.SENDING.value,
            attempt=1,
        )
        db.add(delivery)
        diagnosis.delivery_status = DeliveryStatus.SENDING.value
        await db.commit()
    elif delivery.status == DeliveryStatus.SENT.value:
        return {"skipped": "already_sent"}
    else:
        delivery.attempt = (delivery.attempt or 1) + 1
        delivery.status = DeliveryStatus.SENDING.value
        await db.commit()

    raw_token = lead_report_token.derive_report_token(diagnosis.id)
    report_url = lead_report_token.report_status_url(raw_token)

    try:
        # ── 2단계: 부수효과. Idempotency-Key는 **행 id**이며 attempt가 올라가도 그대로다.
        result = await mailer.send_email(
            to=recipient,
            subject=mailer.build_report_email_subject(diagnosis.subject_hospital_name),
            html=mailer.build_report_email_html(
                hospital_name=diagnosis.subject_hospital_name, report_url=report_url
            ),
            idempotency_key=str(delivery.id),
        )
    except Exception as exc:  # noqa: BLE001
        delivery.error = f"{type(exc).__name__}: {exc}"[:2000]
        if (delivery.attempt or 1) >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED.value
            diagnosis.delivery_status = DeliveryStatus.FAILED.value
        await db.commit()
        raise

    # ── 3단계: 여기서 실패해도 행은 SENDING으로 남고, 스윕이 같은 키로 재시도한다.
    delivery.status = DeliveryStatus.SENT.value
    delivery.provider_message_id = result.provider_message_id
    delivery.sent_at = datetime.now(timezone.utc)
    delivery.error = None
    diagnosis.delivery_status = DeliveryStatus.SENT.value
    await db.commit()

    return {"delivery_id": str(delivery.id), "attempt": delivery.attempt}


async def sweep_stuck_deliveries(db: AsyncSession, *, now: datetime | None = None) -> dict:
    """SENDING으로 남은 행을 처리한다.

    - 24시간 창 안 + 재시도 시각 도래 → 재시도 대상으로 돌려준다(같은 키를 쓴다)
    - 창을 넘겼거나 시도 소진 → FAILED + Slack. 자동 재발송하지 않는다
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(LeadDelivery).where(LeadDelivery.status == DeliveryStatus.SENDING.value)
        )
    ).scalars().all()

    retriable: list[str] = []
    abandoned: list[LeadDelivery] = []
    for delivery in rows:
        created = delivery.created_at
        if created is None:  # pragma: no cover - server_default가 채운다
            continue
        due = next_attempt_due_at(delivery)
        if due is None or (now - created) >= IDEMPOTENCY_WINDOW:
            delivery.status = DeliveryStatus.FAILED.value
            reason = (
                "재시도 소진"
                if due is None
                else "Resend 멱등성 보관 기간(24시간) 초과 — 중복 발송 위험으로 자동 재시도 중단"
            )
            delivery.error = f"{delivery.error or ''}\n{reason}".strip()[:2000]
            abandoned.append(delivery)
            continue
        if now >= due:
            retriable.append(str(delivery.diagnosis_id))

    if abandoned:
        for delivery in abandoned:
            diagnosis = (
                await db.execute(
                    select(LeadDiagnosis).where(LeadDiagnosis.id == delivery.diagnosis_id)
                )
            ).scalar_one_or_none()
            if diagnosis is not None:
                diagnosis.delivery_status = DeliveryStatus.FAILED.value
        await db.commit()

    return {"retriable": retriable, "abandoned": [str(d.id) for d in abandoned]}
