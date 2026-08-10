"""Task19 thin worker orchestration over durable domain/cache control services."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx

from app.services.site_revalidation_control import RevalidationRetryPlan
from app.workers import tasks


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


def test_domain_monitor_uses_bounded_no_redirect_probe_and_durable_control(monkeypatch):
    hospital_id = uuid.uuid4()
    hospital = SimpleNamespace(
        id=hospital_id,
        slug="clinic",
        aeo_domain="Clinic.Example.COM",
    )
    clients = []
    recorded = []

    class Client:
        def __init__(self, *, timeout, follow_redirects):
            assert timeout.connect == 5.0
            assert timeout.read == 10.0
            assert follow_redirects is False
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "hospital_id": str(hospital_id),
                    "slug": "clinic",
                    "canonical_host": "clinic.example.com",
                    "release": "task19",
                },
            )

    async def record(**facts):
        recorded.append(facts)
        return SimpleNamespace(incident_opened=False, incident_recovered=True)

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _Session([hospital]))
    monkeypatch.setattr(tasks.httpx, "Client", Client)
    monkeypatch.setattr(tasks, "record_domain_health_check", record)
    monkeypatch.setattr(
        tasks,
        "_get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("Redis must not hold domain truth")),
    )

    result = tasks.monitor_live_custom_domains.run()

    assert len(clients) == 1
    assert recorded == [
        {
            "hospital_id": hospital_id,
            "canonical_host": "clinic.example.com",
            "healthy": True,
            "safe_reason": "tenant_marker_ok",
        }
    ]
    assert result == {
        "checked": 1,
        "new_failures": 0,
        "recoveries": 1,
        "state_unavailable": 0,
    }


def test_site_revalidation_worker_retries_cache_only_at_control_delay(monkeypatch):
    run_id = uuid.uuid4()
    content_id = uuid.uuid4()
    scheduled = []
    paths_seen = []

    monkeypatch.setattr(
        tasks,
        "_site_revalidation_context",
        lambda _run_id: ("clinic", content_id, [{"name": "허리 통증"}]),
    )

    async def refresh(*, paths):
        paths_seen.extend(paths)
        return False

    async def failed(_run_id):
        return RevalidationRetryPlan(run_id, 300, False)

    monkeypatch.setattr(tasks, "trigger_site_revalidate", refresh)
    monkeypatch.setattr(tasks, "record_retry_failure", failed)
    monkeypatch.setattr(
        tasks.retry_site_revalidation,
        "apply_async",
        lambda *, args, queue, countdown: scheduled.append((args, queue, countdown)),
    )

    result = tasks.retry_site_revalidation.run(str(run_id))

    assert f"/clinic/contents/{content_id}" in paths_seen
    assert scheduled == [([str(run_id)], "default", 300)]
    assert result == {"status": "retry_scheduled", "delay_seconds": 300}


def test_site_revalidation_worker_closes_only_after_observed_refresh(monkeypatch):
    run_id = uuid.uuid4()
    content_id = uuid.uuid4()
    successes = []
    monkeypatch.setattr(
        tasks,
        "_site_revalidation_context",
        lambda _run_id: ("clinic", content_id, []),
    )

    async def refresh(*, paths):
        return bool(paths)

    async def succeeded(observed_run_id):
        successes.append(observed_run_id)
        return True

    monkeypatch.setattr(tasks, "trigger_site_revalidate", refresh)
    monkeypatch.setattr(tasks, "record_revalidation_success", succeeded)

    result = tasks.retry_site_revalidation.run(str(run_id))

    assert result == {"status": "recovered"}
    assert successes == [run_id]
