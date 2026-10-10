"""공유 공개 서비스의 5xx는 병원별 `[조치 필요]`가 아니라 개발 담당 인시던트 하나다.

2026-10-09 12:30 UTC 같은 점검에서 세 병원이 `http_503`을 받았고, 병원마다 DOMAIN_UNHEALTHY와
운영 채널 Slack이 열렸다. 세 병원 주소는 한 공개 서비스가 서빙한다 — AE가 병원 정보에서 고칠 일이 아니다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.operations import IncidentState
from app.services import domain_health_control as control
from app.services.incident_types import (
    SLACK_DEVELOPER_CHANNEL,
    IncidentAudience,
    incident_audience,
)
from app.workers import tasks

_AT = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)


# ── 작업: 같은 실행의 5xx를 묶는다 ────────────────────────────────────────


class _Hospitals:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _statement):
        return _Hospitals(self.rows)


class _NoHttp:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _hospital(name: str):
    return SimpleNamespace(id=uuid.uuid4(), slug=name, aeo_domain=f"{name}.example.com")


def _run_monitor(monkeypatch, verdicts: dict[str, tuple[bool, str]]):
    hospitals = [_hospital(name) for name in verdicts]
    recorded: list[dict] = []
    public_site: list[dict] = []

    def check(_client, domain, *, expected_hospital_id, expected_slug):
        return verdicts[expected_slug]

    async def record(**facts):
        recorded.append(facts)
        opened = not facts["healthy"] and facts["open_incident"]
        return SimpleNamespace(incident_opened=opened, incident_recovered=False)

    async def record_public_site(**facts):
        public_site.append(facts)
        opened = facts["failing_hospitals"] >= control.PUBLIC_SITE_OUTAGE_MIN_HOSPITALS
        return SimpleNamespace(incident_opened=opened, incident_recovered=False)

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _Session(hospitals))
    monkeypatch.setattr(tasks.httpx, "Client", _NoHttp)
    monkeypatch.setattr(tasks, "_check_custom_domain_https", check)
    monkeypatch.setattr(tasks, "_persist_live_domain_check", lambda *_args: True)
    monkeypatch.setattr(tasks, "record_domain_health_check", record)
    monkeypatch.setattr(tasks, "record_public_site_check", record_public_site)
    result = tasks.monitor_live_custom_domains.run()
    by_slug = {
        hospital.slug: facts
        for hospital in hospitals
        for facts in recorded
        if facts["hospital_id"] == hospital.id
    }
    return result, by_slug, public_site


def test_shared_5xx_in_one_run_opens_no_per_hospital_incident(monkeypatch) -> None:
    result, recorded, public_site = _run_monitor(
        monkeypatch,
        {
            "a": (False, "http_503"),
            "b": (False, "http_503"),
            "c": (False, "http_502"),
            "d": (True, "tenant_marker_ok"),
        },
    )

    # 모든 병원 점검은 그대로 기록하지만 5xx 병원의 병원별 인시던트는 열지 않는다.
    assert len(recorded) == 4
    assert [recorded[slug]["open_incident"] for slug in "abc"] == [False, False, False]
    assert recorded["d"]["open_incident"] is True
    assert public_site == [{"failing_hospitals": 3}]
    assert result["new_failures"] == 0
    assert result["shared_server_errors"] == 3
    assert result["public_site_incident_opened"] is True


def test_non_5xx_failure_keeps_its_own_hospital_incident_during_shared_outage(monkeypatch) -> None:
    _result, recorded, _public_site = _run_monitor(
        monkeypatch,
        {
            "a": (False, "http_503"),
            "b": (False, "http_504"),
            "mismatch": (False, "tenant_marker_mismatch"),
        },
    )

    assert recorded["a"]["open_incident"] is False
    assert recorded["b"]["open_incident"] is False
    # 다른 병원 정보를 응답한 주소는 공개 서비스 장애로 설명되지 않는다.
    assert recorded["mismatch"]["open_incident"] is True


def test_one_hospital_5xx_is_not_a_shared_outage(monkeypatch) -> None:
    result, recorded, public_site = _run_monitor(
        monkeypatch,
        {"a": (False, "http_503"), "b": (True, "tenant_marker_ok")},
    )

    assert recorded["a"]["open_incident"] is True
    assert public_site == [{"failing_hospitals": 1}]
    assert result["public_site_incident_opened"] is False


@pytest.mark.parametrize(
    ("reason", "shared"),
    [
        ("http_500", True),
        ("http_503", True),
        ("http_599", True),
        ("http_429", False),
        ("http_404", False),
        ("timeout", False),
        ("tls_or_network_error", False),
        ("http_", False),
        ("http_5xx", False),
    ],
)
def test_only_http_5xx_counts_as_a_shared_public_service_error(reason: str, shared: bool) -> None:
    assert control.is_public_site_server_error(reason) is shared


# ── 서비스: 공개 서비스 인시던트도 연속 두 번 실패해야 연다 ────────────────


class _ControlSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        return None  # 같은 15분 칸의 기존 실행 없음

    def add(self, item) -> None:
        item.id = uuid.uuid4()
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _install_control(monkeypatch, *, streak: int):
    session = _ControlSession()
    calls: dict[str, list] = {"open": [], "recover": [], "streak": []}

    async def fake_streak(_db, **kwargs):
        calls["streak"].append(kwargs)
        return streak

    async def fake_open(_db, _run, failing_hospitals):
        calls["open"].append(failing_hospitals)
        return True

    async def fake_recover(_db, **kwargs):
        calls["recover"].append(kwargs)
        return True

    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(control, "_state_streak", fake_streak)
    monkeypatch.setattr(control, "_open_public_site_incident", fake_open)
    monkeypatch.setattr(control, "_recover_open_incident", fake_recover)
    return session, calls


@pytest.mark.asyncio
async def test_first_shared_outage_run_records_and_opens_nothing(monkeypatch) -> None:
    session, calls = _install_control(monkeypatch, streak=1)

    outcome = await control.record_public_site_check(failing_hospitals=3, observed_at=_AT)

    assert outcome == control.PublicSiteOutcome(True, False, False)
    assert calls["open"] == []
    run = session.added[0]
    assert run.hospital_id is None and run.state == "FAILED"
    assert calls["streak"][0]["hospital_id"] is None


@pytest.mark.asyncio
async def test_second_consecutive_shared_outage_opens_one_developer_incident(monkeypatch) -> None:
    _session, calls = _install_control(monkeypatch, streak=2)

    outcome = await control.record_public_site_check(failing_hospitals=3, observed_at=_AT)

    assert outcome.incident_opened is True
    assert calls["open"] == [3]


@pytest.mark.asyncio
async def test_three_healthy_runs_recover_the_shared_incident(monkeypatch) -> None:
    _session, calls = _install_control(monkeypatch, streak=3)

    outcome = await control.record_public_site_check(failing_hospitals=1, observed_at=_AT)

    assert outcome == control.PublicSiteOutcome(True, False, True)
    assert calls["recover"][0]["hospital_id"] is None
    assert calls["recover"][0]["source_type"] == "PUBLIC_SITE_HEALTH"


@pytest.mark.asyncio
async def test_shared_incident_goes_to_the_developer_channel_once_per_episode(monkeypatch) -> None:
    previous: list[object] = [None]
    enqueued: list[object] = []

    class DB:
        async def scalar(self, _statement):
            return previous[0]

    async def open_or_touch(_db, request, **_kwargs):
        return SimpleNamespace(
            id=uuid.uuid4(),
            severity="HIGH",
            customer_impact=request.customer_impact,
            next_action=request.next_action,
            admin_path=request.admin_path,
            hospital_id=request.hospital_id,
            version=1,
            safe_error_message=request.safe_error_message,
            episode_seq=1,
            incident_type=request.incident_type,
        )

    async def enqueue(_db, intent):
        enqueued.append(intent)

    monkeypatch.setattr(control, "open_or_touch_incident", open_or_touch)
    monkeypatch.setattr(control, "enqueue_notification", enqueue)
    run = SimpleNamespace(id=uuid.uuid4(), completed_at=_AT)

    first = await control._open_public_site_incident(DB(), run, 3)
    previous[0] = SimpleNamespace(state=IncidentState.OPEN.value)
    touched = await control._open_public_site_incident(DB(), run, 3)

    assert first is True and touched is False
    assert len(enqueued) == 1
    assert enqueued[0].channel == SLACK_DEVELOPER_CHANNEL
    assert enqueued[0].hospital_id is None
    assert incident_audience(control.PUBLIC_SITE_INCIDENT_TYPE) is IncidentAudience.DEVELOPER
