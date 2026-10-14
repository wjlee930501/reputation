"""병원 프로필 자동 입력 — 수집 → 초안 저장 → 빈 칸 자동 적용(D30), 그리고 15분 틱의 회수 스윕.

실행 키(``operation_runs.idempotency_key``, 병원마다 한 번씩):

- ``registration`` — 계약 등록 직후.
- ``backfill`` — 기능 이전에 등록된 병원·등록 디스패치 유실(스윕·백필 CLI).
- ``retry:{n}`` (n=1..3) — 마지막 수집의 초안이 비었거나 소스가 모두 실패했으면 1·3·7일 뒤.
- ``followup`` — 등록 때 홈페이지·블로그 없이 네이버만 읽었고, 적용으로 주소가 새로 생겼을 때 1회.
- ``apply:{run_id}`` — 수집 없이 저장된 초안을 다시 적용(적용 실패 회수·백필 CLI, 비용 0).
- ``identity:{해시}`` — 수집 뒤 네이버 신원 입력(place id·병원명·지역)이 바뀌어 적용이 네이버 값을 버렸을
  때 지금 신원으로 다시 수집(신원마다 한 번).

수집 실행은 **유료 호출 전에** 실행 키를 RUNNING 행으로 선점한다(lease 15분, 소유자 토큰). 같은 키의 다른
작업은 그 행을 보고 물러나며, lease가 지난 행(죽은 작업)만 인수한다. 수집 결과는 lease 소유자일 때만
저장한다. 비용 가드 보류·수집 예외는 선점을 지워(실행을 남기지 않으므로) 다음 틱이 다시 집는다.
초안은 ``apply_status=PENDING``으로 저장되고, 적용 결과(성공·실패)는 같은 요약에 남는다 — 적용 전에
멈춘 초안과 실패한 적용은 스윕이 수집 없이 다시 적용한다(시도 상한 3회). 자동 입력 실패·충돌은 사고·Slack을
만들지 않는다 — 남은 필수 칸은 ``PROFILE_INCOMPLETE`` 정체가 마케터에게 알린다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from celery.app.task import Task
from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError

from app.core.celery_app import celery_app
from app.core.database import get_async_sessionmaker
from app.models.handoff import HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services import cost_guard, profile_completion
from app.services.audit_log import write_audit_log
from app.services.hospital_lifecycle import missing_profile_requirement_keys
from app.services.hospital_profile_autofill import autofill_profile
from app.services.naver_place import stored_place_id
from app.services.profile_autofill_apply import (
    APPLY_ACTOR,
    RECOLLECT_KEY_PREFIX,
    apply_autofill_draft,
    collection_identity,
    load_survey_text,
)
from app.utils.db_locks import acquire_hospital_advisory_lock
from app.workers.dispatch_auth import build_dispatch_headers, require_dispatch

logger = logging.getLogger(__name__)

PROFILE_AUTOFILL_OPERATION = "PROFILE_AUTOFILL"
PROFILE_AUTOFILL_REGISTRATION_KEY = "registration"
PROFILE_AUTOFILL_BACKFILL_KEY = "backfill"
PROFILE_AUTOFILL_FOLLOWUP_KEY = "followup"
RETRY_KEY_PREFIX = "retry:"
APPLY_KEY_PREFIX = "apply:"
PROFILE_AUTOFILL_ACTOR = APPLY_ACTOR
COMPLETION_SWEEP_ACTOR = "system:profile-completion"

#: 빈 초안·전체 소스 실패 뒤 다시 수집하는 간격(마지막 수집 기준). 길이가 재시도 상한이다.
RETRY_DELAYS = (timedelta(days=1), timedelta(days=3), timedelta(days=7))
#: 적용 실패를 수집 없이 다시 적용하는 상한.
MAX_APPLY_ATTEMPTS = 3
#: 수집 선점의 lease — 작업 hard limit(300초)보다 길다. 지난 RUNNING 행은 죽은 작업이라 인수한다.
COLLECTION_LEASE = timedelta(minutes=15)
#: 저장 직후 같은 작업이 적용 중일 수 있다 — 적용 결과 없이 이만큼 지난 초안만 스윕이 다시 적용한다.
APPLY_PENDING_GRACE = timedelta(minutes=15)
APPLY_PENDING = "PENDING"
APPLY_FAILED = "FAILED"
#: 막 등록된 병원은 등록 디스패치가 처리 중일 수 있다 — 스윕이 같은 수집을 겹쳐 사지 않게 기다린다.
REGISTRATION_GRACE = timedelta(minutes=30)
SWEEP_DISPATCH_LIMIT = 5
PROMOTION_SWEEP_LIMIT = 20

_COLLECTION_KEYS = frozenset(
    {PROFILE_AUTOFILL_REGISTRATION_KEY, PROFILE_AUTOFILL_BACKFILL_KEY, PROFILE_AUTOFILL_FOLLOWUP_KEY}
)


def _existing_run_stmt(hospital_id: uuid.UUID, key: str):
    return select(OperationRun).where(
        OperationRun.hospital_id == hospital_id,
        OperationRun.operation_type == PROFILE_AUTOFILL_OPERATION,
        OperationRun.idempotency_key == key,
    )


def _is_collection_key(key: str) -> bool:
    return (
        key in _COLLECTION_KEYS
        or key.startswith(RETRY_KEY_PREFIX)
        or key.startswith(RECOLLECT_KEY_PREFIX)
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _apply_stored_run(
    hospital_id: uuid.UUID, run_id: uuid.UUID, *, allow_live: bool
) -> dict[str, Any]:
    sessions = get_async_sessionmaker()
    try:
        async with sessions() as db:
            run = await db.get(OperationRun, run_id)
            if (
                run is None
                or run.hospital_id != hospital_id
                or run.operation_type != PROFILE_AUTOFILL_OPERATION
                or run.state != OperationRunState.SUCCEEDED
            ):
                return {"status": "NOT_FOUND"}
            summary = dict(run.result_summary or {})
            outcome = await apply_autofill_draft(
                db, hospital_id, summary, run_id=run.id, allow_live=allow_live
            )
    except Exception as exc:  # noqa: BLE001 — 실패도 시도로 센다(상한이 반복을 끝낸다).
        logger.warning("profile autofill re-apply failed for %s", hospital_id, exc_info=True)
        await _mark_apply_failed(run_id, type(exc).__name__)
        return {"status": "REAPPLIED", "apply_status": APPLY_FAILED}
    return {"status": "REAPPLIED", "apply_status": outcome.status}


async def _mark_apply_failed(run_id: uuid.UUID, error: str) -> None:
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        run = await db.get(OperationRun, run_id)
        if run is None:
            return
        summary = dict(run.result_summary or {})
        summary["apply_status"] = APPLY_FAILED
        summary["apply_error"] = error[:200]
        summary["apply_attempts"] = int(summary.get("apply_attempts") or 0) + 1
        run.result_summary = summary
        await db.commit()


async def _autofill_once(
    hospital_id: uuid.UUID,
    key: str = PROFILE_AUTOFILL_REGISTRATION_KEY,
    *,
    allow_live: bool = False,
) -> dict[str, Any]:
    if key.startswith(APPLY_KEY_PREFIX):
        return await _apply_stored_run(
            hospital_id, uuid.UUID(key[len(APPLY_KEY_PREFIX) :]), allow_live=allow_live
        )
    if not _is_collection_key(key):
        return {"status": "UNKNOWN_KEY"}

    sessions = get_async_sessionmaker()
    owner = f"profile-autofill:{uuid.uuid4()}"
    async with sessions() as db:
        hospital = await db.get(Hospital, hospital_id)
        if hospital is None:
            return {"status": "NOT_FOUND"}
        if getattr(hospital, "status", None) == HospitalStatus.PAUSED:
            return {"status": "SKIPPED_PAUSED"}
        name, website_url, blog_url = hospital.name, hospital.website_url, hospital.blog_url
        naver_place_id = getattr(hospital, "naver_place_id", None)
        naver_place_url = getattr(hospital, "naver_place_url", None)
        regions = list(getattr(hospital, "region", None) or [])
        try:
            claim = await _claim_collection(db, hospital_id, key, owner)
            if isinstance(claim, str):
                await db.rollback()
                return {"status": claim}
            run_id = claim
            # 아래 SELECT의 autoflush가 선점 INSERT를 먼저 내보낼 수 있다 — 충돌은 커밋과 같게 다룬다.
            survey_text = await load_survey_text(db, hospital_id)
            await db.commit()
        except IntegrityError:
            # 같은 키를 다른 작업이 먼저 선점했다 — 유료 호출 전에 물러난다.
            await db.rollback()
            return {"status": "ALREADY_RUNNING"}

    try:
        decision = await cost_guard.check_and_increment("content")
        if not decision.allowed:
            await _release_claim(run_id, owner)
            return {"status": "COST_BLOCKED"}
        result = await autofill_profile(
            name,
            website_url=website_url,
            blog_url=blog_url,
            hospital_id=hospital_id,
            naver_place_id=naver_place_id,
            naver_place_url=naver_place_url,
            regions=regions,
            survey_text=survey_text,
        )
    except Exception:
        # 수집이 끝나지 않았다 — 선점을 지워 다음 틱이 다시 집게 한다(실행 키를 태우지 않는다).
        await _release_claim(run_id, owner)
        raise
    sources = [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.sources]
    now = datetime.now(UTC)
    collected_without_channels = not ((website_url or "").strip() or (blog_url or "").strip())
    summary = {
        "draft": result.draft,
        "field_meta": result.field_meta,
        "violations": result.violations,
        "rejected_fields": result.rejected_fields,
        "conflicts": result.conflicts,
        "naver_place_id": result.naver_place_id,
        "naver_identity": result.naver_identity,
        "sources": sources,
        "collected_without_channels": collected_without_channels,
        # 적용이 같은 병원인지 다시 확인할 신원 입력 — 수집 뒤 바뀌면 네이버 값을 버린다.
        "collection_identity": collection_identity(
            name=name,
            naver_place_id=stored_place_id(naver_place_id, naver_place_url),
            regions=regions,
        ),
        # 적용 전에 멈춰도 스윕이 알아보게 — 적용 결과가 이 값을 덮는다.
        "apply_status": APPLY_PENDING,
        "apply_attempts": 0,
    }
    async with sessions() as db:
        stored = await db.execute(
            update(OperationRun)
            .where(
                OperationRun.id == run_id,
                OperationRun.state == OperationRunState.RUNNING,
                OperationRun.lease_owner == owner,
            )
            .values(
                state=OperationRunState.SUCCEEDED,
                success_count=1,
                result_summary=summary,
                completed_at=now,
                heartbeat_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if stored.rowcount != 1:
            # lease가 지나 다른 작업이 인수했다 — 그 작업이 저장·적용한다.
            await db.rollback()
            return {"status": "LEASE_LOST"}
        await write_audit_log(
            db,
            action="autofill_profile",
            hospital_id=hospital_id,
            actor=PROFILE_AUTOFILL_ACTOR,
            target_type="hospital",
            target_id=hospital_id,
            detail={
                "name": name,
                "key": key,
                "filled_fields": sorted(result.draft.keys()),
                "violation_fields": [v["field"] for v in result.violations],
                "rejected_fields": [v["field"] for v in result.rejected_fields],
                "naver_identity": result.naver_identity,
                "sources": sources,
            },
        )
        await db.commit()

    try:
        async with sessions() as db:
            outcome = await apply_autofill_draft(
                db,
                hospital_id,
                summary,
                run_id=run_id,
                allow_live=allow_live,
                collected_without_channels=collected_without_channels,
            )
    except Exception as exc:  # noqa: BLE001 — 초안은 저장됐다. 스윕이 수집 없이 다시 적용한다.
        logger.warning("profile autofill apply failed for %s", hospital_id, exc_info=True)
        await _mark_apply_failed(run_id, type(exc).__name__)
        return {"status": "DRAFT_STORED", "apply_status": APPLY_FAILED}
    return {"status": "DRAFT_STORED", "apply_status": outcome.status}


async def _claim_collection(
    db: Any, hospital_id: uuid.UUID, key: str, owner: str
) -> uuid.UUID | str:
    """실행 키를 유료 호출 전에 선점한다. 선점한 실행 id, 아니면 물러날 사유를 돌려준다.

    - 행이 없으면 RUNNING 행을 만든다(유일 색인이 동시 INSERT 하나만 남긴다 — 커밋에서 갈린다).
    - lease가 지난 RUNNING 행은 죽은 작업이다 — 소유자를 바꿔 인수한다(시도 수 +1).
    - 끝난 행은 ``ALREADY_DONE``, lease가 살아 있는 RUNNING 행은 ``ALREADY_RUNNING``.
    """

    now = datetime.now(UTC)
    existing = await db.scalar(_existing_run_stmt(hospital_id, key).with_for_update())
    if existing is None:
        run = OperationRun(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            operation_type=PROFILE_AUTOFILL_OPERATION,
            idempotency_key=key,
            state=OperationRunState.RUNNING,
            total_count=1,
            attempt_count=1,
            lease_owner=owner,
            lease_expires_at=now + COLLECTION_LEASE,
            heartbeat_at=now,
            request_payload={
                "trigger": "contract_registration"
                if key == PROFILE_AUTOFILL_REGISTRATION_KEY
                else key
            },
            requested_at=now,
            started_at=now,
        )
        db.add(run)
        return run.id
    if existing.state != OperationRunState.RUNNING:
        return "ALREADY_DONE"
    lease = getattr(existing, "lease_expires_at", None)
    if lease is not None and _aware(lease) > now:
        return "ALREADY_RUNNING"
    existing.lease_owner = owner
    existing.lease_expires_at = now + COLLECTION_LEASE
    existing.heartbeat_at = now
    existing.attempt_count = int(existing.attempt_count or 0) + 1
    return existing.id


async def _release_claim(run_id: uuid.UUID, owner: str) -> None:
    """끝나지 않은 수집의 선점을 지운다(소유자일 때만). 실패하면 lease 만료가 같은 일을 한다."""

    try:
        async with get_async_sessionmaker()() as db:
            await db.execute(
                delete(OperationRun).where(
                    OperationRun.id == run_id,
                    OperationRun.state == OperationRunState.RUNNING,
                    OperationRun.lease_owner == owner,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("profile autofill claim release failed for run %s", run_id, exc_info=True)


@celery_app.task(
    name="app.workers.profile_autofill_tasks.autofill_registered_hospital_profile",
    bind=True,
    soft_time_limit=240,
    time_limit=300,
)
def autofill_registered_hospital_profile(
    self: Task,
    hospital_id: str,
    key: str = PROFILE_AUTOFILL_REGISTRATION_KEY,
    allow_live: bool = False,
) -> dict[str, Any]:
    require_dispatch(self, "autofill-registered-profile", hospital_id)

    async def _run() -> dict[str, Any]:
        return await _autofill_once(uuid.UUID(hospital_id), key, allow_live=allow_live)

    return anyio.run(_run)


def dispatch_profile_autofill(
    hospital_id: uuid.UUID | str, key: str, *, allow_live: bool = False
) -> bool:
    """자동 입력 실행을 워커에 보낸다. 발행 실패는 False — 다음 틱이 다시 집는다."""

    args: list[Any] = [str(hospital_id), key]
    if allow_live:
        args.append(True)
    try:
        autofill_registered_hospital_profile.apply_async(
            args=args,
            queue="default",
            headers=build_dispatch_headers("autofill-registered-profile", str(hospital_id)),
        )
    except Exception:
        logger.warning("profile autofill dispatch failed for %s (%s)", hospital_id, key)
        return False
    return True


# ── 스윕(15분 reconcile 틱, E3 — RedBeat 변경 없음) ─────────────────────


def _run_time(run: Any) -> datetime | None:
    value = getattr(run, "completed_at", None) or getattr(run, "created_at", None)
    return _aware(value) if value is not None else None


def _draft_is_empty(summary: Mapping[str, Any]) -> bool:
    draft = summary.get("draft") if isinstance(summary.get("draft"), Mapping) else {}
    sources = summary.get("sources") if isinstance(summary.get("sources"), list) else []
    useful = [key for key in draft if key not in {"naver_place_id", "naver_place_url"}]
    all_failed = bool(sources) and not any(
        isinstance(item, Mapping) and item.get("ok") for item in sources
    )
    return not useful or all_failed


def next_autofill_key(runs: Sequence[Any], *, now: datetime) -> str | None:
    """이 병원의 자동 입력 실행 기록으로 다음에 보낼 실행 키를 정한다(없으면 None).

    ``runs``는 PROFILE_AUTOFILL 실행 행(``id``·``idempotency_key``·``result_summary``·시각)이다.
    """

    if not runs:
        return PROFILE_AUTOFILL_BACKFILL_KEY
    epoch = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(runs, key=lambda run: _run_time(run) or epoch)
    keys = {str(run.idempotency_key) for run in ordered}
    latest = ordered[-1]
    if str(getattr(latest, "state", OperationRunState.SUCCEEDED)) == OperationRunState.RUNNING:
        # 수집 중이다. lease가 지났으면 죽은 작업이라 같은 키로 인수한다.
        lease = getattr(latest, "lease_expires_at", None)
        if lease is None or _aware(lease) <= now:
            return str(latest.idempotency_key)
        return None
    summary = dict(latest.result_summary or {})
    attempts = int(summary.get("apply_attempts") or 0)
    if summary.get("apply_status") == APPLY_FAILED and attempts < MAX_APPLY_ATTEMPTS:
        return f"{APPLY_KEY_PREFIX}{latest.id}"
    latest_at = _run_time(latest)
    # 적용 결과 키가 아예 없는 초안은 이 기능 이전의 '저장만' 초안이다 — 대표가 dry-run을 본 뒤 백필 CLI가
    # 적용한다. 스윕은 이 기능이 PENDING으로 저장한 초안만 회수한다.
    if (
        summary.get("apply_status") == APPLY_PENDING
        and not _draft_is_empty(summary)
        and attempts < MAX_APPLY_ATTEMPTS
        and latest_at is not None
        and now >= latest_at + APPLY_PENDING_GRACE
    ):
        # 초안은 커밋됐는데 적용 결과가 없다(저장 직후 작업이 죽었다) — 수집 없이 적용한다.
        return f"{APPLY_KEY_PREFIX}{latest.id}"
    recollect = summary.get("recollect_key")
    if isinstance(recollect, str) and recollect.startswith(RECOLLECT_KEY_PREFIX) and recollect not in keys:
        # 수집 뒤 신원이 바뀌어 네이버 값을 버렸다 — 지금 신원으로 한 번 다시 수집한다.
        return recollect
    if summary.get("followup_needed") and PROFILE_AUTOFILL_FOLLOWUP_KEY not in keys:
        return PROFILE_AUTOFILL_FOLLOWUP_KEY
    if _draft_is_empty(summary):
        retries = sum(1 for key in keys if key.startswith(RETRY_KEY_PREFIX))
        if (
            retries < len(RETRY_DELAYS)
            and latest_at is not None
            and now >= latest_at + RETRY_DELAYS[retries]
        ):
            return f"{RETRY_KEY_PREFIX}{retries + 1}"
    return None


def sweep_profile_autofill(
    *, now: datetime | None = None, limit: int = SWEEP_DISPATCH_LIMIT
) -> int:
    """기능 이전 병원·유실된 등록·재시도 기한·followup·적용 실패를 틱당 ``limit``곳까지 보낸다.

    일시정지 병원과 공개 중인 병원(ACTIVE·site_live)은 건너뛴다 — 공개 중인 병원의 빈 칸 채우기는
    대표가 백필 CLI dry-run을 본 뒤 ``--include-live``로만 한다.
    """

    from app.core.database import SyncSessionLocal

    now = now or datetime.now(UTC)
    with SyncSessionLocal() as db:
        hospitals = db.execute(
            select(Hospital.id, Hospital.status, Hospital.site_live, Hospital.created_at)
            .where(Hospital.status != HospitalStatus.PAUSED)
            .order_by(Hospital.created_at, Hospital.id)
        ).all()
        candidate_ids = [
            row.id
            for row in hospitals
            if not (row.status == HospitalStatus.ACTIVE and bool(row.site_live))
            and (row.created_at is None or _aware(row.created_at) <= now - REGISTRATION_GRACE)
        ]
        if not candidate_ids:
            return 0
        runs = (
            db.execute(
                select(OperationRun).where(
                    OperationRun.operation_type == PROFILE_AUTOFILL_OPERATION,
                    OperationRun.hospital_id.in_(candidate_ids),
                )
            )
            .scalars()
            .all()
        )
        by_hospital: dict[uuid.UUID, list[OperationRun]] = {}
        for run in runs:
            by_hospital.setdefault(run.hospital_id, []).append(run)
        planned: list[tuple[uuid.UUID, str]] = []
        for hospital_id in candidate_ids:
            key = next_autofill_key(by_hospital.get(hospital_id, []), now=now)
            if key is not None:
                planned.append((hospital_id, key))
            if len(planned) >= limit:
                break
    return sum(1 for hospital_id, key in planned if dispatch_profile_autofill(hospital_id, key))


async def promote_completed_profiles(*, limit: int = PROMOTION_SWEEP_LIMIT) -> list[str]:
    """필수 항목이 모두 찬 미완료 병원을 완료로 승격한다(승격만, 일시정지 제외).

    D31로 술어가 줄어 지금 막혀 있던 병원도 이 스윕이 연다. 인수 수락이 없으면 건너뛴다.
    승격은 사람 PATCH와 같은 전환 부수효과(사이트 준비·V0)를 낸다.
    """

    sessions = get_async_sessionmaker()
    async with sessions() as db:
        rows = (
            (
                await db.execute(
                    select(Hospital)
                    .where(
                        Hospital.profile_complete.is_not(True),
                        Hospital.status != HospitalStatus.PAUSED,
                        # 인수 수락을 상한보다 먼저 거른다 — 수락 안 된 병원이 상한을 채워 뒤의
                        # 승격 가능 병원을 굶기지 않게.
                        exists().where(
                            HospitalHandoff.hospital_id == Hospital.id,
                            HospitalHandoff.state == HandoffState.HANDOFF_ACCEPTED,
                        ),
                    )
                    .order_by(Hospital.created_at, Hospital.id)
                )
            )
            .scalars()
            .all()
        )
        candidates = [row.id for row in rows if not missing_profile_requirement_keys(row)][:limit]

    promoted: list[str] = []
    for hospital_id in candidates:
        async with sessions() as db:
            await acquire_hospital_advisory_lock(db, hospital_id)
            hospital = await db.get(Hospital, hospital_id, populate_existing=True)
            if (
                hospital is None
                or hospital.profile_complete
                or hospital.status == HospitalStatus.PAUSED
            ):
                await db.rollback()
                continue
            completion = await profile_completion.evaluate_profile_completion(
                db, hospital, missing_before=set(), promote_only=True
            )
            if not completion.transitioned:
                await db.rollback()
                continue
            await write_audit_log(
                db,
                action="promote_profile_complete",
                hospital_id=hospital_id,
                actor=COMPLETION_SWEEP_ACTOR,
                target_type="hospital",
                target_id=hospital_id,
                detail=profile_completion.completion_audit_detail(completion),
            )
            await db.commit()
            await profile_completion.dispatch_completion_side_effects(
                db, hospital_id, actor=COMPLETION_SWEEP_ACTOR
            )
            promoted.append(str(hospital_id))
    return promoted


def run_onboarding_sweeps() -> dict[str, int]:
    """15분 reconcile 틱(E3)에 붙는 온보딩 스윕. 한 쪽 실패가 다른 쪽·틱을 막지 않는다."""

    result = {"profiles_promoted": 0, "profile_autofill_dispatched": 0}
    try:
        result["profiles_promoted"] = len(anyio.run(promote_completed_profiles))
    except Exception:
        logger.warning("profile completion promotion sweep failed", exc_info=True)
    try:
        result["profile_autofill_dispatched"] = sweep_profile_autofill()
    except Exception:
        logger.warning("profile autofill sweep failed", exc_info=True)
    return result
