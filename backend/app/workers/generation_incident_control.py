"""Incident and Slack-outbox projection for content generation failures."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_sessionmaker
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import (
    build_incident_key,
    mark_recovered,
    mark_retrying,
    open_or_touch_incident,
)
from app.services.notification_contracts import IncidentSlackProjection
from app.services.notification_messages import build_open_incident_notification
from app.services.notification_store import enqueue_notification

_EXPECTED_PENDING_CODES = {
    "MISSING_APPROVED_ESSENCE",
    "COST_BLOCKED",
    "GENERATION_LEASE_ACTIVE",
    "STALE_GENERATION_CLAIM",
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
}


def _generation_operator_copy(code: str) -> tuple[str, str]:
    impact = "발행 예정 콘텐츠가 저장되지 않아 병원 채널에 제때 공개되지 않습니다."
    actions = {
        "PROVIDER_TIMEOUT": (
            "일시적인 응답 지연입니다. 다음 예약 배치가 자동으로 다시 시도하므로 지금은 기다리세요."
        ),
        "PROVIDER_UNAVAILABLE": (
            "일시적인 외부 서비스 장애입니다. 다음 예약 배치가 자동으로 다시 시도하므로 지금은 기다리세요."
        ),
        "GENERATION_REJECTED": (
            "운영 센터에서 해당 항목의 “작업 다시 시도”를 한 번 누르고 완료 결과를 확인하세요."
        ),
        "MISSING_APPROVED_ESSENCE": (
            "병원별로 근거 자료를 처리하고 운영 기준을 한 번 승인하세요. 승인 전에는 재시도할 "
            "필요가 없으며, 승인 후 다음 예약 배치에서 자동으로 다시 생성합니다."
        ),
        "COST_BLOCKED": (
            "운영 센터 하단의 “비용·자동 작업 안전장치”를 펼치세요. 전체 중지 상태면 “중지 해제”를 "
            "누르고, 오늘 한도에 도달했다면 계정 소유자에게 “오늘 한도 2배” 조치를 요청하세요."
        ),
        "GENERATION_LEASE_ACTIVE": (
            "다른 생성 작업이 진행 중입니다. 완료될 때까지 기다린 뒤 운영 센터를 새로고침하세요."
        ),
        "STALE_GENERATION_CLAIM": (
            "이전 작업 기록이 남아 자동 생성이 시작되지 않았습니다. 운영 센터를 새로고침해 "
            "현재 상태를 다시 확인하세요."
        ),
        "CONTENT_NOT_GENERATED": (
            "운영 센터에서 해당 항목의 “작업 다시 시도”를 누르세요. 자동 복구는 "
            "01시·04시·07시·07시 45분에도 다시 실행됩니다."
        ),
        "MISSING_REFERENCES": (
            "운영 센터에서 해당 항목의 “작업 다시 시도”를 눌러 참고 자료가 포함된 "
            "본문을 다시 생성하세요."
        ),
        "FORBIDDEN_EXPRESSION": (
            "운영 센터에서 해당 항목의 “작업 다시 시도”를 눌러 의료광고 금지 표현이 없는 "
            "본문을 다시 생성하세요."
        ),
        "ESSENCE_NOT_ALIGNED": (
            "병원 온보딩에서 운영 기준을 확인한 뒤 운영 센터의 “작업 다시 시도”를 누르세요."
        ),
        "CONTENT_IMAGE_NOT_READY": (
            "운영 센터에서 해당 항목의 “대표 이미지 다시 생성”을 누르고 완료 결과를 확인하세요."
        ),
        "IMAGE_GENERATION_FAILED": (
            "본문은 저장되어 있습니다. 운영 센터에서 해당 항목의 “대표 이미지 다시 생성”을 한 번 누르세요."
        ),
    }
    action = actions.get(
        code,
        "운영 센터에 “작업 다시 시도”가 보이면 누르고 완료 결과를 확인하세요.",
    )
    return impact, action


def _generation_safe_cause(code: str) -> str:
    return {
        "PROVIDER_TIMEOUT": "콘텐츠 생성 서비스의 응답이 제시간에 오지 않았습니다.",
        "PROVIDER_UNAVAILABLE": "콘텐츠 생성 서비스를 일시적으로 사용할 수 없습니다.",
        "GENERATION_REJECTED": "콘텐츠 생성 서비스가 이번 요청을 처리하지 못했습니다.",
        "MISSING_APPROVED_ESSENCE": "승인된 콘텐츠 운영 기준이 없어 자동 생성을 시작하지 않았습니다.",
        "COST_BLOCKED": "오늘 설정된 사용 한도에 도달해 자동 생성을 시작하지 않았습니다.",
        "IMAGE_GENERATION_FAILED": "본문은 준비됐지만 대표 이미지를 만들지 못했습니다.",
        "GENERATION_LEASE_ACTIVE": "같은 콘텐츠의 다른 생성 작업이 아직 진행 중입니다.",
        "STALE_GENERATION_CLAIM": "완료되지 않은 이전 작업 기록 때문에 새 생성을 시작하지 못했습니다.",
        "CONTENT_NOT_GENERATED": "발행 시각까지 콘텐츠 제목과 본문이 준비되지 않았습니다.",
        "MISSING_REFERENCES": "의료 콘텐츠에 필요한 참고 자료가 준비되지 않았습니다.",
        "FORBIDDEN_EXPRESSION": "의료광고 금지 표현이 발견되어 공개를 중단했습니다.",
        "ESSENCE_NOT_ALIGNED": "콘텐츠가 승인된 운영 기준의 자동 검사를 통과하지 못했습니다.",
        "CONTENT_IMAGE_NOT_READY": "대표 이미지가 준비되지 않아 공개를 중단했습니다.",
    }.get(code, "자동 콘텐츠 생성 작업이 완료되지 않았습니다.")


def _generation_severity(code: str) -> IncidentSeverity:
    """Separate expected preparation/wait states from publication blockers."""

    if code in {
        "MISSING_APPROVED_ESSENCE",
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_LEASE_ACTIVE",
        "STALE_GENERATION_CLAIM",
    }:
        return IncidentSeverity.MEDIUM
    return IncidentSeverity.HIGH


def _fingerprint(code: str) -> IncidentFingerprint:
    return {
        "PROVIDER_TIMEOUT": IncidentFingerprint.PROVIDER_TIMEOUT,
        "PROVIDER_UNAVAILABLE": IncidentFingerprint.PROVIDER_REJECTED,
        "GENERATION_REJECTED": IncidentFingerprint.PROVIDER_REJECTED,
        "MISSING_APPROVED_ESSENCE": IncidentFingerprint.MISSING_PREREQUISITE,
        "COST_BLOCKED": IncidentFingerprint.COST_BLOCKED,
        "IMAGE_GENERATION_FAILED": IncidentFingerprint.RENDER_FAILED,
        "GENERATION_LEASE_ACTIVE": IncidentFingerprint.VALIDATION_FAILED,
        "STALE_GENERATION_CLAIM": IncidentFingerprint.VALIDATION_FAILED,
        "CONTENT_NOT_GENERATED": IncidentFingerprint.MISSING_PREREQUISITE,
        "MISSING_REFERENCES": IncidentFingerprint.MISSING_PREREQUISITE,
        "FORBIDDEN_EXPRESSION": IncidentFingerprint.SAFETY_BLOCKED,
        "ESSENCE_NOT_ALIGNED": IncidentFingerprint.VALIDATION_FAILED,
        "CONTENT_IMAGE_NOT_READY": IncidentFingerprint.RENDER_FAILED,
    }.get(code, IncidentFingerprint.UNKNOWN)


def _incident_identity(
    code: str, item_id: uuid.UUID, hospital_id: uuid.UUID
) -> tuple[str, str, str]:
    """Use one durable incident per hospital for a hospital-level preparation gate."""

    if code == "MISSING_APPROVED_ESSENCE":
        return "hospital", str(hospital_id), f"/hospitals/{hospital_id}/essence"
    return "content_item", str(item_id), "/operations"


def _should_send_generation_notification(
    *,
    notify_requested: bool,
    previous_state: str | None,
    code: str | None = None,
    has_open_cause: bool = False,
) -> bool:
    """Page once per incident episode, never once per observation or expected gate item."""

    if not notify_requested:
        return False
    if code in _EXPECTED_PENDING_CODES:
        return False
    if code == "CONTENT_NOT_GENERATED" and has_open_cause:
        return False
    return previous_state is None or previous_state in {
        IncidentState.RECOVERED.value,
        IncidentState.ACKNOWLEDGED.value,
    }


def _projection(
    incident: Incident, hospital_name: str, run_id: uuid.UUID | None, owner: str, sla: str
) -> IncidentSlackProjection:
    return IncidentSlackProjection(
        incident.id,
        hospital_name,
        incident.severity,
        incident.customer_impact,
        incident.next_action,
        incident.admin_path,
        owner,
        sla,
        incident.hospital_id,
        run_id,
        incident.version,
        incident.safe_error_message or _generation_safe_cause(incident.safe_error_code or ""),
        incident.episode_seq,
    )


async def open_generation_incident(
    *,
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID,
    code: str,
    message: str,
    notify: bool = True,
) -> uuid.UUID:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        object_type, object_id, admin_path = _incident_identity(code, item_id, hospital_id)
        dedupe_key = build_incident_key(
            "content_generation", object_type, object_id, _fingerprint(code)
        )
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == dedupe_key))
        previous_state = previous.state if previous is not None else None
        has_open_cause = False
        if code == "CONTENT_NOT_GENERATED":
            has_open_cause = (
                await db.scalar(
                    select(Incident.id).where(
                        Incident.hospital_id == hospital_id,
                        Incident.state.in_((
                            IncidentState.OPEN.value,
                            IncidentState.RETRYING.value,
                        )),
                        Incident.safe_error_code != "CONTENT_NOT_GENERATED",
                        Incident.source_id.in_((str(item_id), str(hospital_id))),
                    )
                )
            ) is not None

        # The old implementation opened one incident per content item for this
        # hospital-level gate.  During the first rollout of the hospital-scoped
        # key, inherit any still-open legacy episode so the migration itself does
        # not page the operator again.
        if code == "MISSING_APPROVED_ESSENCE" and previous is None:
            legacy_open = await db.scalar(
                select(Incident).where(
                    Incident.hospital_id == hospital_id,
                    Incident.source_type == "CONTENT_GENERATION",
                    Incident.safe_error_code == code,
                    Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
                )
            )
            if legacy_open is not None:
                previous_state = legacy_open.state

        # An unchanged preparation gate has no new operational information.
        # Item-level operation runs still record every scheduler observation;
        # avoid churning the durable incident version and outbox projection.
        if (
            code == "MISSING_APPROVED_ESSENCE"
            and previous is not None
            and previous.state == IncidentState.OPEN
        ):
            return previous.id

        customer_impact, next_action = _generation_operator_copy(code)
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                pipeline="content_generation",
                object_type=object_type,
                object_id=object_id,
                fingerprint=_fingerprint(code),
                incident_type="CONTENT_GENERATION_FAILED",
                severity=_generation_severity(code),
                customer_impact=customer_impact,
                source_type="CONTENT_GENERATION",
                next_action=next_action,
                admin_path=admin_path,
                hospital_id=hospital_id,
                operation_run_id=run_id,
                source_id=object_id,
                safe_error_code=code,
                safe_error_message=_generation_safe_cause(code),
            ),
            actor="content-generation-worker",
            reason="generation attempt failed",
        )
        if _should_send_generation_notification(
            notify_requested=notify,
            previous_state=previous_state,
            code=code,
            has_open_cause=has_open_cause,
        ):
            await enqueue_notification(
                db,
                build_open_incident_notification(
                    _projection(
                        incident,
                        hospital_name,
                        run_id,
                        "병원 운영 담당자",
                        "예정 공개 전",
                    ),
                    settings.ADMIN_BASE_URL,
                ),
            )
        await db.commit()
        return incident.id


async def recover_generation_incidents(
    item_id: uuid.UUID,
    hospital_id: uuid.UUID,
    hospital_name: str,
    run_id: uuid.UUID | None,
    *,
    include_image: bool = True,
    safe_error_codes: tuple[str, ...] | None = None,
) -> int:
    """Close incidents from observed success without paging humans about healthy recovery."""

    sessions = get_async_sessionmaker()
    recovered = 0
    async with sessions() as db:
        source_scope = (Incident.source_id == str(item_id)) | (
            (Incident.safe_error_code == "MISSING_APPROVED_ESSENCE")
            & (Incident.source_id == str(hospital_id))
        )
        statement = select(Incident).where(
            Incident.hospital_id == hospital_id,
            Incident.source_type == "CONTENT_GENERATION",
            source_scope,
            Incident.state.in_((IncidentState.OPEN, IncidentState.RETRYING)),
        )
        if safe_error_codes is not None:
            statement = statement.where(Incident.safe_error_code.in_(safe_error_codes))
        elif not include_image:
            statement = statement.where(
                Incident.safe_error_code.notin_(
                    ("IMAGE_GENERATION_FAILED", "CONTENT_IMAGE_NOT_READY")
                )
            )
        incidents = list((await db.execute(statement)).scalars())
        for incident in incidents:
            current = incident
            if current.state == IncidentState.OPEN:
                retrying = await mark_retrying(
                    db,
                    current.id,
                    expected_version=current.version,
                    actor="content-generation-worker",
                    reason="generation retry started",
                )
                if not isinstance(retrying, Incident):
                    continue
                current = retrying
            if run_id is not None:
                current.operation_run_id = run_id
            result = await mark_recovered(
                db,
                current.id,
                expected_version=current.version,
                observed_success=True,
                actor="content-generation-worker",
                reason="generation retry succeeded",
            )
            if not isinstance(result, Incident):
                continue
            recovered += 1
        await db.commit()
    return recovered
