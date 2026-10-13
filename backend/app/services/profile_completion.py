"""프로필 완료 재판정과 완료 전환의 부수효과 — 사람 PATCH와 자동 입력 적용 공용(P4-3).

완료 여부는 요청이 아니라 저장된 프로필에서 파생한다(`hospital_lifecycle.profile_requirements`).
규칙은 `PATCH /admin/hospitals/{id}/profile`에 있던 그대로다:

- 이미 완료였던 병원은 **이번 저장이 비운 항목**이 있을 때만 완료가 풀린다. 저장 전부터 비어 있던
  레거시 공백은 완료를 되돌리지 않는다.
- 완료가 아니던 병원은 파생값을 따른다.
- 완료 전환(False→True)은 인수 수락(`HANDOFF_ACCEPTED`) 뒤에만 열린다.

자동 경로(`promote_only=True`)는 승격만 한다 — 강등은 사람이 비운 경우(PATCH)뿐이다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital
from app.services.hospital_lifecycle import profile_requirements
from app.services.operation_runs import (
    OperationCommand,
    OperationQueueUnavailable,
    dispatch_operation,
)
from app.workers.dispatch_auth import build_dispatch_headers

logger = logging.getLogger(__name__)


def profile_completion_handoff_blocker(
    _hospital: Hospital, handoff: HospitalHandoff | None
) -> dict[str, object] | None:
    if handoff is not None and handoff.state is HandoffState.HANDOFF_ACCEPTED:
        return None
    return {
        "code": "HANDOFF_NOT_ACCEPTED",
        "missing": ["handoff_accepted"],
        "message": "고객 인수 승인이 완료된 뒤 프로파일을 완료할 수 있습니다.",
    }


@dataclass(frozen=True)
class CompletionDecision:
    was_complete: bool
    derived: bool
    next_complete: bool
    missing_requirements: list[str]
    newly_missing_requirements: list[str]
    #: 완료 전환을 막은 사유(인수 미수락). 있으면 ``next_complete``는 적용되지 않았다.
    blocker: dict[str, object] | None = field(default=None)

    @property
    def transitioned(self) -> bool:
        return self.next_complete and not self.was_complete and self.blocker is None

    @property
    def blocked(self) -> bool:
        return self.blocker is not None


def decide_profile_completion(
    hospital: Hospital, *, missing_before: Iterable[str], promote_only: bool = False
) -> CompletionDecision:
    """저장 결과로 다음 완료 값을 정한다(순수 함수, 인수 확인 전)."""

    missing_before = set(missing_before)
    was_complete = bool(hospital.profile_complete)
    requirements = profile_requirements(hospital)
    missing = [item.key for item in requirements if not item.passed]
    derived = not missing
    newly_missing = [key for key in missing if key not in missing_before]

    if was_complete and newly_missing and not promote_only:
        next_complete = False
    elif was_complete:
        # 이 저장이 비우지 않은 레거시 공백은 완료를 되돌리지 않는다.
        next_complete = True
    else:
        next_complete = derived
    return CompletionDecision(
        was_complete=was_complete,
        derived=derived,
        next_complete=next_complete,
        missing_requirements=missing,
        newly_missing_requirements=newly_missing,
    )


async def evaluate_profile_completion(
    db: AsyncSession,
    hospital: Hospital,
    *,
    missing_before: Iterable[str],
    promote_only: bool = False,
) -> CompletionDecision:
    """완료 값을 다시 판정해 병원 행에 적용한다. 전환이 인수 미수락으로 막히면 적용하지 않는다.

    호출자는 병원 advisory 잠금을 이미 잡고 있어야 한다. 막힌 결정(``blocked``)에서 사람
    경로는 409를 돌려주고, 자동 경로는 조용히 건너뛴다.
    """

    decision = decide_profile_completion(
        hospital, missing_before=missing_before, promote_only=promote_only
    )
    if decision.next_complete and not decision.was_complete:
        handoff_result = await db.execute(
            select(HospitalHandoff).where(HospitalHandoff.hospital_id == hospital.id)
        )
        blocker = profile_completion_handoff_blocker(hospital, handoff_result.scalar_one_or_none())
        if blocker is not None:
            return CompletionDecision(
                was_complete=decision.was_complete,
                derived=decision.derived,
                next_complete=decision.next_complete,
                missing_requirements=decision.missing_requirements,
                newly_missing_requirements=decision.newly_missing_requirements,
                blocker=blocker,
            )
    hospital.profile_complete = decision.next_complete
    return decision


def completion_audit_detail(decision: CompletionDecision) -> dict[str, Any]:
    return {
        "profile_complete_transition": decision.transitioned,
        "profile_complete_derived": decision.derived,
        # 레거시 공백을 남겨 둔 채 완료를 유지한 저장은 파생값과 저장값이 다르다.
        "profile_complete_applied": decision.next_complete,
        "missing_requirements": decision.missing_requirements,
        "newly_missing_requirements": decision.newly_missing_requirements,
    }


async def dispatch_completion_side_effects(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    *,
    actor: str,
    schedule: Callable[..., Any] | None = None,
) -> None:
    """완료 전환 뒤 V0와 공개 페이지 준비를 각각 시작한다 — 반드시 커밋 뒤에 부른다.

    초기 진단은 오래 걸리는 백그라운드 산출물이라 그 큐의 지연·실패가 공개 준비를 막지 않는다.
    허브 태스크는 자율 복구에서도 멱등 재디스패치된다. ``schedule``이 있으면(요청 경로의
    ``BackgroundTasks.add_task``) 사이트 준비 발행을 응답 뒤로 미룬다.
    """

    from app.workers.tasks import build_aeo_site, trigger_v0_report

    site_kwargs = {
        "args": [str(hospital_id)],
        "queue": "default",
        "headers": build_dispatch_headers("build-aeo-site", str(hospital_id)),
    }
    if schedule is not None:
        schedule(build_aeo_site.apply_async, **site_kwargs)
    else:
        try:
            build_aeo_site.apply_async(**site_kwargs)
        except Exception:
            # 발행 실패는 저장을 되돌리지 않는다 — 사이트 준비 스윕이 profile_complete 병원을 다시 집는다.
            logger.warning(
                "build_aeo_site dispatch failed for hospital %s after profile complete",
                hospital_id,
                exc_info=True,
            )
    try:
        await dispatch_operation(
            db,
            OperationCommand(
                operation_type="TRIGGER_V0_REPORT",
                hospital_id=hospital_id,
                requested_by_id=None,
                idempotency_key=None,
                audit_actor=actor,
                target_type="hospital",
                target_id=str(hospital_id),
                queue="reports",
                task_args=(str(hospital_id),),
            ),
            trigger_v0_report,
        )
    except OperationQueueUnavailable:
        logger.warning("V0 report enqueue failed for hospital %s after profile complete", hospital_id)
