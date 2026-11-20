"""섹션 설정(레이아웃 편집기) 3a — 실제 Postgres에서 초안 저장 → 미리보기 → 공개 반영 → 이력 → 복원·초기화."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.admin import site_config as admin_site_config
from app.api.admin.accounts import require_active_account
from app.api.public import site as public_site
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import AdminUser
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.models.site_config import HospitalSiteConfig
from tests.db_env import fail_unreachable, require_db_url

_URL_ENV = "DATABASE_URL"
ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine(require_db_url(_URL_ENV))
    try:
        connection = await engine.connect()
    except OSError as exc:
        await engine.dispose()
        fail_unreachable(_URL_ENV, exc)
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
async def account(db: AsyncSession) -> AdminUser:
    user = AdminUser(
        email=f"site-config-{uuid.uuid4().hex[:8]}@example.test",
        name="편집기 검증",
        role="OWNER",
        password_hash="x",
    )
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def client(db: AsyncSession, account: AdminUser, monkeypatch):
    revalidated: list[str] = []

    async def record_revalidate(slug, treatments=None, *, hospital_name=None):
        revalidated.append(slug)
        return True

    monkeypatch.setattr(admin_site_config, "trigger_hospital_site_revalidate_safe", record_revalidate)

    async def override_get_db():
        yield db

    async def override_account():
        return account

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_active_account] = override_account
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            http.revalidated = revalidated  # type: ignore[attr-defined]
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_active_account, None)
        app.state.limiter = previous_limiter


async def _hospital(db: AsyncSession, *, layout: str | None = None) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, site_layout, hero_headline) "
            "VALUES (:id, :name, :slug, :layout, :headline)"
        ),
        {
            "id": hospital_id,
            "name": "편집기 검증 정형외과의원",
            "slug": f"site-config-{hospital_id.hex[:12]}",
            "layout": layout,
            "headline": "편집기 검증 정형외과의원 동네 정형외과 진료",
        },
    )
    await db.commit()
    return hospital_id


def _base(hospital_id: uuid.UUID) -> str:
    return f"/api/v1/admin/hospitals/{hospital_id}/site-config"


async def _save(client, hospital_id, *, layout="B", config=None, draft=None):
    body = {"layout": layout, "config": config or {}}
    if draft is not None:
        body.update({"draft_id": draft["id"], "revision": draft["revision"]})
    return await client.put(f"{_base(hospital_id)}/draft", headers=ADMIN, json=body)


async def _publish(client, hospital_id, draft, *, key=None):
    headers = {**ADMIN, "Idempotency-Key": key or uuid.uuid4().hex}
    return await client.post(
        f"{_base(hospital_id)}/draft/publish",
        headers=headers,
        json={"draft_id": draft["id"], "revision": draft["revision"]},
    )


async def _count_runs(db: AsyncSession, hospital_id: uuid.UUID, operation_type: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(OperationRun)
            .where(OperationRun.hospital_id == hospital_id, OperationRun.operation_type == operation_type)
        )
        or 0
    )


async def test_draft_save_preview_publish_restore_reset_and_discard(db, client, monkeypatch):
    hospital_id = await _hospital(db)

    empty = (await client.get(_base(hospital_id), headers=ADMIN)).json()
    assert empty["published"] is None and empty["draft"] is None and empty["history"] == []
    assert set(empty["defaults"]) == {"A", "B", "C"}

    # 초안 저장 — 없을 때는 전제조건 없이, 있으면 {draft_id, revision}이 맞아야 한다.
    first = await _save(client, hospital_id, config={"sections": [{"id": "faq", "enabled": False}]})
    assert first.status_code == 200, first.text
    draft = first.json()["draft"]
    assert draft["revision"] == 1 and draft["state"] == "DRAFT" and draft["version"] is None
    stale = await _save(client, hospital_id, config={}, draft={**draft, "revision": 9})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DRAFT_CHANGED"
    assert stale.json()["detail"]["draft"]["revision"] == 1
    missing = await _save(client, hospital_id, config={})
    assert missing.status_code == 409
    second = await _save(
        client,
        hospital_id,
        config={
            "sections": [{"id": "faq", "enabled": False}],
            "hero": {"line_breaks": {"hero.headline": {"pc": "편집기 검증 정형외과의원\n동네 정형외과 진료"}}},
        },
        draft=draft,
    )
    assert second.status_code == 200, second.text
    draft = second.json()["draft"]
    assert draft["revision"] == 2

    # 검증 실패는 422와 칸 경로·코드.
    invalid = await _save(
        client, hospital_id, config={"sections": [{"id": "doctor", "enabled": False}]}, draft=draft
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["errors"][0]["code"] == "FIXED_SECTION"
    forbidden = await _save(
        client, hospital_id, config={"copy": {"faq.note": "최고의 진료"}}, draft=draft
    )
    assert forbidden.status_code == 422
    assert forbidden.json()["detail"]["errors"][0]["words"] == ["최고"]

    # 초안 저장은 화면에 영향이 없다 — 재검증 intent·병원 레이아웃 변화 없음.
    assert await _count_runs(db, hospital_id, "SITE_REVALIDATION") == 0
    hospital = await db.get(Hospital, hospital_id)
    await db.refresh(hospital)
    assert hospital.site_layout is None

    # 미리보기 — 비밀값이 없으면 503(fail-closed), 있으면 초안·revision에 묶인 토큰.
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "")
    disabled = await client.post(
        f"{_base(hospital_id)}/preview-token", headers=ADMIN, json={"draft_id": draft["id"], "revision": 2}
    )
    assert disabled.status_code == 503
    monkeypatch.setattr(settings, "SITE_PREVIEW_TOKEN_SECRET", "preview-secret")
    issued = await client.post(
        f"{_base(hospital_id)}/preview-token", headers=ADMIN, json={"draft_id": draft["id"], "revision": 2}
    )
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    preview = await client.get(f"/api/v1/public/site-preview/{token}")
    assert preview.status_code == 200, preview.text
    assert preview.headers["cache-control"] == "no-store"
    assert "noindex" in preview.headers["x-robots-tag"]
    body = preview.json()
    # 공개 전 병원도 미리본다. 레이아웃·설정은 초안의 것.
    assert body["site_layout"] == "B"
    sections = {s["id"]: s for s in body["site_config"]["sections"]}
    assert sections["faq"]["enabled"] is False
    assert body["site_config"]["hero"]["line_breaks"]["hero.headline"]["pc"].startswith("편집기 검증")
    bad = await client.get("/api/v1/public/site-preview/not-a-token")
    assert bad.status_code == 404 and bad.headers["cache-control"] == "no-store"

    # 공개 반영 — 요청 키가 필수, 같은 키는 같은 결과, 다른 초안에 같은 키는 거절.
    no_key = await client.post(
        f"{_base(hospital_id)}/draft/publish",
        headers=ADMIN,
        json={"draft_id": draft["id"], "revision": draft["revision"]},
    )
    assert no_key.status_code == 400
    key = uuid.uuid4().hex
    published = await _publish(client, hospital_id, draft, key=key)
    assert published.status_code == 200, published.text
    v1 = published.json()["published"]
    assert v1["version"] == 1 and v1["state"] == "PUBLISHED"
    assert published.json()["idempotent_replay"] is False
    replay = await _publish(client, hospital_id, draft, key=key)
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
    assert replay.json()["published"]["id"] == v1["id"]
    reused = await client.post(
        f"{_base(hospital_id)}/draft/publish",
        headers={**ADMIN, "Idempotency-Key": key},
        json={"draft_id": str(uuid.uuid4()), "revision": 1},
    )
    assert reused.status_code == 409
    await db.refresh(hospital)
    assert hospital.site_layout == "B"
    # 공개 판이 있으면 병원 정보 PATCH로 레이아웃을 바꾸지 못한다(같은 값을 다시 보내는 것은 된다).
    profile = f"/api/v1/admin/hospitals/{hospital_id}/profile"
    moved = await client.patch(profile, headers=ADMIN, json={"site_layout": "C"})
    assert moved.status_code == 409, moved.text
    assert moved.json()["detail"]["code"] == "SITE_LAYOUT_OWNED_BY_SITE_CONFIG"
    cleared = await client.patch(profile, headers=ADMIN, json={"site_layout": None})
    assert cleared.status_code == 409
    same = await client.patch(profile, headers=ADMIN, json={"site_layout": "B"})
    assert same.status_code != 409, same.text
    # 공개 중이 아닌 병원은 재검증 intent 없이 저장만.
    assert await _count_runs(db, hospital_id, "SITE_REVALIDATION") == 0
    assert client.revalidated == []
    # 공개 반영 뒤 옛 미리보기 토큰은 열리지 않는다.
    after = await client.get(f"/api/v1/public/site-preview/{token}")
    assert after.status_code == 404

    # 두 번째 공개 판 — 옛 판은 SUPERSEDED로 남는다(이력).
    draft = (await _save(client, hospital_id, config={"faq": {"limit": 4}})).json()["draft"]
    assert draft["based_on_id"] == v1["id"]
    v2 = (await _publish(client, hospital_id, draft)).json()["published"]
    assert v2["version"] == 2
    assert "faq" in v2["change_summary"] and "sections.faq" in v2["change_summary"]
    state = (await client.get(_base(hospital_id), headers=ADMIN)).json()
    assert [entry["version"] for entry in state["history"]] == [2, 1]
    assert [entry["state"] for entry in state["history"]] == ["PUBLISHED", "SUPERSEDED"]
    assert state["draft"] is None

    # 복원 — 옛 판으로 새 초안(공개는 따로). 초안이 없음을 기대한다.
    restored = await client.post(
        f"{_base(hospital_id)}/versions/{v1['id']}/restore", headers=ADMIN, json={}
    )
    assert restored.status_code == 200, restored.text
    draft = restored.json()["draft"]
    assert draft["origin"] == "RESTORE" and draft["based_on_id"] == v1["id"]
    assert draft["config"]["sections"] == [{"id": "faq", "enabled": False}]
    again = await client.post(f"{_base(hospital_id)}/versions/{v1['id']}/restore", headers=ADMIN, json={})
    assert again.status_code == 409

    # 초기화(레이아웃 바꾸기 포함) — 지금 초안을 기본 세팅으로.
    reset = await client.post(
        f"{_base(hospital_id)}/reset",
        headers=ADMIN,
        json={"layout": "C", "draft_id": draft["id"], "revision": draft["revision"]},
    )
    assert reset.status_code == 200, reset.text
    draft = reset.json()["draft"]
    assert draft["layout"] == "C" and draft["config"] == {} and draft["origin"] == "RESET"

    # 버리기 — 전제조건이 맞아야 한다.
    wrong = await client.delete(
        f"{_base(hospital_id)}/draft", headers=ADMIN, params={"draft_id": draft["id"], "revision": 99}
    )
    assert wrong.status_code == 409
    discarded = await client.delete(
        f"{_base(hospital_id)}/draft",
        headers=ADMIN,
        params={"draft_id": draft["id"], "revision": draft["revision"]},
    )
    assert discarded.status_code == 200
    assert (await client.get(_base(hospital_id), headers=ADMIN)).json()["draft"] is None

    # 이력은 지우지 않는다(append-only). 감사 로그가 남는다.
    rows = (
        await db.execute(select(HospitalSiteConfig).where(HospitalSiteConfig.hospital_id == hospital_id))
    ).scalars().all()
    assert sorted(row.state for row in rows) == ["PUBLISHED", "SUPERSEDED"]
    actions = (
        await db.execute(select(AdminAuditLog.action).where(AdminAuditLog.hospital_id == hospital_id))
    ).scalars().all()
    for action in (
        "site_config_save",
        "site_config_publish",
        "site_config_restore",
        "site_config_reset",
        "site_config_discard",
    ):
        assert action in actions


async def test_publishing_a_public_hospital_writes_one_intent_and_serves_site_config(db, client, monkeypatch):
    hospital_id = await _hospital(db, layout="A")
    monkeypatch.setattr(
        admin_site_config,
        "profile_publication_verdict",
        lambda hospital: SimpleNamespace(allowed=True, blockers=()),
    )
    monkeypatch.setattr(public_site, "_is_active_public_hospital", lambda hospital: hospital is not None)

    public_url = f"/api/v1/public/hospitals/site-config-{hospital_id.hex[:12]}"
    before = await client.get(public_url)
    assert before.status_code == 200, before.text
    assert before.json()["site_config"] is None
    assert before.json()["site_layout"] == "A"

    draft = (await _save(client, hospital_id, layout="B", config={"faq": {"limit": 5}})).json()["draft"]
    assert await _count_runs(db, hospital_id, "SITE_REVALIDATION") == 0
    published = await _publish(client, hospital_id, draft)
    assert published.status_code == 200, published.text
    assert published.json()["revalidate_requested"] is True
    assert await _count_runs(db, hospital_id, "SITE_REVALIDATION") == 1
    assert len(client.revalidated) == 1

    served = (await client.get(public_url)).json()
    assert served["site_layout"] == "B"
    assert served["site_config"]["layout"] == "B"
    assert served["site_config"]["version"] == 1
    assert served["site_config"]["faq"]["limit"] == 5

    # 병원의 레이아웃이 공개 판과 갈라지면 공개 응답은 설정 없이(null) — 사이트는 지금 화면 그대로.
    await db.execute(text("UPDATE hospitals SET site_layout = 'C' WHERE id = :id"), {"id": hospital_id})
    await db.commit()
    assert (await client.get(public_url)).json()["site_config"] is None
    warnings = (await client.get(_base(hospital_id), headers=ADMIN)).json()["warnings"]
    assert "SITE_LAYOUT_DIVERGED" in [w["code"] for w in warnings]


async def test_old_published_snapshots_serve_current_defaults_for_fields_the_site_did_not_read(
    db, client, monkeypatch
):
    """③-1: 사이트가 아직 읽지 않던 칸의 틀린 기본값을 담은 옛 공개 판은 그 칸만 지금 기본값으로 낸다."""

    hospital_id = await _hospital(db, layout="B")
    monkeypatch.setattr(
        admin_site_config,
        "profile_publication_verdict",
        lambda hospital: SimpleNamespace(allowed=True, blockers=()),
    )
    monkeypatch.setattr(public_site, "_is_active_public_hospital", lambda hospital: hospital is not None)
    config = {"faq": {"limit": 5}, "sections": [{"id": "gallery", "align": "left"}]}
    draft = (await _save(client, hospital_id, layout="B", config=config)).json()["draft"]
    assert (await _publish(client, hospital_id, draft)).status_code == 200

    # ③-1 전 판처럼 만든다 — 판 번호와 그때의 틀린 기본값.
    row = await db.scalar(
        select(HospitalSiteConfig).where(
            HospitalSiteConfig.hospital_id == hospital_id, HospitalSiteConfig.state == "PUBLISHED"
        )
    )
    old = dict(row.resolved)
    old["defaults_version"] = "2026-11-21.2"
    old["doctor"] = {**old["doctor"], "photo_focus": {"x": 0.5, "y": 0.2}}
    old["sections"] = [{**section, "background": "default"} for section in old["sections"]]
    row.resolved = old
    row.defaults_version = "2026-11-21.2"
    await db.commit()

    served = (await client.get(f"/api/v1/public/hospitals/site-config-{hospital_id.hex[:12]}")).json()
    site_config = served["site_config"]
    assert site_config["doctor"]["photo_focus"] == {"x": 0.5, "y": 0.5}
    sections = {section["id"]: section for section in site_config["sections"]}
    assert sections["care_principles"]["background"] == "tint"
    assert sections["gallery"]["align"] == "left"  # 직접 바꾼 칸은 남는다
    assert site_config["faq"]["limit"] == 5  # 사이트가 이미 읽는 칸은 스냅샷 그대로
    await db.refresh(row)
    assert row.resolved["doctor"]["photo_focus"] == {"x": 0.5, "y": 0.2}  # 저장된 스냅샷은 바꾸지 않는다

    # 편집기도 같은 완성본에서 시작한다 — 옛 기본값이 '직접 바꾼 칸'으로 굳지 않게.
    admin_view = (await client.get(_base(hospital_id), headers=ADMIN)).json()["published"]["resolved"]
    assert admin_view["doctor"] == site_config["doctor"]
    assert {s["id"]: s["background"] for s in admin_view["sections"]} == {
        s["id"]: s["background"] for s in site_config["sections"]
    }
    assert admin_view["faq"]["limit"] == 5
