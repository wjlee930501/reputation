"""실제 PostgreSQL로 확인한다 — 도메인을 바꾸면 옛 도메인의 DNS 사고가 닫히고 새 도메인 사고만 남는다.

사고는 실제 여는 경로(`poll_pending_custom_domains_once` → `open_ops_incident`)로 연다. 저장된
`source_id`는 점이 든 `{hospital_id}:{domain}`을 `normalize_source_id`가 `sha256:…`로 바꾸므로,
정리가 저장된 쌍을 원래 쌍과 비교하거나 그 값으로 키를 다시 만들면 옛 사고를 찾지 못한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.operations import Incident, IncidentState, NotificationOutbox
from app.services import ops_incident_alerts
from app.workers import domain_dns_poll
from tests.db_env import require_db_url


@pytest.mark.asyncio
async def test_a_domain_change_closes_the_old_domains_dns_incident_through_the_real_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(require_db_url("TASK19_ASYNC_DATABASE_URL"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(domain_dns_poll, "get_async_sessionmaker", lambda: sessions)
    monkeypatch.setattr(ops_incident_alerts, "get_async_sessionmaker", lambda: sessions)
    hospital_id = uuid.uuid4()
    old_domain = f"www.old-{hospital_id.hex[:8]}.example.com"
    new_domain = f"www.new-{hospital_id.hex[:8]}.example.com"
    long_ago = datetime.now(UTC) - timedelta(days=30)

    # 공유 테스트 DB의 다른 병원을 건드리지 않도록 실제 조회 결과를 이 병원으로만 좁힌다.
    real_targets = domain_dns_poll._load_targets_in
    real_active = domain_dns_poll._active_dns_incidents

    async def own_targets(db):
        return [t for t in await real_targets(db) if t.hospital_id == hospital_id]

    async def own_incidents(db):
        return [i for i in await real_active(db) if i.hospital_id == hospital_id]

    async def dns_not_ready(_target):
        return SimpleNamespace(dns_verified=False)

    monkeypatch.setattr(domain_dns_poll, "_load_targets_in", own_targets)
    monkeypatch.setattr(domain_dns_poll, "_active_dns_incidents", own_incidents)
    monkeypatch.setattr(domain_dns_poll, "_verify", dns_not_ready)

    async def incidents() -> list[Incident]:
        async with sessions() as db:
            return list(
                (
                    await db.execute(
                        select(Incident)
                        .where(Incident.hospital_id == hospital_id)
                        .order_by(Incident.first_seen_at)
                    )
                ).scalars()
            )

    try:
        async with sessions() as db:
            db.add(
                Hospital(
                    id=hospital_id,
                    name="DNS 변경 테스트의원",
                    slug=f"dns-change-{hospital_id.hex[:10]}",
                    status=HospitalStatus.PENDING_DOMAIN,
                    plan=Plan.PLAN_12,
                    aeo_domain=old_domain,
                    updated_at=long_ago,
                )
            )
            await db.commit()

        # 14일 창이 지난 옛 도메인 — 실제 여는 경로로 사고가 열린다.
        await domain_dns_poll.poll_pending_custom_domains_once(datetime.now(UTC))
        opened = await incidents()
        assert len(opened) == 1
        old_incident = opened[0]
        assert old_incident.state == IncidentState.OPEN.value
        # 저장된 source_id는 원래 쌍이 아니다(정규화가 해시로 바꿨다).
        assert old_incident.source_id != f"{hospital_id}:{old_domain}"
        assert old_incident.source_id.startswith("sha256:")

        # 병원이 대기 중에 도메인을 바꾼다(새 도메인도 창이 지났다고 둔다).
        async with sessions() as db:
            await db.execute(
                update(Hospital)
                .where(Hospital.id == hospital_id)
                .values(aeo_domain=new_domain, updated_at=long_ago)
            )
            await db.commit()

        await domain_dns_poll.poll_pending_custom_domains_once(datetime.now(UTC))

        after = {row.id: row for row in await incidents()}
        assert len(after) == 2
        assert after[old_incident.id].state in {
            IncidentState.RECOVERED.value,
            IncidentState.ACKNOWLEDGED.value,
        }
        new_incident = next(row for row in after.values() if row.id != old_incident.id)
        assert new_incident.state == IncidentState.OPEN.value
        assert new_incident.dedupe_key == domain_dns_poll._dedupe_key(
            domain_dns_poll.PendingDomain(hospital_id, "DNS 변경 테스트의원", new_domain)
        )

        # 한 번 더 돌아도 지금 도메인의 사고는 닫지 않는다.
        await domain_dns_poll.poll_pending_custom_domains_once(datetime.now(UTC))
        again = {row.id: row for row in await incidents()}
        assert again[new_incident.id].state == IncidentState.OPEN.value
    finally:
        async with sessions() as db:
            await db.execute(
                delete(NotificationOutbox).where(NotificationOutbox.hospital_id == hospital_id)
            )
            await db.execute(delete(Incident).where(Incident.hospital_id == hospital_id))
            await db.execute(delete(Hospital).where(Hospital.id == hospital_id))
            await db.commit()
        await engine.dispose()
