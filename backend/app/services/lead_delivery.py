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
from app.services import lead_privacy, lead_report_token, mailer

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
    if lead_privacy.is_purged_value(recipient):
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
        # **발송 직전에 창을 다시 본다.** 스윕이 23시간 59분에 retriable로 판정해도
        # 큐 지연으로 24시간을 넘겨 실행될 수 있고, 그때는 Resend가 키를 잊어
        # 재시도가 곧 두 번째 메일이 된다.
        age = datetime.now(timezone.utc) - delivery.created_at
        if age >= IDEMPOTENCY_WINDOW or (delivery.attempt or 1) >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED.value
            delivery.error = (
                f"{delivery.error or ''}\n멱등성 창 밖 또는 재시도 소진 — 자동 재발송 중단"
            ).strip()[:2000]
            diagnosis.delivery_status = DeliveryStatus.FAILED.value
            await db.commit()
            return {"skipped": "idempotency_window_expired"}
        # `or 0`이어야 한다. `or 1`이면 수동 재발송이 리셋한 attempt=0이 1로 읽혀
        # 첫 발송이 2회차가 되고, 재시도 사다리가 한 칸(총 4→3회) 줄어든다.
        # 정상 행은 attempt >= 1이라 결과가 같다.
        delivery.attempt = (delivery.attempt or 0) + 1
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


async def rearm_report_delivery(
    db: AsyncSession,
    diagnosis: LeadDiagnosis,
    *,
    actor: str,
    reason: str,
    acknowledge_duplicate_risk: bool = False,
    now: datetime | None = None,
) -> dict:
    """FAILED로 종결된 리포트 메일을 사람이 다시 보내도록 되살린다 (수동 재발송).

    ## 왜 필요한가

    자동 재시도(4회)가 소진되면 `delivery_status=FAILED`가 종결 상태다. 그런데 리포트는
    이미 만들어져 있고, 신청자의 전화번호·이메일은 **영구 잠금**이라 재신청도 불가능하다.
    복구 경로가 없으면 그 리드는 영구 소실된다 — 실제로 2026-07-30 첫 E2E에서 Resend 키가
    PLACEHOLDER였던 탓에 3회가 연속 실패했고, 그때 이 함수가 없었다.

    ## 중복 발송을 어떻게 막는가

    되살릴 때 **행 id(= Resend Idempotency-Key)를 바꾸지 않는다.** 그래서:

    - `age < IDEMPOTENCY_WINDOW`: Resend가 키를 아직 기억한다. 원래 발송이 실제로
      나갔다면 재시도는 그 응답을 돌려받고 메일은 다시 나가지 않는다 —
      **중복이 구조적으로 불가능**하므로 확인 절차 없이 되살린다.
    - `age >= IDEMPOTENCY_WINDOW`: Resend가 키를 잊었다. 원래 발송이 나갔는지 알 수 없고
      (프로세스가 발송 직후 죽었을 수 있다) 나갔다면 두 번째 메일이 된다. 그래서
      `acknowledge_duplicate_risk`를 요구한다 — 이 판단은 사람이 해야 한다.

    되살린 뒤 직접 발송하지 않고 `delivery_status=PENDING`으로만 돌려놓는다. 1분 폴러가
    진실의 원천이므로(설계 §5-3) 발송 경로를 하나로 유지한다.
    """
    now = now or datetime.now(timezone.utc)
    reason = reason.strip()[:200]

    if diagnosis.report_status != ReportStatus.READY.value:
        return {
            "ok": False,
            "code": "report_not_ready",
            "message": (
                f"리포트가 READY가 아닙니다(현재 {diagnosis.report_status}). "
                "보낼 리포트가 없으므로 재발송할 수 없습니다."
            ),
        }

    lead = (
        await db.execute(select(SalesLead).where(SalesLead.id == diagnosis.lead_id))
    ).scalar_one_or_none()
    if lead is None or lead_privacy.is_purged_value(lead.email):
        return {
            "ok": False,
            "code": "no_recipient",
            "message": "수신자 이메일이 없거나 파기된 리드입니다. 재발송할 수 없습니다.",
        }

    delivery = await _existing_delivery(db, diagnosis.id)

    if delivery is not None and delivery.status == DeliveryStatus.SENT.value:
        return {
            "ok": False,
            "code": "already_sent",
            "message": (
                f"이미 발송 완료된 건입니다(발송 시각 {delivery.sent_at}). "
                "다시 보내려면 중복 발송이 되므로 이 경로로는 처리하지 않습니다."
            ),
        }

    previous_status = delivery.status if delivery is not None else None
    age = (now - delivery.created_at) if delivery is not None else None
    key_forgotten = age is not None and age >= IDEMPOTENCY_WINDOW

    if (
        delivery is not None
        and delivery.status == DeliveryStatus.SENDING.value
        and not key_forgotten
    ):
        # 아직 자동 재시도 창 안이다 — 폴러가 처리한다. 여기서 끼어들면 attempt만 태운다.
        return {
            "ok": False,
            "code": "in_flight",
            "message": (
                "자동 재시도가 아직 진행 중입니다(시도 "
                f"{delivery.attempt}/{MAX_ATTEMPTS}). 소진된 뒤에 다시 시도해 주세요."
            ),
        }

    if key_forgotten and not acknowledge_duplicate_risk:
        return {
            "ok": False,
            "code": "duplicate_risk_unacknowledged",
            "message": (
                "첫 발송으로부터 24시간이 지나 Resend가 멱등성 키를 잊었습니다. "
                "원래 메일이 실제로 발송됐는지 확인할 수 없으므로, 재발송하면 신청자가 "
                "같은 메일을 두 번 받을 수 있습니다. 확인했다면 중복 위험 동의를 켜고 "
                "다시 요청해 주세요."
            ),
        }

    note = f"[{now.isoformat()}] {actor} 수동 재발송: {reason}"
    if delivery is None:
        # 발송 행이 아예 없다 — 나간 메일도 없으므로 폴러가 새로 만들게 두면 된다.
        diagnosis.delivery_status = DeliveryStatus.PENDING.value
    else:
        # id는 유지한다(= 멱등성 키 유지). created_at을 되돌려 재시도 사다리와 24시간
        # 창을 함께 리셋한다 — 그러지 않으면 deliver_report가 즉시 창 밖으로 판정한다.
        delivery.created_at = now
        delivery.attempt = 0          # deliver_report가 +1 해서 1회차로 만든다
        delivery.status = DeliveryStatus.PENDING.value
        delivery.error = f"{delivery.error or ''}\n{note}".strip()[:2000]
        diagnosis.delivery_status = DeliveryStatus.PENDING.value

    return {
        "ok": True,
        "code": "rearmed",
        "message": "재발송을 예약했습니다. 1분 안에 폴러가 발송합니다.",
        "diagnosis_id": str(diagnosis.id),
        "delivery_id": str(delivery.id) if delivery is not None else None,
        "previous_status": previous_status,
        "duplicate_risk_acknowledged": bool(key_forgotten and acknowledge_duplicate_risk),
    }


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
