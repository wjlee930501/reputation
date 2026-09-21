# /// script
# requires-python = ">=3.11"
# dependencies = ["celery", "redis", "sqlalchemy"]
# ///
# How to run: run.sh orchestrates phases inside the disposable production image.
"""Fail-closed assertions against real Redis deliveries and PostgreSQL state."""
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import redis
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.services.public_surface_intents import enqueue_public_surface_intent
from app.workers.canary_tasks import EXPECTED_QUEUES, read_queue_canaries
from app.workers.dispatch_auth import build_dispatch_headers
from app.workers import published_image_refresh as refresh
from scenarios import fencing, occupy_prefix_leases, seed

broker = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                             socket_timeout=5, socket_connect_timeout=5)
REFRESH = "app.workers.published_image_refresh.refresh_reused_content_images"
RECOVER = "app.workers.autonomous_recovery.reconcile"


def evidence(name: str) -> None:
    print(json.dumps({"scenario": name, "status": "assertions_passed",
                      "provenance": "TEST_ONLY"}), flush=True)


def deliver(name: str, purpose: str):
    return celery_app.send_task(name, headers=build_dispatch_headers(purpose))


def wait_key(key: str) -> None:
    deadline = time.monotonic() + 60
    while not broker.exists(key):
        assert time.monotonic() < deadline, f"timeout waiting for {key}"
        time.sleep(0.1)


def smoke() -> None:
    from app.services import openrouter

    assert openrouter.OPENROUTER_BASE_URL == "http://fixture:8090/api/v1"
    deadline = time.monotonic() + 60
    stats = None
    while not stats:
        stats = celery_app.control.inspect(timeout=2).stats()
        assert time.monotonic() < deadline, "worker did not answer inspect"
    for value in stats.values():
        assert value["pool"]["max-concurrency"] == 2
        assert "prefork" in value["pool"]["implementation"]
    registered = celery_app.control.inspect(timeout=5).registered()
    assert registered and all(REFRESH in tasks and RECOVER in tasks
                              for tasks in registered.values())
    for queue in EXPECTED_QUEUES:
        result = deliver(f"app.workers.canary_tasks.canary_{queue}", f"canary-{queue}")
        payload = result.get(timeout=45)
        assert payload["queue"] == queue and payload["task_id"] == result.id
        assert payload["result"] == "ok" and all(payload["checks"].values())
    assert read_queue_canaries().current
    # Publish raw protocol-v2 messages: deliberately do not invoke the signing signal.
    task_id = str(uuid.uuid4())
    message = celery_app.amqp.create_task_message(task_id, REFRESH, args=[], kwargs={})
    with celery_app.connection_for_write() as connection:
        connection.Producer().publish(message.body, headers=message.headers,
                                      **message.properties, exchange="", routing_key="content",
                                      serializer="json", declare=[celery_app.amqp.queues["content"]])
    rejected = celery_app.AsyncResult(task_id)
    rejected.get(timeout=45, propagate=False)
    assert rejected.state == "FAILURE"
    assert "dispatch" in str(rejected.result).lower()
    evidence("prefork_two_seven_signed_canaries_registration_unsigned_rejection")


def initial() -> None:
    item_id = seed()
    fencing(item_id)
    evidence("competing_postgres_claims_stale_success_and_failure_writeback")
    before = int(broker.get("rehearsal:provider_calls") or 0)
    result = deliver(REFRESH, refresh.PUBLISHED_IMAGE_REFRESH_PURPOSE).get(timeout=150)
    assert result == {"replaced": 0, "skipped": 50, "failed": 1}, result
    calls = int(broker.get("rehearsal:provider_calls") or 0)
    assert calls > before, "actual HTTP provider boundary was not reached"
    with SyncSessionLocal() as db:
        item = db.get(ContentItem, item_id)
        assert item is not None and item.image_policy_verified_at is None
        attempt = refresh.read_generation_attempt(item)
        assert attempt["provider_attempt_count"] == 1
        assert item.image_fallback_source == "HOSPITAL_HERO"
        # Fix the due time for a deterministic repeated-delivery check across KST ticks.
        summary = dict(item.essence_check_summary)
        summary[refresh.GENERATION_ATTEMPT_KEY] = {
            **attempt, "next_retry_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()}
        item.essence_check_summary = summary
        db.commit()
    repeated = [deliver(REFRESH, refresh.PUBLISHED_IMAGE_REFRESH_PURPOSE) for _ in range(2)]
    for task in repeated:
        assert task.get(timeout=60) == {"replaced": 0, "skipped": 51, "failed": 0}
    assert int(broker.get("rehearsal:provider_calls") or 0) == calls
    evidence("real_refresh_51st_candidate_repeated_deliveries_retry_gate")
    occupy_prefix_leases(item_id)
    lease_result = deliver(REFRESH, refresh.PUBLISHED_IMAGE_REFRESH_PURPOSE).get(timeout=150)
    assert lease_result == {"replaced": 0, "skipped": 50, "failed": 1}, lease_result
    assert int(broker.get("rehearsal:provider_calls") or 0) > calls
    with SyncSessionLocal() as db:
        target = db.get(ContentItem, item_id)
        assert target is not None
        assert refresh.read_generation_attempt(target)["provider_attempt_count"] == 2
    evidence("fifty_live_leases_do_not_consume_successful_claim_budget")
    broker.set("rehearsal:block_callback", "1")
    with SyncSessionLocal() as db:
        hospital = db.execute(select(Hospital).where(Hospital.slug == "rehearsal-integrity")).scalar_one()
        run = enqueue_public_surface_intent(db, hospital, content_ids=[item_id])
        assert run is not None
        db.commit()
        broker.set("rehearsal:run_id", str(run.id))
    delivered = deliver(RECOVER, "reconcile-autonomous-workflows").get(timeout=60)
    assert delivered["site_revalidations"] == 1
    wait_key("rehearsal:callback_entered")
    evidence("durable_intent_dispatched_callback_blocked_ready_for_worker_kill")


def after_loss() -> None:
    with SyncSessionLocal() as db:
        run = db.get(OperationRun, uuid.UUID(broker.get("rehearsal:run_id")))
        assert run is not None and run.state == "RUNNING"
        assert run.attempt_count == 0
        run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()
    broker.delete("rehearsal:block_callback")
    evidence("worker_loss_preserved_durable_pending_intent")


def recovered() -> None:
    result = deliver(RECOVER, "reconcile-autonomous-workflows").get(timeout=60)
    assert result["site_revalidations"] == 1
    deadline = time.monotonic() + 60
    while True:
        with SyncSessionLocal() as db:
            run = db.get(OperationRun, uuid.UUID(broker.get("rehearsal:run_id")))
            assert run is not None
            if run.state == "SUCCEEDED":
                assert run.result_summary["invalidation_state"] == "ACCEPTED"
                assert run.result_summary["page_visibility_verified"] is False
                break
        assert time.monotonic() < deadline, "recovery did not persist success"
        time.sleep(0.2)
    assert broker.llen("rehearsal:callbacks") >= 2
    assert int(broker.get("rehearsal:callback_accepted") or 0) >= 1
    assert broker.llen("rehearsal:unexpected_requests") == 0
    evidence("real_reconciler_redis_redelivery_callback_acceptance_durable_recovery")


if __name__ == "__main__":
    {"smoke": smoke, "initial": initial, "after-loss": after_loss,
     "recovered": recovered}[sys.argv[1]]()
