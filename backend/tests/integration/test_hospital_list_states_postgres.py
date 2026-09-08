"""병원 목록이 3상태·예외 수·담당 AE를 그대로 내려준다 — 실제 SQL로 확인한다.

목록이 병원마다 기준·인시던트·담당자를 조회하면 화면 하나가 병원 수에 비례하는 쿼리를
낸다. 그리고 판정이 admin으로 새어 나가면 목록과 상세가 다른 답을 한다(PR-0A H-06).
JSONB gap·부분 unique 인덱스·enum 집계는 모의 세션으로 검증할 수 없다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from sqlalchemy import event

from app.api.admin.hospitals import list_hospitals
from app.core.database import get_db
from app.core.rate_limit import get_request_ip
from app.main import app
from app.models.admin_user import ROLE_OWNER, AdminUser
from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity, IncidentState
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.evidence_noise import compute_evidence_noise_hash

pytestmark = pytest.mark.asyncio

# 병원 페이지 1 + 콘텐츠 준비 묶음 4(승인·자료·노이즈·예외 초안) + 예외 수 1 + 담당 AE 1.
# 병원 수와 무관해야 한다 — 늘어나면 목록이 N+1이다.
_LIST_STATEMENT_BUDGET = 7


async def _admin_user(db, name: str = "AE 담당") -> AdminUser:
    actor = AdminUser(
        email=f"{uuid.uuid4().hex}@example.com",
        name=name,
        role=ROLE_OWNER,
        password_hash="pbkdf2_sha256$1$c2FsdA$ZGlnZXN0",
        is_active=True,
    )
    db.add(actor)
    await db.flush()
    return actor


async def _source(db, hospital: Hospital, *, status: SourceStatus = SourceStatus.PROCESSED):
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title=f"{hospital.name} 홈페이지",
        raw_text="원장이 직접 설명한 진료 원칙 본문",
        content_hash=f"hash-{uuid.uuid4().hex[:12]}",
        status=status,
        processed_at=datetime.now(UTC) if status == SourceStatus.PROCESSED else None,
    )
    db.add(source)
    await db.flush()
    return source


async def _hospital(
    db,
    name: str,
    *,
    status: HospitalStatus = HospitalStatus.ACTIVE,
    site_live: bool = True,
    site_built: bool = True,
    profile_complete: bool = True,
    schedule_set: bool = True,
    unprocessed_source: bool = False,
    escalated_draft: bool = False,
    aeo_domain: str | None = None,
    domain_last_check_ok: bool | None = None,
) -> Hospital:
    hospital = Hospital(
        name=name,
        slug=f"clinic-{uuid.uuid4().hex[:12]}",
        status=status,
        site_live=site_live,
        site_built=site_built,
        profile_complete=profile_complete,
        schedule_set=schedule_set,
        aeo_domain=aeo_domain,
        domain_last_check_ok=domain_last_check_ok,
        domain_last_checked_at=(
            datetime.now(UTC) if domain_last_check_ok is not None else None
        ),
    )
    db.add(hospital)
    await db.flush()
    source = await _source(db, hospital)
    if unprocessed_source:
        await _source(db, hospital, status=SourceStatus.PENDING)
    db.add(
        HospitalContentPhilosophy(
            hospital_id=hospital.id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            positioning_statement=f"{name}은 근거 중심으로 충분히 설명합니다.",
            patient_promise="확인된 정보만 환자에게 안내합니다.",
            source_snapshot_hash=compute_sources_snapshot_hash([source]),
            evidence_noise_hash=compute_evidence_noise_hash([]),
            source_asset_ids=[str(source.id)],
            approved_at=datetime.now(UTC),
        )
    )
    if escalated_draft:
        db.add(
            HospitalContentPhilosophy(
                hospital_id=hospital.id,
                version=2,
                status=PhilosophyStatus.DRAFT,
                positioning_statement=f"{name} 초안",
                unsupported_gaps=[
                    {"field": AUTO_REVIEW_GAP_FIELD, "reason": "근거 없는 효과 표현"}
                ],
            )
        )
    await db.flush()
    return hospital


async def _incident(db, hospital: Hospital, *, state: IncidentState) -> Incident:
    incident = Incident(
        hospital_id=hospital.id,
        dedupe_key=f"qa:{uuid.uuid4()}",
        incident_type="PROVIDER_TIMEOUT",
        state=state,
        severity=IncidentSeverity.HIGH,
        customer_impact="오늘 콘텐츠 초안 생성이 멈췄습니다.",
        source_type="content_generation",
        safe_error_code="PROVIDER_TIMEOUT",
        safe_error_message="AI 공급자 응답이 지연되고 있습니다.",
        next_action="작업을 다시 시도해 주세요.",
        admin_path=f"/hospitals/{hospital.id}/content",
        # ck_incidents_recovery_fact — 복구 상태는 복구 시각을 함께 요구한다.
        recovered_at=datetime.now(UTC) if state == IncidentState.RECOVERED else None,
    )
    db.add(incident)
    await db.flush()
    return incident


async def _handoff(db, hospital: Hospital, *, ae_owner: AdminUser) -> HospitalHandoff:
    """인수까지 끝난 계약 — ck_hospital_handoffs_state_facts가 요구하는 사실을 모두 채운다."""
    handoff = HospitalHandoff(
        hospital_id=hospital.id,
        state=HandoffState.HANDOFF_ACCEPTED,
        acceptance_source=HandoffSource.DIRECT_CREATE,
        sales_owner_id=ae_owner.id,
        ae_owner_id=ae_owner.id,
        contract_reference=f"CTR-{hospital.slug}",
        contract_effective_at=datetime.now(UTC) - timedelta(days=1),
        plan="PLAN_12",
        sla_due_at=datetime.now(UTC) + timedelta(days=7),
        accepted_by_id=ae_owner.id,
        accepted_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(handoff)
    await db.flush()
    return handoff


async def _list_rows(db, actor: AdminUser) -> dict[str, dict]:
    """HTTP 경로로 받아 응답 모델(HospitalListItem)까지 통과한 행만 본다."""

    async def override_get_db():
        yield db

    previous_limiter = app.state.limiter
    app.state.limiter = Limiter(key_func=get_request_ip, storage_uri="memory://")
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/admin/hospitals",
                headers={"X-Admin-Key": "test-admin-key", "X-Admin-Actor": actor.email},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.limiter = previous_limiter
    assert response.status_code == 200
    return {row["id"]: row for row in response.json()}


async def test_list_rows_carry_the_three_states(pg_async_session):
    db = pg_async_session
    actor = await _admin_user(db)
    live = await _hospital(db, "운영 중 의원")
    paused = await _hospital(db, "일시정지 의원", status=HospitalStatus.PAUSED)
    preparing = await _hospital(
        db,
        "준비 중 의원",
        status=HospitalStatus.BUILDING,
        site_live=False,
        site_built=False,
        profile_complete=False,
        schedule_set=False,
        unprocessed_source=True,
    )

    rows = await _list_rows(db, actor)

    assert rows[str(live.id)]["public_service_state"] == {"kind": "live", "remaining": []}
    assert rows[str(live.id)]["content_state"] == {"kind": "auto", "remaining": []}
    assert rows[str(paused.id)]["public_service_state"]["kind"] == "paused"
    assert rows[str(preparing.id)]["public_service_state"] == {
        "kind": "not_live",
        "remaining": ["profile_complete", "site_built"],
    }
    assert rows[str(preparing.id)]["content_state"] == {
        "kind": "preparing",
        "remaining": ["schedule", "sources:1", "essence_review"],
    }


async def test_list_rows_count_open_exceptions_and_name_the_ae_owner(pg_async_session):
    db = pg_async_session
    actor = await _admin_user(db)
    excepted = await _hospital(db, "예외 의원", escalated_draft=True)
    await _incident(db, excepted, state=IncidentState.OPEN)
    await _incident(db, excepted, state=IncidentState.OPEN)
    # 복구된 인시던트는 사람이 볼 예외가 아니다 — 세면 목록이 상시 빨갛게 된다.
    await _incident(db, excepted, state=IncidentState.RECOVERED)
    owned = await _hospital(db, "담당 있는 의원", aeo_domain="jangclinic.kr")
    owned.domain_last_check_ok = True
    owned.domain_last_checked_at = datetime.now(UTC)
    await db.flush()
    await _handoff(db, owned, ae_owner=actor)

    rows = await _list_rows(db, actor)

    assert rows[str(excepted.id)]["content_state"]["kind"] == "exception"
    assert rows[str(excepted.id)]["open_exception_count"] == 3
    assert rows[str(excepted.id)]["ae_owner"] is None
    assert rows[str(excepted.id)]["domain_state"]["kind"] == "unused"
    assert rows[str(owned.id)]["open_exception_count"] == 0
    assert rows[str(owned.id)]["ae_owner"] == {"id": str(actor.id), "name": actor.name}
    assert rows[str(owned.id)]["domain_state"]["kind"] == "connected"
    assert rows[str(owned.id)]["domain_state"]["last_checked_at"] is not None


async def _list_query_count(db) -> int:
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.bind.engine
    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        await list_hospitals(skip=0, limit=50, db=db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    return len(statements)


async def test_list_query_count_is_constant_across_hospitals(pg_async_session):
    db = pg_async_session
    await _hospital(db, "쿼리예산 첫 의원")

    one_count = await _list_query_count(db)

    for index in range(11):
        await _hospital(db, f"쿼리예산 {index} 의원", escalated_draft=index % 2 == 0)
    many_count = await _list_query_count(db)

    assert one_count == _LIST_STATEMENT_BUDGET
    assert many_count == one_count
