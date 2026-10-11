"""대기 도메인 폴링 대상과 창 시작 — 실 Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.workers.domain_dns_poll import PendingDomain, _load_targets_in, _poll_started_at_in


async def test_targets_and_window_start_come_from_existing_rows(pg_async_session) -> None:
    hospital = Hospital(
        id=uuid.uuid4(), name="DNS대기의원", slug=f"dns-poll-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.PENDING_DOMAIN, site_built=True, profile_complete=True,
        aeo_domain="www.dns-poll.example",
    )
    pg_async_session.add(hospital)
    await pg_async_session.flush()
    older = datetime.now(UTC) - timedelta(days=20)
    newer = datetime.now(UTC) - timedelta(days=2)
    for created_at in (older, newer):
        pg_async_session.add(AdminAuditLog(
            hospital_id=hospital.id, actor="ae@example.test", action="connect_domain",
            target_type="domain", target_id="www.dns-poll.example", detail={},
            created_at=created_at,
        ))
    await pg_async_session.flush()

    targets = [t for t in await _load_targets_in(pg_async_session) if t.hospital_id == hospital.id]
    assert targets == [PendingDomain(hospital.id, "DNS대기의원", "www.dns-poll.example")]
    started = await _poll_started_at_in(pg_async_session, targets[0])
    assert abs((started - newer).total_seconds()) < 1
