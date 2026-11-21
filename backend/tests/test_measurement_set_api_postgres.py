"""측정 키워드 30개 세트 어드민 API — 실제 Postgres에서 초안 → 저장 → 교체 → 거절 (설계 §6)."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.main import app
from app.models.audit import AdminAuditLog
from app.models.sov import AIQueryTarget, AIQueryVariant
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
async def client(db: AsyncSession):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _hospital(db: AsyncSession) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, region, specialties, keywords) "
            "VALUES (:id, :name, :slug, CAST(:region AS json), CAST(:specialties AS json), CAST(:keywords AS json))"
        ),
        {
            "id": hospital_id,
            "name": "행복드림의원",
            "slug": f"measure-{hospital_id.hex[:12]}",
            "region": json.dumps(["서울 마포구", "합정동"], ensure_ascii=False),
            "specialties": json.dumps(["내과"], ensure_ascii=False),
            "keywords": json.dumps(["위내시경", "갑상선초음파", "대장내시경", "당뇨"], ensure_ascii=False),
        },
    )
    await db.commit()
    return hospital_id


async def _legacy_target(db: AsyncSession, hospital_id: uuid.UUID, name: str) -> uuid.UUID:
    target = AIQueryTarget(
        hospital_id=hospital_id,
        name=name,
        target_intent="추천 탐색",
        region_terms=[],
        decision_criteria=[],
        platforms=["CHATGPT", "GEMINI"],
        competitor_names=[],
        priority="HIGH",
        status="ACTIVE",
        in_tracking_set=True,
        created_by="V0 자동 시드",
    )
    target.variants = [
        AIQueryVariant(query_text=name, platform=platform, language="ko", is_active=True)
        for platform in ("CHATGPT", "GEMINI")
    ]
    db.add(target)
    await db.commit()
    return target.id


async def _targets(db: AsyncSession, hospital_id: uuid.UUID) -> dict[str, AIQueryTarget]:
    db.expire_all()
    rows = (
        await db.execute(
            select(AIQueryTarget)
            .options(selectinload(AIQueryTarget.variants))
            .where(AIQueryTarget.hospital_id == hospital_id)
        )
    ).scalars()
    return {row.name: row for row in rows}


async def test_draft_save_swap_and_reject(db, client):
    hospital_id = await _hospital(db)
    reused_id = await _legacy_target(db, hospital_id, "서울 마포구 내과 추천해줘")
    dropped_id = await _legacy_target(db, hospital_id, "마포 내과 병원 어디가 좋은지 비교해줘")
    base = f"/api/v1/admin/hospitals/{hospital_id}/measurement-set"

    status = await client.get(base, headers=ADMIN)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["curated"] is False
    assert body["rows"] == []
    assert {q["text"] for q in body["legacy_questions"]} == {
        "서울 마포구 내과 추천해줘",
        "마포 내과 병원 어디가 좋은지 비교해줘",
    }
    assert body["plan"]["B"] == {"fixed": 7, "rotating": 3}

    draft = await client.post(f"{base}/draft", headers=ADMIN)
    assert draft.status_code == 200, draft.text
    draft_rows = draft.json()["rows"]
    assert draft.json()["shape"]["fixed"] == 20
    assert draft.json()["shape"]["rotating"] == 10
    assert draft.json()["shape"]["problems"] == []
    # 초안은 저장하지 않는다.
    assert (await client.get(base, headers=ADMIN)).json()["rows"] == []

    payload = {"rows": [{"text": r["text"], "group": r["group"], "role": r["role"]} for r in draft_rows]}
    saved = await client.put(base, headers=ADMIN, json=payload)
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["curated"] is True
    assert saved_body["shape"]["fixed"] == 20
    assert saved_body["legacy_questions"] == []

    targets = await _targets(db, hospital_id)
    reused = targets["서울 마포구 내과 추천해줘"]
    # 같은 문구의 옛 질문은 다시 쓴다(측정 이력 보존).
    assert reused.id == reused_id
    assert (reused.question_group, reused.set_role, reused.in_tracking_set) == ("B", "FIXED", True)
    dropped = targets["마포 내과 병원 어디가 좋은지 비교해줘"]
    assert dropped.id == dropped_id
    assert (dropped.question_group, dropped.set_role, dropped.in_tracking_set) == (None, None, False)
    assert dropped.status == "ACTIVE"  # 글 주제로는 남는다
    brand = [t for t in targets.values() if t.question_group == "A"]
    assert len(brand) == 5 and all(t.set_role == "FIXED" for t in brand)
    candidates = [t for t in targets.values() if t.question_group and t.set_role is None]
    assert candidates and all(t.status == "PAUSED" for t in candidates)
    assert all(
        {v.platform for v in t.variants if v.is_active} == {"CHATGPT", "GEMINI"}
        for t in targets.values()
        if t.set_role
    )

    # 교체 질문 하나를 후보와 맞바꾼다.
    rows = [dict(row) for row in payload["rows"]]
    rotating_index = next(i for i, r in enumerate(rows) if r["role"] == "ROTATING")
    candidate_index = next(i for i, r in enumerate(rows) if r["role"] is None)
    rows[rotating_index]["role"], rows[candidate_index]["role"] = None, "ROTATING"
    swapped = await client.put(base, headers=ADMIN, json={"rows": rows})
    assert swapped.status_code == 200, swapped.text
    targets = await _targets(db, hospital_id)
    promoted = targets[rows[candidate_index]["text"]]
    assert (promoted.set_role, promoted.status, promoted.in_tracking_set) == ("ROTATING", "ACTIVE", True)
    demoted = targets[rows[rotating_index]["text"]]
    assert (demoted.set_role, demoted.in_tracking_set, demoted.status) == (None, False, "PAUSED")

    # 세트에서 뺀 질문: 세트가 만든 행은 보관, 옛 병원명 질문은 그룹 A를 남기고 쉰다(글 주제 아님).
    removed_text = next(r["text"] for i, r in enumerate(rows) if r["role"] is None and i != rotating_index)
    if removed_text:
        trimmed = [r for r in rows if r["text"] != removed_text]
        response = await client.put(base, headers=ADMIN, json={"rows": trimmed})
        assert response.status_code == 200, response.text
        archived = (await _targets(db, hospital_id))[removed_text]
        assert archived.status == "ARCHIVED"
        restored = await client.put(base, headers=ADMIN, json={"rows": rows})
        assert restored.status_code == 200
        assert (await _targets(db, hospital_id))[removed_text].status == "PAUSED"

    # 지침 3-1 문형과 모양 위반은 저장하지 않는다.
    bad = [dict(row) for row in rows]
    bad[0] = {"text": "마포 산부인과 병원 추천해줘", "group": "B", "role": "FIXED"}
    rejected = await client.put(base, headers=ADMIN, json={"rows": bad})
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["code"] == "MEASUREMENT_SET_INVALID"
    assert detail["problems"][0]["index"] == 0
    too_few = [r for r in rows if r["role"] != "FIXED" or r["group"] == "A"]
    short = await client.put(base, headers=ADMIN, json={"rows": too_few})
    assert short.status_code == 422
    assert any(p["index"] is None for p in short.json()["detail"]["problems"])

    audits = (
        await db.execute(
            select(AdminAuditLog.action).where(AdminAuditLog.hospital_id == hospital_id)
        )
    ).scalars().all()
    assert audits.count("save_measurement_set") >= 2


async def test_rollout_cli_prechecks_applies_and_keeps_curated_sets(db, monkeypatch):
    import io

    from app.utils import measurement_set_rollout as rollout

    hospital_id = await _hospital(db)
    await _legacy_target(db, hospital_id, "서울 마포구 내과 추천해줘")
    connection = await db.connection()

    def factory():
        return AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(rollout, "get_async_sessionmaker", lambda: factory)

    async def _run(command: str, **kwargs) -> list[dict]:
        out = io.StringIO()
        code = await rollout.run(command, ids=[hospital_id], out=out, **kwargs)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        return [code, *lines]

    code, precheck = await _run("precheck", replace=False, confirm=False)
    assert code == 0
    assert precheck["action"] == "apply"
    assert precheck["curated_now"] is False
    assert precheck["draft"]["A"]["fixed"] == 5
    assert precheck["reused_texts"] == 1
    assert precheck["problems"] == []
    assert (await _targets(db, hospital_id))["서울 마포구 내과 추천해줘"].question_group is None

    code, refused = await _run("apply", replace=False, confirm=False)
    assert code == 2 and "confirm" in refused["error"]

    code, applied = await _run("apply", replace=False, confirm=True)
    assert code == 0 and applied["action"] == "applied"
    assert applied["summary"]["fixed"] == 20
    targets = await _targets(db, hospital_id)
    assert targets["서울 마포구 내과 추천해줘"].set_role == "FIXED"
    assert sum(t.set_role is not None for t in targets.values()) == 30

    code, again = await _run("apply", replace=False, confirm=True)
    assert again["action"] == "skipped_curated"
