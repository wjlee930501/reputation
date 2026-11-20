"""Admin — 병원 공개 페이지 섹션 설정(레이아웃 편집기) 쓰기 API(설계 4절, 단계 3a).

초안 저장 → 미리보기 → 공개 반영 → 이력 → 복원/기본으로. 편집 화면은 3c(어드민 세션)가 만든다.

- 모든 쓰기는 병원 advisory 잠금 안에서 하고 사람 계정(`require_active_account`)을 요구한다 — 공통
  의존성은 시스템 actor(`X-Admin-Actor-System`)를 받으므로, 화면 설정을 배치가 바꾸지 못하게 여기서 막는다.
  공개 반영의 멱등 영수증도 그 계정에 묶는다.
- 초안을 바꾸거나 쓰는 요청은 모두 `{draft_id, revision}` 전제조건을 보낸다. 초안이 없을 것을 기대하면
  둘 다 비운다. 다르면 409 `DRAFT_CHANGED`(최신 초안 동봉) — A가 미리 본 판 대신 B가 그 뒤 저장한 판이
  공개되는 경로를 막는다.
- 초안 저장은 화면에 영향이 없다(재검증 없음). 공개 반영만 `hospitals.site_layout`과 함께 쓰고, 공개
  중인 병원이면 같은 트랜잭션에 공개 표면 재검증 intent를 남긴 뒤 커밋 후 재검증을 부른다.
- 검증은 저장·공개 반영 둘 다 `site_config_validation.validate_site_config`이고, 공개 반영 직전에 지금
  사실(사진 공개·FAQ 글 공개)로 **다시** 돈다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.accounts import require_active_account
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.hospital import Hospital
from app.models.operations import OperationRun, OperationRunState
from app.models.site_config import (
    ORIGIN_EDIT,
    ORIGIN_RESET,
    ORIGIN_RESTORE,
    SITE_CONFIG_DRAFT,
    SITE_CONFIG_PUBLISHED,
    SITE_CONFIG_SUPERSEDED,
    HospitalSiteConfig,
)
from app.services import site_config_refs, site_defaults
from app.services.audit_log import default_actor, write_audit_log
from app.services.hospital_lifecycle import profile_publication_verdict
from app.services.operation_run_keys import normalize_operation_key
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.services.site_config_validation import (
    SiteConfigCheck,
    changed_sections,
    line_break_source,
    lines_match,
    validate_site_config,
)
from app.services.site_preview_token import issue_preview_token, preview_enabled
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)
from app.utils.db_locks import acquire_hospital_advisory_lock

router = APIRouter(prefix="/admin/hospitals", tags=["Admin — Site config"])

PUBLISH_OPERATION_TYPE = "PUBLISH_SITE_CONFIG"
HISTORY_LIMIT = 50
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


class DraftExpectation(BaseModel):
    """바꾸려는 초안. 둘 다 비우면 '초안이 없다'를 기대한다."""

    model_config = ConfigDict(extra="forbid")

    draft_id: uuid.UUID | None = None
    revision: int | None = None


class DraftSave(DraftExpectation):
    layout: str
    config: dict[str, Any]


class DraftReset(DraftExpectation):
    layout: str


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_config(row: HospitalSiteConfig | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "state": row.state,
        "version": row.version,
        "layout": row.layout,
        "config": row.config or {},
        "resolved": row.resolved or {},
        "defaults_version": row.defaults_version,
        "schema_version": row.schema_version,
        "revision": row.revision,
        "origin": row.origin,
        "based_on_id": str(row.based_on_id) if row.based_on_id else None,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "updated_by": row.updated_by,
        "updated_at": _iso(row.updated_at),
        "published_by": row.published_by,
        "published_at": _iso(row.published_at),
        "change_summary": row.change_summary or [],
    }


def _history_entry(row: HospitalSiteConfig) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "state": row.state,
        "version": row.version,
        "layout": row.layout,
        "origin": row.origin,
        "published_by": row.published_by,
        "published_at": _iso(row.published_at),
        "change_summary": row.change_summary or [],
        "defaults_version": row.defaults_version,
    }


async def _hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="병원을 찾을 수 없습니다.")
    return hospital


async def _row(
    db: AsyncSession, hospital_id: uuid.UUID, state: str, *, for_update: bool = False
) -> HospitalSiteConfig | None:
    stmt = select(HospitalSiteConfig).where(
        HospitalSiteConfig.hospital_id == hospital_id, HospitalSiteConfig.state == state
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


def _require_draft_match(
    draft: HospitalSiteConfig | None, expected: DraftExpectation, *, allow_none: bool
) -> None:
    if draft is None:
        if expected.draft_id is None and expected.revision is None and allow_none:
            return
    elif expected.draft_id == draft.id and expected.revision == draft.revision:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "DRAFT_CHANGED",
            "message": "그 사이 초안이 바뀌었습니다. 최신 초안을 다시 불러와 확인해 주세요.",
            "draft": serialize_config(draft),
        },
    )


def _reject_invalid(check: SiteConfigCheck) -> None:
    if not check.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SITE_CONFIG_INVALID",
                "message": "설정에 고칠 곳이 있습니다.",
                "errors": check.errors,
                "warnings": check.warnings,
            },
        )


def _validate_layout(layout: str) -> None:
    if layout not in site_defaults.LAYOUTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "LAYOUT_INVALID", "message": "레이아웃은 A·B·C 중 하나여야 합니다."},
        )


def _line_break_warnings(row: HospitalSiteConfig | None, hospital: Hospital) -> list[dict[str, Any]]:
    """원문이 바뀌어 공개 화면에서 꺼진 줄바꿈 지정(저장값은 남는다)."""

    if row is None:
        return []
    ctx = site_config_refs.text_context(hospital)
    warnings = []
    for path, spec in ((row.config or {}).get("hero") or {}).get("line_breaks", {}).items():
        source = line_break_source(path, ctx)
        values = [value for value in (spec or {}).values() if isinstance(value, str)]
        if not source or any(not lines_match(value, source) for value in values):
            warnings.append(
                {
                    "path": f"hero.line_breaks.{path}",
                    "code": "LINES_SOURCE_CHANGED",
                    "message": "병원 정보의 문장이 바뀌어 이 줄바꿈 지정은 공개 화면에서 꺼져 있습니다.",
                }
            )
    return warnings


@router.get("/{hospital_id}/site-config")
async def get_site_config(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    hospital = await _hospital_or_404(db, hospital_id)
    published = await _row(db, hospital_id, SITE_CONFIG_PUBLISHED)
    draft = await _row(db, hospital_id, SITE_CONFIG_DRAFT)
    history = (
        await db.execute(
            select(HospitalSiteConfig)
            .where(
                HospitalSiteConfig.hospital_id == hospital_id,
                HospitalSiteConfig.state.in_([SITE_CONFIG_PUBLISHED, SITE_CONFIG_SUPERSEDED]),
            )
            .order_by(HospitalSiteConfig.version.desc())
            .limit(HISTORY_LIMIT)
        )
    ).scalars().all()
    warnings = _line_break_warnings(draft or published, hospital)
    if published is not None and published.layout != hospital.site_layout:
        warnings.append(
            {
                "path": "layout",
                "code": "SITE_LAYOUT_DIVERGED",
                "message": "병원 정보의 레이아웃이 공개 설정과 달라 공개 화면은 설정 없이 그려집니다.",
            }
        )
    return {
        "hospital_id": str(hospital_id),
        "site_layout": hospital.site_layout,
        "published": serialize_config(published),
        "draft": serialize_config(draft),
        "defaults": {layout: site_defaults.defaults(layout) for layout in site_defaults.LAYOUTS},
        "defaults_version": site_defaults.DEFAULTS_VERSION,
        "history": [_history_entry(row) for row in history],
        "warnings": warnings,
        "preview_enabled": preview_enabled(),
    }


@router.get("/{hospital_id}/site-config/faq-candidates")
async def list_site_config_faq_candidates(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """FAQ 직접 고르기 후보 — 홈 FAQ와 같은 집합(공개 FAQ 글 중 승인 질문·답이 있는 것), 최근 공개 순.

    질문 문장은 승인된 그대로 보인다(편집기에서 고치지 않는다, I8). 공개 중이 아닌 병원은 빈 목록이다.
    """

    hospital = await _hospital_or_404(db, hospital_id)
    entries = await site_config_refs.public_faq_entries(db, hospital)
    return {
        "items": [{"id": str(content_id), "question": entry["question"]} for content_id, entry in entries.items()],
        "limit": site_config_refs.FAQ_SCAN_LIMIT,
    }


async def _write_draft(
    db: AsyncSession,
    hospital: Hospital,
    draft: HospitalSiteConfig | None,
    *,
    layout: str,
    check: SiteConfigCheck,
    origin: str,
    based_on_id: uuid.UUID | None,
) -> HospitalSiteConfig:
    actor = default_actor()
    assert check.config is not None and check.resolved is not None
    if draft is None:
        draft = HospitalSiteConfig(
            hospital_id=hospital.id,
            state=SITE_CONFIG_DRAFT,
            layout=layout,
            config=check.config,
            resolved=check.resolved,
            defaults_version=site_defaults.DEFAULTS_VERSION,
            schema_version=site_defaults.SCHEMA_VERSION,
            revision=1,
            based_on_id=based_on_id,
            origin=origin,
            created_by=actor,
            updated_by=actor,
        )
        db.add(draft)
    else:
        draft.layout = layout
        draft.config = check.config
        draft.resolved = check.resolved
        draft.defaults_version = site_defaults.DEFAULTS_VERSION
        draft.schema_version = site_defaults.SCHEMA_VERSION
        draft.revision = draft.revision + 1
        draft.origin = origin
        draft.based_on_id = based_on_id
        draft.updated_by = actor
        draft.updated_at = datetime.now(UTC)
    await db.flush()
    return draft


@router.put("/{hospital_id}/site-config/draft")
async def save_site_config_draft(
    hospital_id: uuid.UUID,
    body: DraftSave,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    _validate_layout(body.layout)
    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await _hospital_or_404(db, hospital_id)
    draft = await _row(db, hospital_id, SITE_CONFIG_DRAFT, for_update=True)
    _require_draft_match(draft, body, allow_none=True)
    check = validate_site_config(
        body.layout, body.config, await site_config_refs.load_context(db, hospital, body.config)
    )
    _reject_invalid(check)
    published = await _row(db, hospital_id, SITE_CONFIG_PUBLISHED)
    based_on = draft.based_on_id if draft is not None else (published.id if published else None)
    draft = await _write_draft(
        db, hospital, draft, layout=body.layout, check=check, origin=ORIGIN_EDIT, based_on_id=based_on
    )
    await write_audit_log(
        db,
        action="site_config_save",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital_site_config",
        target_id=draft.id,
        detail={
            "draft_id": str(draft.id),
            "revision": draft.revision,
            "layout": draft.layout,
            "changed": changed_sections(published.resolved if published else None, draft.resolved),
        },
    )
    await db.commit()
    await db.refresh(draft)
    return {"draft": serialize_config(draft), "warnings": check.warnings}


def _publish_fingerprint(body: DraftExpectation) -> str:
    return hashlib.sha256(
        json.dumps({"draft_id": str(body.draft_id), "revision": body.revision}, sort_keys=True).encode()
    ).hexdigest()


async def _replayed_publish(
    db: AsyncSession, hospital_id: uuid.UUID, actor: AdminUser, key: str, fingerprint: str
) -> dict[str, Any] | None:
    run = await db.scalar(
        select(OperationRun).where(
            OperationRun.operation_type == PUBLISH_OPERATION_TYPE,
            OperationRun.requested_by_id == actor.id,
            OperationRun.hospital_id == hospital_id,
            OperationRun.idempotency_key == key,
        )
    )
    if run is None:
        return None
    payload = run.request_payload or {}
    if payload.get("request_fingerprint") != fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "같은 요청 키로 다른 초안이 들어왔습니다. 화면을 새로 고친 뒤 다시 공개해 주세요.",
            },
        )
    return {**payload["response"], "idempotent_replay": True}


@router.post("/{hospital_id}/site-config/draft/publish")
async def publish_site_config_draft(
    hospital_id: uuid.UUID,
    body: DraftExpectation,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_active_account),
    idempotency_key: IdempotencyKey = None,
):
    key = normalize_operation_key(idempotency_key)
    if key is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "공개 반영에는 요청 키가 필요합니다."},
        )
    fingerprint = _publish_fingerprint(body)
    await acquire_hospital_advisory_lock(db, hospital_id)
    # 같은 키의 재요청은 초안이 이미 공개 판이 됐으므로 전제조건보다 먼저 영수증을 본다.
    replay = await _replayed_publish(db, hospital_id, actor, key, fingerprint)
    if replay is not None:
        return replay
    hospital = await _hospital_or_404(db, hospital_id)
    draft = await _row(db, hospital_id, SITE_CONFIG_DRAFT, for_update=True)
    _require_draft_match(draft, body, allow_none=False)
    assert draft is not None
    # 저장 뒤 사진이 비공개가 됐거나 FAQ 글이 내려갔을 수 있다 — 지금 사실로 다시 검증한다.
    check = validate_site_config(
        draft.layout, draft.config or {}, await site_config_refs.load_context(db, hospital, draft.config)
    )
    _reject_invalid(check)
    is_public = profile_publication_verdict(hospital).allowed
    if is_public:
        ensure_site_revalidate_configured()

    previous = await _row(db, hospital_id, SITE_CONFIG_PUBLISHED, for_update=True)
    if previous is not None:
        previous.state = SITE_CONFIG_SUPERSEDED
        await db.flush()
    last_version = await db.scalar(
        select(func.max(HospitalSiteConfig.version)).where(HospitalSiteConfig.hospital_id == hospital_id)
    )
    now = datetime.now(UTC)
    draft.state = SITE_CONFIG_PUBLISHED
    draft.version = int(last_version or 0) + 1
    draft.config = check.config
    draft.resolved = check.resolved
    draft.defaults_version = site_defaults.DEFAULTS_VERSION
    draft.published_by = default_actor()
    draft.published_at = now
    draft.change_summary = changed_sections(previous.resolved if previous else None, check.resolved or {})
    layout_before = hospital.site_layout
    hospital.site_layout = draft.layout
    if is_public:
        enqueue_public_surface_intent(db, hospital)
    await write_audit_log(
        db,
        action="site_config_publish",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital_site_config",
        target_id=draft.id,
        detail={
            "version": draft.version,
            "superseded_id": str(previous.id) if previous else None,
            "layout_before": layout_before,
            "layout_after": draft.layout,
            "changed": draft.change_summary,
            "revalidate": is_public,
        },
    )
    await db.flush()
    # 서버가 채운 시각(updated_at)을 읽어 둔다 — 영수증의 응답이 커밋된 행과 같아야 한다.
    await db.refresh(draft)
    response = {
        "published": serialize_config(draft),
        "warnings": check.warnings,
        "revalidate_requested": is_public,
    }
    db.add(
        OperationRun(
            operation_type=PUBLISH_OPERATION_TYPE,
            hospital_id=hospital_id,
            state=OperationRunState.SUCCEEDED.value,
            idempotency_key=key,
            requested_by_id=actor.id,
            request_payload={
                "source_id": str(draft.id),
                "request_fingerprint": fingerprint,
                "response": response,
            },
            completed_at=now,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # 같은 키의 요청이 동시에 먼저 커밋했다(잠금을 기다린 사이). 이 요청은 되돌린다.
        await db.rollback()
        replay = await _replayed_publish(db, hospital_id, actor, key, fingerprint)
        if replay is None:
            raise
        return replay
    if is_public:
        # 커밋 뒤라 실패해도 되돌리지 않는다 — intent가 남아 재검증 조정이 다시 보낸다.
        await trigger_hospital_site_revalidate_safe(hospital.slug, hospital.treatments, hospital_name=hospital.name)
    return {**response, "idempotent_replay": False}


@router.delete("/{hospital_id}/site-config/draft")
async def discard_site_config_draft(
    hospital_id: uuid.UUID,
    draft_id: uuid.UUID = Query(...),
    revision: int = Query(...),
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    await acquire_hospital_advisory_lock(db, hospital_id)
    await _hospital_or_404(db, hospital_id)
    draft = await _row(db, hospital_id, SITE_CONFIG_DRAFT, for_update=True)
    _require_draft_match(draft, DraftExpectation(draft_id=draft_id, revision=revision), allow_none=False)
    assert draft is not None
    await write_audit_log(
        db,
        action="site_config_discard",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="hospital_site_config",
        target_id=draft.id,
        detail={"draft_id": str(draft.id), "revision": draft.revision, "layout": draft.layout},
    )
    await db.delete(draft)
    await db.commit()
    return {"draft": None}


async def _replace_draft(
    db: AsyncSession,
    hospital: Hospital,
    expected: DraftExpectation,
    *,
    layout: str,
    config: dict[str, Any],
    origin: str,
    based_on_id: uuid.UUID | None,
    audit_action: str,
    audit_detail: dict[str, Any],
) -> dict[str, Any]:
    draft = await _row(db, hospital.id, SITE_CONFIG_DRAFT, for_update=True)
    _require_draft_match(draft, expected, allow_none=True)
    check = validate_site_config(layout, config, await site_config_refs.load_context(db, hospital, config))
    _reject_invalid(check)
    draft = await _write_draft(
        db, hospital, draft, layout=layout, check=check, origin=origin, based_on_id=based_on_id
    )
    await write_audit_log(
        db,
        action=audit_action,
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="hospital_site_config",
        target_id=draft.id,
        detail={"draft_id": str(draft.id), "revision": draft.revision, "layout": layout, **audit_detail},
    )
    await db.commit()
    await db.refresh(draft)
    return {"draft": serialize_config(draft), "warnings": check.warnings}


@router.post("/{hospital_id}/site-config/versions/{version_id}/restore")
async def restore_site_config_version(
    hospital_id: uuid.UUID,
    version_id: uuid.UUID,
    body: DraftExpectation,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    """그 판의 레이아웃·설정으로 새 초안을 만든다. 공개는 공개 반영을 따로 거친다."""

    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await _hospital_or_404(db, hospital_id)
    source = await db.get(HospitalSiteConfig, version_id)
    if (
        source is None
        or source.hospital_id != hospital_id
        or source.state not in {SITE_CONFIG_PUBLISHED, SITE_CONFIG_SUPERSEDED}
    ):
        raise HTTPException(status_code=404, detail="복원할 판을 찾을 수 없습니다.")
    result = await _replace_draft(
        db,
        hospital,
        body,
        layout=source.layout,
        config=dict(source.config or {}),
        origin=ORIGIN_RESTORE,
        based_on_id=source.id,
        audit_action="site_config_restore",
        audit_detail={"restored_id": str(source.id), "restored_version": source.version},
    )
    if source.defaults_version != site_defaults.DEFAULTS_VERSION:
        # 옛 판은 그때 기본값과 합친 완성본이었다. 복원은 덮어쓰기 원본을 지금 기본값에 다시 합친다.
        result["warnings"] = [
            *result["warnings"],
            {
                "path": "defaults_version",
                "code": "DEFAULTS_CHANGED",
                "message": "기본 설정이 그 판 이후 바뀌어, 직접 바꾸지 않은 칸은 지금 기본값으로 들어갑니다.",
                "changed": changed_sections(source.resolved or {}, result["draft"]["resolved"]),
            },
        ]
    return result


@router.post("/{hospital_id}/site-config/reset")
async def reset_site_config(
    hospital_id: uuid.UUID,
    body: DraftReset,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    """기본 세팅(설정 비움)으로 새 초안. 레이아웃 바꾸기도 여기서 한다."""

    _validate_layout(body.layout)
    await acquire_hospital_advisory_lock(db, hospital_id)
    hospital = await _hospital_or_404(db, hospital_id)
    published = await _row(db, hospital_id, SITE_CONFIG_PUBLISHED)
    return await _replace_draft(
        db,
        hospital,
        body,
        layout=body.layout,
        config={},
        origin=ORIGIN_RESET,
        based_on_id=published.id if published else None,
        audit_action="site_config_reset",
        audit_detail={},
    )


@router.post("/{hospital_id}/site-config/preview-token")
async def issue_site_config_preview_token(
    hospital_id: uuid.UUID,
    body: DraftExpectation,
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    if not preview_enabled():
        raise HTTPException(
            status_code=503,
            detail={"code": "SITE_PREVIEW_DISABLED", "message": "미리보기가 아직 설정되지 않았습니다."},
        )
    await _hospital_or_404(db, hospital_id)
    draft = await _row(db, hospital_id, SITE_CONFIG_DRAFT)
    _require_draft_match(draft, body, allow_none=False)
    assert draft is not None
    token, expires_at = issue_preview_token(hospital_id, draft.id, draft.revision)
    return {
        "token": token,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "preview_path": f"/preview/{token}",
        "draft_id": str(draft.id),
        "revision": draft.revision,
    }
