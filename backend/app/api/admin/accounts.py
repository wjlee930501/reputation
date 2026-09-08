"""Admin API — 운영자 계정 관리.

이 라우터가 존재하는 이유: 계정 생성 경로가 `python -m app.utils.admin_user create-owner`
CLI뿐이라, AE가 한 명 늘 때마다 개발자가 셸에 들어가야 했다.

인가 모델 — 이 OWNER 검사가 **무엇을 막고 무엇을 못 막는지** 분명히 해 둔다.

쓰기 요청의 행위자는 `core/security.resolve_request_actor`가 결정한다. 시크릿이 설정된
환경에서 사람의 변경은 BFF가 서명한 단언을 요구하므로, 여기서 보는 이메일은 `X-Admin-Key`만
아는 쪽이 마음대로 붙일 수 있는 평문 헤더가 아니라 **서명으로 확인된 신원**이다. 평문
`X-Admin-Actor`는 인가에 쓰지 않으며, 단언과 값이 다르면 요청 자체가 거부된다.
시스템 호출(`X-Admin-Actor-System`)에는 사람 계정이 없으므로 이 라우터를 쓸 수 없다.

그래도 남는 한계: 공유 `X-Admin-Key`는 병원·콘텐츠·발행 등 다른 Admin API의 경계이고,
BFF 서명 키(`BFF_ACTOR_SECRET`)가 함께 새면 여기 OWNER 검사도 우회된다. 두 키의 보호
(Secret Manager·유출 대응)는 여전히 필수다.

조회는 활성 계정이면 누구나 가능하다(팀 명부 성격). `ADMIN_REJECT_UNVERIFIED_ACTOR`
설정과 무관하게 여기서는 항상 actor를 검증한다 — 그 플래그는 전역 기본 정책이고,
계정 관리는 그보다 강한 기본값이 필요하다.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import SYSTEM_ACTOR_NOT_ALLOWED_DETAIL, resolve_request_actor
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.schemas.admin_account import (
    AdminAccountCreateRequest,
    AdminAccountPasswordRequest,
    AdminAccountResponse,
    AdminAccountUpdateRequest,
)
from app.services.admin_passwords import hash_admin_password
from app.services.audit_log import write_audit_log

router = APIRouter(prefix="/admin/accounts", tags=["Admin — Accounts"])

_TARGET_TYPE = "admin_account"


async def _resolve_actor_account(db: AsyncSession, email: str | None) -> AdminUser | None:
    """확인된 actor 이메일이 가리키는 활성 운영자 계정을 조회한다."""
    raw = (email or "").strip()
    if not raw or "@" not in raw:
        return None
    result = await db.execute(
        select(AdminUser).where(
            func.lower(AdminUser.email) == raw.lower(),
            AdminUser.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def require_active_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """인가에 쓸 운영자 계정은 **확인된 actor**에서만 온다.

    시스템 호출에는 사람 계정이 없다 — 배치 job 이름으로 사람 권한을 빌릴 수 없어야 하므로
    여기서 끊는다(403). 배치가 이 경로를 정말 필요로 하면 규칙을 낮추는 대신 전용 경로를 만든다.
    """
    effective = resolve_request_actor(request)
    if effective.system_job:
        raise HTTPException(status_code=403, detail=SYSTEM_ACTOR_NOT_ALLOWED_DETAIL)
    account = await _resolve_actor_account(db, effective.email)
    if account is None:
        raise HTTPException(
            status_code=403,
            detail="운영자 계정을 확인할 수 없습니다. 다시 로그인한 뒤 시도해 주세요.",
        )
    return account


async def require_owner_account(
    account: AdminUser = Depends(require_active_account),
) -> AdminUser:
    if account.role != ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="계정 관리는 소유자(OWNER) 권한이 있는 운영자만 할 수 있습니다.",
        )
    return account


async def _get_account_or_404(db: AsyncSession, account_id: uuid.UUID) -> AdminUser:
    account = await db.get(AdminUser, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="운영자 계정을 찾을 수 없습니다.")
    return account


async def _other_active_owner_exists(db: AsyncSession, exclude_id: uuid.UUID) -> bool:
    """대상 계정을 뺀 나머지에 활성 OWNER가 남아 있는지.

    OWNER 행 **전체**를 id 순서로 잠그고 센다. 잠그지 않으면 소유자 두 명이 서로를
    동시에 강등할 때(READ COMMITTED에서 각자 상대의 미커밋 상태를 못 본다) 양쪽 다
    "다른 소유자가 있다"고 판정하고 커밋해 활성 소유자가 0명이 된다.
    같은 집합을 같은 순서로 잠그므로 두 번째 트랜잭션이 대기했다가 갱신된 상태를 본다.
    """
    locked = await db.execute(
        select(AdminUser.id, AdminUser.is_active)
        .where(AdminUser.role == ROLE_OWNER)
        .order_by(AdminUser.id)
        .with_for_update()
    )
    return any(
        is_active and owner_id != exclude_id for owner_id, is_active in locked.all()
    )


def _hash_or_422(password: str) -> str:
    try:
        return hash_admin_password(password)
    except ValueError as exc:
        # admin_passwords가 길이 하한을 강제한다 — 사유를 그대로 사용자에게 전달한다.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[AdminAccountResponse])
async def list_admin_accounts(
    db: AsyncSession = Depends(get_db),
    _actor: AdminUser = Depends(require_active_account),
):
    """운영자 명부 — 비활성 계정도 포함해 퇴사·정지 상태를 화면에서 볼 수 있게 한다."""
    result = await db.execute(select(AdminUser).order_by(AdminUser.created_at.asc()))
    return list(result.scalars().all())


@router.post("", response_model=AdminAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_admin_account(
    body: AdminAccountCreateRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    existing = await db.execute(select(AdminUser.id).where(AdminUser.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다.")

    account = AdminUser(
        email=body.email,
        name=body.name,
        role=body.role,
        password_hash=_hash_or_422(body.password),
        is_active=True,
    )
    db.add(account)
    await db.flush()

    await write_audit_log(
        db,
        action="admin_account_created",
        actor=actor.email,
        target_type=_TARGET_TYPE,
        target_id=account.id,
        detail={"email": account.email, "role": account.role},
    )
    await db.commit()
    await db.refresh(account)
    return account


@router.patch("/{account_id}", response_model=AdminAccountResponse)
async def update_admin_account(
    account_id: uuid.UUID,
    body: AdminAccountUpdateRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    account = await _get_account_or_404(db, account_id)

    demoting = body.role is not None and body.role != ROLE_OWNER
    promoting_inactive = (
        body.role == ROLE_OWNER and account.role != ROLE_OWNER and not account.is_active
    )
    deactivating = body.is_active is False
    if promoting_inactive:
        raise HTTPException(
            status_code=409,
            detail="정지된 계정은 소유자로 승격할 수 없습니다. 먼저 계정을 활성화해 주세요.",
        )
    # 자기 자신의 권한을 낮추거나 스스로를 잠그면 복구 경로가 CLI밖에 남지 않는다.
    if account.id == actor.id and (demoting or deactivating):
        raise HTTPException(
            status_code=400,
            detail="자기 계정의 권한을 낮추거나 비활성화할 수 없습니다. 다른 소유자에게 요청해 주세요.",
        )
    # 마지막 활성 OWNER를 잃으면 아무도 계정을 관리할 수 없게 된다.
    if account.role == ROLE_OWNER and (demoting or deactivating):
        if not await _other_active_owner_exists(db, account.id):
            raise HTTPException(
                status_code=400,
                detail="마지막 소유자 계정입니다. 다른 소유자를 먼저 지정해 주세요.",
            )

    changes: dict[str, object] = {}
    if body.name is not None and body.name != account.name:
        changes["name"] = body.name
        account.name = body.name
    if body.role is not None and body.role != account.role:
        changes["role"] = body.role
        account.role = body.role
    if body.is_active is not None and body.is_active != account.is_active:
        changes["is_active"] = body.is_active
        account.is_active = body.is_active
        if body.is_active:
            # 정지 기간에는 세션이 폐기로 판정되지만, 재활성화하면 그 판정이 풀려
            # 정지 전에 발급된 세션이 되살아난다 — 기준선을 올려 확실히 끊는다.
            account.sessions_invalid_before = datetime.now(UTC)
            changes["sessions_revoked"] = True

    if changes:
        await write_audit_log(
            db,
            action="admin_account_updated",
            actor=actor.email,
            target_type=_TARGET_TYPE,
            target_id=account.id,
            detail={"email": account.email, "changes": changes},
        )
        await db.commit()
        await db.refresh(account)
    return account


@router.post("/{account_id}/password", response_model=AdminAccountResponse)
async def reset_admin_account_password(
    account_id: uuid.UUID,
    body: AdminAccountPasswordRequest,
    db: AsyncSession = Depends(get_db),
    actor: AdminUser = Depends(require_owner_account),
):
    """비밀번호 재설정 — 값은 감사 로그에 남기지 않는다.

    비밀번호를 바꾸는 이유는 대개 탈취다. 기존 세션을 그대로 두면 공격자의 쿠키가
    만료(최대 7일)까지 계속 통하므로, 재설정과 동시에 이 계정의 모든 세션을 무효화한다.
    """
    account = await _get_account_or_404(db, account_id)
    account.password_hash = _hash_or_422(body.password)
    account.sessions_invalid_before = datetime.now(UTC)

    await write_audit_log(
        db,
        action="admin_account_password_reset",
        actor=actor.email,
        target_type=_TARGET_TYPE,
        target_id=account.id,
        detail={"email": account.email},
    )
    await db.commit()
    await db.refresh(account)
    return account
