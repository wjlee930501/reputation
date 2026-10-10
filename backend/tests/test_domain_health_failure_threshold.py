"""도메인 점검 한 번의 실패는 사람을 부르지 않는다.

2026-10-09 12:30 UTC, 세 병원의 DOMAIN_HEALTH_CHECK가 `http_503`으로 한 번씩 실패했고 앞뒤
점검은 모두 `tenant_marker_ok`였다. 실패마다 DOMAIN_UNHEALTHY 인시던트와 `[조치 필요]` Slack이
열렸다가 저절로 복구됐다. 연속 두 번 실패해야 인시던트를 연다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import domain_health_control as control

_HOSPITAL_ID = uuid.UUID("d0e10000-0000-0000-0000-000000000001")
_DOMAIN = "clinic.example.com"
_AT = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)


class _Session:
    """record_domain_health_check가 쓰는 만큼만 흉내 낸다: 병원 조회 → 중복 실행 없음."""

    def __init__(self) -> None:
        self._scalars = [SimpleNamespace(id=_HOSPITAL_ID, name="테스트의원"), None]
        self.added: list[object] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        return self._scalars.pop(0)

    def add(self, item) -> None:
        item.id = uuid.uuid4()
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _install(monkeypatch, *, failure_streak: int) -> tuple[_Session, list[str]]:
    session = _Session()
    opened: list[str] = []

    async def streak(_db, _hospital_id, _domain_key):
        return failure_streak

    async def open_incident(_db, _hospital, _run, canonical_host, _reason):
        opened.append(canonical_host)
        return True

    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(control, "_failure_streak", streak)
    monkeypatch.setattr(control, "_open_domain_incident", open_incident)
    return session, opened


async def _fail_once():
    return await control.record_domain_health_check(
        hospital_id=_HOSPITAL_ID,
        canonical_host=_DOMAIN,
        healthy=False,
        safe_reason="http_503",
        observed_at=_AT,
    )


@pytest.mark.asyncio
async def test_single_failed_check_records_run_and_opens_nothing(monkeypatch) -> None:
    session, opened = _install(monkeypatch, failure_streak=1)

    outcome = await _fail_once()

    assert outcome.recorded is True
    assert outcome.incident_opened is False
    assert opened == []
    assert len(session.added) == 1 and session.commits == 1
    assert session.added[0].state == "FAILED"


@pytest.mark.asyncio
async def test_second_consecutive_failure_opens_one_incident(monkeypatch) -> None:
    _session, opened = _install(monkeypatch, failure_streak=2)

    outcome = await _fail_once()

    assert outcome.incident_opened is True
    assert opened == [_DOMAIN]


def test_thresholds_match_the_alerting_contract() -> None:
    assert control._FAILURE_CHECKS == 2
    assert control._RECOVERY_CHECKS == 3
