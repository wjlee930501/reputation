"""운영자 계정 관리 — 실제 Postgres로 검증.

가드(마지막 소유자 보호, 자기 잠금 방지, 이메일 중복)는 전부 DB 상태에 대한 질의로
판정하므로, 모의 세션으로는 "질의를 호출했다"까지밖에 확인할 수 없다. 여기서는 실제
행을 넣고 엔드포인트를 호출해 **상태가 정말 바뀌었는지 / 바뀌지 않았는지**를 본다.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.api.admin.accounts import (
    create_admin_account,
    list_admin_accounts,
    require_active_account,
    require_owner_account,
    reset_admin_account_password,
    update_admin_account,
)
from app.api.admin.auth import get_admin_session_revocation
from app.models.admin_user import ROLE_OPERATOR, ROLE_OWNER, AdminUser
from app.schemas.admin_account import (
    AdminAccountCreateRequest,
    AdminAccountPasswordRequest,
    AdminAccountResponse,
    AdminAccountUpdateRequest,
)
from app.services.admin_passwords import verify_admin_password

pytestmark = pytest.mark.asyncio

VALID_PASSWORD = "correct horse battery staple"


class StubRequest:
    """엔드포인트가 헤더만 읽으므로 Request 전체를 세울 필요가 없다."""

    def __init__(self, actor: str | None):
        self.headers = {"X-Admin-Actor": actor} if actor is not None else {}


async def _seed_account(
    db,
    *,
    email: str | None = None,
    role: str = ROLE_OWNER,
    is_active: bool = True,
) -> AdminUser:
    account = AdminUser(
        email=email or f"{uuid.uuid4().hex}@example.com",
        name="Seed",
        role=role,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",  # 검증 대상 아님 — 로그인 경로가 아니다
        is_active=is_active,
    )
    db.add(account)
    await db.flush()
    return account


async def _owner_actor(db, account: AdminUser) -> AdminUser:
    resolved = await require_active_account(StubRequest(account.email), db)
    return await require_owner_account(resolved)


async def test_create_account_persists_and_hides_password(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    actor = await _owner_actor(db, owner)
    email = f"{uuid.uuid4().hex}@example.com"

    created = await create_admin_account(
        AdminAccountCreateRequest(
            email=email.upper(),  # 정규화 확인
            name="  새 운영자  ",
            role=ROLE_OPERATOR,
            password=VALID_PASSWORD,
        ),
        db,
        actor,
    )

    assert created.email == email
    assert created.name == "새 운영자"
    assert created.role == ROLE_OPERATOR
    assert created.is_active is True
    # 직렬화는 응답 모델이 한다 — 그 모델에 password_hash 자리가 없어야 유출이 구조적으로 막힌다.
    assert "password_hash" not in AdminAccountResponse.model_fields

    stored = (
        await db.execute(select(AdminUser).where(AdminUser.email == email))
    ).scalar_one()
    assert verify_admin_password(VALID_PASSWORD, stored.password_hash)


async def test_create_account_rejects_duplicate_email(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    actor = await _owner_actor(db, owner)

    with pytest.raises(HTTPException) as exc:
        await create_admin_account(
            AdminAccountCreateRequest(
                email=owner.email, name="중복", role=ROLE_OPERATOR, password=VALID_PASSWORD
            ),
            db,
            actor,
        )

    assert exc.value.status_code == 409


async def test_operator_cannot_manage_accounts(pg_async_session):
    db = pg_async_session
    operator = await _seed_account(db, role=ROLE_OPERATOR)

    resolved = await require_active_account(StubRequest(operator.email), db)
    with pytest.raises(HTTPException) as exc:
        await require_owner_account(resolved)

    assert exc.value.status_code == 403


@pytest.mark.parametrize("actor_header", [None, "", "not-an-email", "ghost@example.com"])
async def test_unresolvable_actor_is_rejected(pg_async_session, actor_header):
    db = pg_async_session

    with pytest.raises(HTTPException) as exc:
        await require_active_account(StubRequest(actor_header), db)

    assert exc.value.status_code == 403


async def test_deactivated_account_cannot_act(pg_async_session):
    db = pg_async_session
    disabled = await _seed_account(db, is_active=False)

    with pytest.raises(HTTPException) as exc:
        await require_active_account(StubRequest(disabled.email), db)

    assert exc.value.status_code == 403


async def test_cannot_demote_or_deactivate_self(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    # 다른 소유자가 있어도 자기 잠금은 막는다 — 마지막 소유자 규칙과 독립이다.
    await _seed_account(db)
    actor = await _owner_actor(db, owner)

    for payload in (
        AdminAccountUpdateRequest(role=ROLE_OPERATOR),
        AdminAccountUpdateRequest(is_active=False),
    ):
        with pytest.raises(HTTPException) as exc:
            await update_admin_account(owner.id, payload, db, actor)
        assert exc.value.status_code == 400

    await db.refresh(owner)
    assert owner.role == ROLE_OWNER
    assert owner.is_active is True


async def test_cannot_remove_last_active_owner(pg_async_session):
    db = pg_async_session
    acting_owner = await _seed_account(db)
    target_owner = await _seed_account(db)
    actor = await _owner_actor(db, acting_owner)

    # acting_owner를 먼저 비활성화하면 target_owner가 유일한 활성 소유자가 된다.
    acting_owner.is_active = False
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await update_admin_account(
            target_owner.id, AdminAccountUpdateRequest(is_active=False), db, actor
        )

    assert exc.value.status_code == 400
    await db.refresh(target_owner)
    assert target_owner.is_active is True


async def test_can_deactivate_owner_when_another_active_owner_remains(pg_async_session):
    db = pg_async_session
    acting_owner = await _seed_account(db)
    spare_owner = await _seed_account(db)
    target_owner = await _seed_account(db)
    actor = await _owner_actor(db, acting_owner)
    assert spare_owner.is_active is True

    updated = await update_admin_account(
        target_owner.id, AdminAccountUpdateRequest(is_active=False), db, actor
    )

    assert updated.is_active is False


async def test_update_unknown_account_returns_404(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    actor = await _owner_actor(db, owner)

    with pytest.raises(HTTPException) as exc:
        await update_admin_account(
            uuid.uuid4(), AdminAccountUpdateRequest(name="없음"), db, actor
        )

    assert exc.value.status_code == 404


async def test_password_reset_changes_stored_hash(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    target = await _seed_account(db, role=ROLE_OPERATOR)
    actor = await _owner_actor(db, owner)
    new_password = "another correct horse staple"

    await reset_admin_account_password(
        target.id, AdminAccountPasswordRequest(password=new_password), db, actor
    )

    stored = (await db.execute(select(AdminUser).where(AdminUser.id == target.id))).scalar_one()
    assert verify_admin_password(new_password, stored.password_hash)


async def test_list_includes_inactive_accounts(pg_async_session):
    db = pg_async_session
    owner = await _seed_account(db)
    disabled = await _seed_account(db, is_active=False)
    actor = await _owner_actor(db, owner)

    listed = await list_admin_accounts(db, actor)

    ids = {account.id for account in listed}
    assert owner.id in ids
    assert disabled.id in ids


@pytest.fixture
def unrevoked_hash(monkeypatch):
    """해시 폐기 저장소(Redis)를 '폐기 안 됨'으로 고정 — 여기서 보려는 축은 계정 상태다."""

    async def _not_revoked(_token_hash: str) -> bool:
        return False

    monkeypatch.setattr(
        "app.api.admin.auth.is_admin_session_hash_revoked", _not_revoked
    )


async def test_revocation_reports_revoked_for_inactive_account(
    pg_async_session, unrevoked_hash
):
    """비활성화한 계정의 세션은 만료 전이라도 즉시 끊겨야 한다."""
    db = pg_async_session
    account = await _seed_account(db, is_active=False)

    result = await get_admin_session_revocation("0" * 64, account_id=account.id, db=db)

    assert result.revoked is True


async def test_revocation_reports_active_for_live_account(pg_async_session, unrevoked_hash):
    db = pg_async_session
    account = await _seed_account(db)

    result = await get_admin_session_revocation("0" * 64, account_id=account.id, db=db)

    assert result.revoked is False


async def test_revocation_reports_revoked_for_missing_account(
    pg_async_session, unrevoked_hash
):
    db = pg_async_session

    result = await get_admin_session_revocation("0" * 64, account_id=uuid.uuid4(), db=db)

    assert result.revoked is True


async def test_revocation_without_account_id_keeps_previous_behaviour(
    pg_async_session, unrevoked_hash
):
    """account_id를 안 보내는 호출자(로그아웃 등)는 종전대로 해시만 본다."""
    db = pg_async_session

    result = await get_admin_session_revocation("0" * 64, account_id=None, db=db)

    assert result.revoked is False


async def test_concurrent_demotions_cannot_remove_every_active_owner(pg_engine, monkeypatch):
    """소유자 둘이 서로를 동시에 강등해도 활성 소유자가 0명이 되면 안 된다.

    단일 요청 경로에서는 '마지막 소유자' 가드가 사실상 발동하지 않는다 — 호출자 자신이
    활성 소유자이고 자기 계정은 건드릴 수 없기 때문이다. 그래서 소유자를 전부 잃는
    경로는 **동시 요청**뿐이고, 그것이 이 테스트가 보는 유일한 실제 시나리오다.
    두 트랜잭션이 각자 상대의 미커밋 상태를 못 보면 양쪽 다 통과해 콘솔이 잠긴다.

    경쟁을 우연에 맡기면 안 된다 — 그냥 asyncio.gather로 띄우면 두 트랜잭션이 순서대로
    끝나버려 잠금이 없어도 테스트가 통과한다(실제로 그랬다). 그래서 가드 통과 직후
    커밋 직전 지점(write_audit_log)에 랑데부를 심어, **둘 다 가드를 통과한 뒤에야**
    커밋하도록 강제한다. 잠금이 있으면 두 번째 트랜잭션은 가드에서 멈춰 랑데부에
    도착하지 못하므로, 첫 번째가 타임아웃으로 풀려 커밋하고 두 번째는 갱신된 상태를 본다.
    """
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.api.admin import accounts as accounts_module

    real_write_audit_log = accounts_module.write_audit_log
    reached = asyncio.Event()
    arrivals = {"n": 0}

    async def rendezvous_write_audit_log(*args, **kwargs):
        arrivals["n"] += 1
        if arrivals["n"] >= 2:
            reached.set()
        else:
            # 상대가 오지 않으면(=잠금에 막혀 있으면) 풀어 준다.
            try:
                await asyncio.wait_for(reached.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        return await real_write_audit_log(*args, **kwargs)

    monkeypatch.setattr(accounts_module, "write_audit_log", rendezvous_write_audit_log)

    url = os.getenv("INTEGRATION_DATABASE_URL") or (
        "postgresql://reputation:reputation@localhost:5434/reputation_test"
    )
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break

    engine = create_async_engine(url)
    # id를 미리 정한다 — 커밋 뒤에 읽으면 시드 도중 예외가 났을 때 정리 대상을 잃고,
    # 커밋된 활성 OWNER 행이 테스트 DB에 남아 다른 테스트의 소유자 수 판정을 깨뜨린다.
    owner_ids: list[uuid.UUID] = [uuid.uuid4(), uuid.uuid4()]
    try:
        # 두 연결이 서로를 볼 수 있어야 하므로 시드는 커밋한다.
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            setup.add_all(
                [
                    AdminUser(
                        id=owner_id,
                        email=f"{uuid.uuid4().hex}@example.com",
                        name=f"Owner {i}",
                        role=ROLE_OWNER,
                        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
                        is_active=True,
                    )
                    for i, owner_id in enumerate(owner_ids)
                ]
            )
            await setup.commit()

        async def demote(actor_id: uuid.UUID, target_id: uuid.UUID) -> bool:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                actor = await session.get(AdminUser, actor_id)
                try:
                    await update_admin_account(
                        target_id, AdminAccountUpdateRequest(role=ROLE_OPERATOR), session, actor
                    )
                    return True
                except HTTPException:
                    return False

        # 서로를 동시에 강등한다.
        await asyncio.gather(
            demote(owner_ids[0], owner_ids[1]),
            demote(owner_ids[1], owner_ids[0]),
            return_exceptions=True,
        )

        async with AsyncSession(engine, expire_on_commit=False) as check:
            remaining = (
                await check.execute(
                    select(func.count())
                    .select_from(AdminUser)
                    .where(
                        AdminUser.id.in_(owner_ids),
                        AdminUser.role == ROLE_OWNER,
                        AdminUser.is_active.is_(True),
                    )
                )
            ).scalar_one()

        assert remaining >= 1, "동시 강등으로 활성 소유자가 전부 사라졌다 — 콘솔이 잠긴다"
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as cleanup:
            await cleanup.execute(delete(AdminUser).where(AdminUser.id.in_(owner_ids)))
            await cleanup.commit()
        await engine.dispose()


# ── 세션 무효화 기준선 ────────────────────────────────────────────────
# 발급된 토큰은 서버가 열거할 수 없어 개별 폐기가 불가능하다. 계정 단위 기준선이
# 실제로 "그 이전 발급분"만 끊는지, 그리고 되살아나지 않는지를 본다.


async def test_password_reset_revokes_sessions_issued_before_it(
    pg_async_session, unrevoked_hash
):
    from datetime import UTC, datetime, timedelta

    db = pg_async_session
    owner = await _seed_account(db)
    target = await _seed_account(db, role=ROLE_OPERATOR)
    actor = await _owner_actor(db, owner)
    before = datetime.now(UTC) - timedelta(minutes=5)

    await reset_admin_account_password(
        target.id, AdminAccountPasswordRequest(password="a brand new passphrase"), db, actor
    )

    stale = await get_admin_session_revocation(
        "0" * 64, account_id=target.id, issued_at=before, db=db
    )
    fresh = await get_admin_session_revocation(
        "0" * 64,
        account_id=target.id,
        issued_at=datetime.now(UTC) + timedelta(minutes=1),
        db=db,
    )

    assert stale.revoked is True, "재설정 이전에 발급된 세션은 끊겨야 한다"
    assert fresh.revoked is False, "재설정 이후 로그인한 세션까지 끊으면 안 된다"


async def test_reactivating_an_account_does_not_revive_old_sessions(
    pg_async_session, unrevoked_hash
):
    from datetime import UTC, datetime, timedelta

    db = pg_async_session
    owner = await _seed_account(db)
    target = await _seed_account(db, role=ROLE_OPERATOR, is_active=False)
    actor = await _owner_actor(db, owner)
    issued_before_suspension = datetime.now(UTC) - timedelta(days=1)

    await update_admin_account(
        target.id, AdminAccountUpdateRequest(is_active=True), db, actor
    )

    result = await get_admin_session_revocation(
        "0" * 64, account_id=target.id, issued_at=issued_before_suspension, db=db
    )

    assert result.revoked is True


async def test_session_without_issue_time_is_revoked_once_a_baseline_exists(
    pg_async_session, unrevoked_hash
):
    """발급 시각을 모르는 구버전 토큰은 기준선 이후로 볼 근거가 없다 — 재로그인시킨다."""
    db = pg_async_session
    owner = await _seed_account(db)
    target = await _seed_account(db, role=ROLE_OPERATOR)
    actor = await _owner_actor(db, owner)

    no_baseline = await get_admin_session_revocation(
        "0" * 64, account_id=target.id, issued_at=None, db=db
    )
    assert no_baseline.revoked is False, "기준선이 없으면 배포만으로 로그아웃시키지 않는다"

    await reset_admin_account_password(
        target.id, AdminAccountPasswordRequest(password="a brand new passphrase"), db, actor
    )

    after_baseline = await get_admin_session_revocation(
        "0" * 64, account_id=target.id, issued_at=None, db=db
    )
    assert after_baseline.revoked is True
