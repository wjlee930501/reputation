# /// script
# requires-python = ">=3.11"
# dependencies = ["celery", "pypdf", "redis", "sqlalchemy", "weasyprint"]
# ///
"""Fail-closed assertions for each named mixed-version rehearsal phase."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from harnesslib import (
    count_matching_revalidation_callbacks,
    matching_revalidation_callback_indexes,
    revalidation_effect_digest,
)

EXPECTED_QUEUES = ("control", "default", "content", "sov", "reports", "leadgen", "certificates")


def emit(scenario: str, **observed: Any) -> None:
    print(
        json.dumps(
            {"scenario": scenario, "status": "assertions_passed", "observed": observed},
            ensure_ascii=False,
        ),
        flush=True,
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_json(url: str, expected: int = 200) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            code, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read()
    if code != expected:
        raise AssertionError(f"{url}: expected {expected}, got {code}: {raw[:500]!r}")
    value = json.loads(raw or b"{}")
    if not isinstance(value, dict):
        raise AssertionError(f"{url}: expected JSON object")
    return code, value


def request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected: int = 200,
) -> tuple[int, dict[str, Any]]:
    encoded = json.dumps(payload or {}).encode()
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            code, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read()
    if code != expected:
        raise AssertionError(f"{method} {url}: expected {expected}, got {code}: {raw[:1000]!r}")
    value = json.loads(raw or b"{}")
    if not isinstance(value, dict):
        raise AssertionError(f"{method} {url}: expected JSON object")
    return code, value


def request_status(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected: int,
) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", **(headers or {})},
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            code, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read()
    if code != expected:
        raise AssertionError(f"{method} {url}: expected {expected}, got {code}: {raw[:1000]!r}")
    return code, raw


def admin_write_headers() -> dict[str, str]:
    email = "qa.owner@example.test"
    now_ms = int(time.time() * 1000)
    payload = {"email": email, "role": "OWNER", "iat": now_ms, "exp": now_ms + 120_000, "nonce": "task16"}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signed = f"v1.{encoded}"
    secret = "test-only-actor-000000000000000000000000000000"
    signature = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Admin-Key": "test-only-admin-000000000000000000000000000000",
        "X-Admin-Actor": email,
        "X-Admin-Actor-Assertion": f"{signed}.{signature}",
    }


def image_identities(args: argparse.Namespace) -> None:
    from harnesslib import ImageIdentity, assert_distinct, read_json

    artifacts = Path(args.artifacts)
    identities: list[ImageIdentity] = []
    sources: dict[str, str] = {}
    for role, tag in (("old", args.old), ("new", args.new), ("compatible", args.compatible)):
        inspect = read_json(artifacts / f"{role}-image-inspect.json")
        source = (artifacts / f"source-commit-{role}.txt").read_text(encoding="utf-8").strip()
        labels = inspect.get("Config", {}).get("Labels", {}) or {}
        if labels.get("org.opencontainers.image.revision") != source:
            raise AssertionError(f"{role}: OCI revision label does not match source SHA")
        image_id = str(inspect.get("Id", ""))
        identities.append(
            ImageIdentity(role=role, source_sha=source, tag=tag, image_id=image_id, digest=image_id)
        )
        identities[-1].validate()
        sources[role] = source
    assert_distinct(identities)
    write_json(
        artifacts / "image-identities.json",
        {identity.role: asdict(identity) for identity in identities},
    )
    emit("old_new_compatible_images_are_distinct_and_source_labeled", sources=sources)


def db_objects() -> tuple[Any, Any]:
    import redis

    from app.core.database import SyncSessionLocal

    broker = redis.Redis.from_url(
        os.environ["REDIS_URL"], decode_responses=True, socket_timeout=5, socket_connect_timeout=5
    )
    return SyncSessionLocal, broker


def migration() -> None:
    from sqlalchemy import text

    session_factory, _ = db_objects()
    with session_factory() as db:
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        functions = db.execute(
            text("SELECT count(*) FROM pg_proc WHERE proname='reconcile_content_revisions'")
        ).scalar_one()
    assert head == "0086_preserve_historical_publications", head
    assert functions == 1
    emit("expand_migration_head_and_reconciliation_function", head=head)


def baseline_characterize() -> None:
    from scenarios import seed_mixed_version

    manifest = seed_mixed_version()
    # The original baseline is expected to hide this input because it still has image,
    # schedule, and reference-age gates. That is characterization, never rollback proof.
    path = f"/api/v1/public/hospitals/{manifest['slug']}/contents/{manifest['contentId']}"
    code, _ = get_json(f"http://api-old:8000{path}", expected=404)
    manifest["baselinePublicStatus"] = code
    write_json(Path("/artifacts/fixture-manifest.json"), manifest)
    emit("baseline_original_input_characterization_only", status=code, contentId=manifest["contentId"])


def rollback_smoke_seed() -> None:
    from scenarios import seed_mixed_version

    manifest = seed_mixed_version()
    write_json(Path("/artifacts/fixture-manifest.json"), manifest)
    emit("rollback_site_smoke_fixture_seeded", hospitalId=manifest["hospitalId"], contentId=manifest["contentId"])


def legacy_mutations() -> None:
    from sqlalchemy import text

    from app.core.celery_app import celery_app
    from app.workers.dispatch_auth import build_dispatch_headers

    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    hospital_id = manifest["hospitalId"]
    ids = manifest["legacyIds"]
    root = f"http://api-old:8000/api/v1/admin/hospitals/{hospital_id}/content"
    headers = admin_write_headers()
    request_json(
        f"{root}/{ids['edit']}",
        method="PATCH",
        payload={"body": "legacy-edit-body-after-old-api " * 100},
        headers=headers,
    )
    request_json(
        f"{root}/{ids['publish']}/publish", method="POST", payload={}, headers=headers
    )
    request_json(
        f"{root}/{ids['withdraw']}/withhold",
        method="POST",
        payload={"reason": "task16 legacy withdraw"},
        headers=headers,
    )
    request_json(
        f"{root}/{ids['reject']}/reject",
        method="POST",
        payload={"reason": "task16 legacy reject"},
        headers=headers,
    )
    regeneration = celery_app.send_task(
        "app.workers.tasks.regenerate_content_item",
        args=[ids["regenerate"]],
        headers=build_dispatch_headers("regenerate-content", ids["regenerate"]),
    )
    regeneration.get(timeout=300, propagate=False)
    session_factory, _ = db_objects()
    with session_factory() as db:
        rows = db.execute(
            text(
                "SELECT id::text, status::text, title, body, active_revision_id "
                "FROM content_items WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": list(ids.values())},
        ).mappings().all()
    indexed = {row["id"]: row for row in rows}
    assert "legacy-edit-body-after-old-api" in indexed[ids["edit"]]["body"]
    assert indexed[ids["publish"]]["status"] == "PUBLISHED"
    assert indexed[ids["withdraw"]]["status"] == "WITHHELD"
    assert indexed[ids["reject"]]["status"] == "REJECTED" and indexed[ids["reject"]]["body"] is None
    regenerated = indexed[ids["regenerate"]]
    assert regenerated["title"] and regenerated["body"], regenerated
    # Old writers cannot maintain the new pointer; post-drain reconciliation owns it.
    assert all(row["active_revision_id"] is None for row in rows)
    with session_factory() as db:
        db.execute(text("UPDATE hospitals SET schedule_set=false WHERE id=:id"), {"id": hospital_id})
        db.execute(text("UPDATE content_schedules SET is_active=false WHERE hospital_id=:id"), {"id": hospital_id})
        db.commit()
    evidence = {
        "edit": indexed[ids["edit"]]["status"],
        "publish": indexed[ids["publish"]]["status"],
        "withdraw": indexed[ids["withdraw"]]["status"],
        "reject": indexed[ids["reject"]]["status"],
        "regenerate": regenerated["status"],
        "regenerateTaskId": regeneration.id,
    }
    write_json(Path("/artifacts/legacy-mutation-matrix.json"), evidence)
    emit("old_api_and_worker_legacy_mutation_matrix", **evidence)


def writer_canaries() -> None:
    from app.core.celery_app import celery_app
    from app.workers.canary_tasks import read_queue_canaries
    from app.workers.dispatch_auth import build_dispatch_headers

    stats = None
    deadline = time.monotonic() + 45
    while not stats:
        stats = celery_app.control.inspect(timeout=2).stats()
        if time.monotonic() >= deadline:
            raise AssertionError("new worker did not answer inspect")
    observed: dict[str, str] = {}
    for queue in EXPECTED_QUEUES:
        result = celery_app.send_task(
            f"app.workers.canary_tasks.canary_{queue}",
            headers=build_dispatch_headers(f"task16-canary-{queue}"),
        )
        payload = result.get(timeout=45)
        assert payload["queue"] == queue and payload["result"] == "ok"
        observed[queue] = result.id
    assert read_queue_canaries().current
    write_json(Path("/artifacts/queue-canaries.json"), observed)
    emit("seven_signed_queue_canaries", queues=list(observed))


def old_queue_prime() -> None:
    from app.core.celery_app import celery_app
    from app.workers.dispatch_auth import build_dispatch_headers

    _, broker = db_objects()
    task = celery_app.send_task(
        "app.workers.canary_tasks.canary_default",
        headers=build_dispatch_headers("canary-default"),
    )
    broker.set("rehearsal:old_queued_task_id", task.id)
    emit("old_image_signed_message_queued_for_new_worker", taskId=task.id)


def old_queue_drain() -> None:
    from app.core.celery_app import celery_app

    _, broker = db_objects()
    task_id = broker.get("rehearsal:old_queued_task_id")
    assert task_id
    payload = celery_app.AsyncResult(task_id).get(timeout=60)
    assert payload["queue"] == "default" and payload["result"] == "ok"
    result = {"taskId": task_id, "queue": payload["queue"], "result": payload["result"]}
    write_json(Path("/artifacts/old-queued-message.json"), result)
    emit("new_worker_reads_old_signed_queued_message", **result)


def worker_loss_prime() -> None:
    from app.core.celery_app import celery_app
    from app.core.database import SyncSessionLocal
    from app.models.hospital import Hospital
    from app.models.operations import OperationRun
    from app.services.public_surface_intents import enqueue_public_surface_intent
    from app.workers.tasks import _site_revalidation_context
    from app.workers.dispatch_auth import build_dispatch_headers

    _, broker = db_objects()
    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    broker.delete("rehearsal:callback_entered")
    broker.set("rehearsal:block_callback", "1")
    callback_start = broker.llen("rehearsal:callbacks")
    broker.set("rehearsal:loss_callback_start_index", callback_start)
    with SyncSessionLocal() as db:
        hospital = db.get(Hospital, uuid.UUID(manifest["hospitalId"]))
        assert hospital is not None
        run = enqueue_public_surface_intent(
            db, hospital, content_ids=[uuid.UUID(manifest["contentId"])]
        )
        assert run is not None
        db.commit()
        expected_paths = _site_revalidation_context(run.id, run.attempt_count)
        assert expected_paths is not None
        broker.set("rehearsal:loss_run_id", str(run.id))
        broker.set("rehearsal:loss_expected_paths", json.dumps(expected_paths))
    task = celery_app.send_task(
        "app.workers.autonomous_recovery.reconcile",
        headers=build_dispatch_headers("reconcile-autonomous-workflows"),
    )
    task.get(timeout=60)
    deadline = time.monotonic() + 60
    matched_index: int | None = None
    while matched_index is None:
        callbacks = broker.lrange("rehearsal:callbacks", callback_start, -1)
        matches = matching_revalidation_callback_indexes(callbacks, expected_paths)
        if matches:
            matched_index = callback_start + matches[0]
            break
        assert time.monotonic() < deadline, "exact run-bound callback never entered before worker loss"
        time.sleep(0.1)
    with SyncSessionLocal() as db:
        in_flight_run = db.get(OperationRun, run.id)
        assert in_flight_run is not None
        in_flight_state = getattr(in_flight_run.state, "value", in_flight_run.state)
        assert in_flight_state == "RUNNING"
        in_flight_attempt = in_flight_run.attempt_count
    active = celery_app.control.inspect(timeout=3).active() or {}
    matching_active = []
    for node, tasks in active.items():
        for active_task in tasks:
            if active_task.get("name") != "app.workers.tasks.retry_site_revalidation":
                continue
            args = active_task.get("args")
            if isinstance(args, tuple):
                args = list(args)
            if args == [str(run.id), in_flight_attempt]:
                matching_active.append(
                    {"node": node, "taskId": active_task.get("id"), "args": args}
                )
    assert len(matching_active) == 1, matching_active
    broker.set("rehearsal:loss_matched_callback_index", matched_index)
    broker.set("rehearsal:loss_inflight_task_id", matching_active[0]["taskId"])
    emit(
        "exact_run_bound_callback_is_running_before_sigkill",
        operationRunId=str(run.id),
        callbackCursor=callback_start,
        matchedCallbackIndex=matched_index,
        expectedEffectDigest=revalidation_effect_digest(expected_paths),
        operationState=in_flight_state,
        inFlightTask=matching_active[0],
    )


def worker_loss_observe() -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.database import SyncSessionLocal
    from app.models.operations import OperationRun

    _, broker = db_objects()
    run_id = uuid.UUID(broker.get("rehearsal:loss_run_id"))
    with SyncSessionLocal() as db:
        run = db.get(OperationRun, run_id)
        assert run is not None and str(run.state) in {"OperationRunState.RUNNING", "RUNNING"}
        run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()
    broker.delete("rehearsal:block_callback")
    emit("sigkill_preserves_recoverable_durable_run", operationRunId=str(run_id))


def worker_loss_recover() -> None:
    from app.core.celery_app import celery_app
    from app.core.database import SyncSessionLocal
    from app.models.operations import OperationRun
    from app.workers.dispatch_auth import build_dispatch_headers
    from sqlalchemy import text

    _, broker = db_objects()
    run_id = uuid.UUID(broker.get("rehearsal:loss_run_id"))
    result = celery_app.send_task(
        "app.workers.autonomous_recovery.reconcile",
        headers=build_dispatch_headers("reconcile-autonomous-workflows"),
    ).get(timeout=90)
    deadline = time.monotonic() + 60
    while True:
        with SyncSessionLocal() as db:
            run = db.get(OperationRun, run_id)
            assert run is not None
            state = getattr(run.state, "value", run.state)
            if state == "SUCCEEDED":
                break
        assert time.monotonic() < deadline, "replacement worker did not finish durable run"
        time.sleep(0.2)
    expected_paths = json.loads(broker.get("rehearsal:loss_expected_paths"))
    callback_start = int(broker.get("rehearsal:loss_callback_start_index"))
    callbacks_before = broker.lrange("rehearsal:callbacks", callback_start, -1)
    matching_before = count_matching_revalidation_callbacks(callbacks_before, expected_paths)
    assert matching_before >= 2, "replacement worker did not redeliver the killed run-bound callback"

    def durable_snapshot() -> dict[str, Any]:
        with SyncSessionLocal() as db:
            run_json = db.execute(
                text("SELECT to_jsonb(r)::text FROM operation_runs r WHERE id=:id"),
                {"id": run_id},
            ).scalar_one()
            counts = db.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM content_revisions) content_revisions, "
                    "(SELECT count(*) FROM notification_outbox) notification_outbox, "
                    "(SELECT count(*) FROM monthly_delivery_events) monthly_delivery_events"
                )
            ).mappings().one()
        return {
            "operationRunSha256": hashlib.sha256(run_json.encode()).hexdigest(),
            "counts": {key: int(value) for key, value in counts.items()},
        }

    with SyncSessionLocal() as db:
        terminal_run = db.get(OperationRun, run_id)
        assert terminal_run is not None
        terminal_attempt = terminal_run.attempt_count
        terminal_result = terminal_run.result_summary
    durable_before = durable_snapshot()
    callbacks_total_before = broker.llen("rehearsal:callbacks")

    duplicate_task = celery_app.send_task(
        "app.workers.tasks.retry_site_revalidation",
        args=[str(run_id), terminal_attempt],
        queue="control",
        headers=build_dispatch_headers("retry-site-revalidation", str(run_id)),
    )
    duplicate_result = duplicate_task.get(timeout=60)
    assert duplicate_result == {"status": "stale_run"}

    durable_after = durable_snapshot()
    callbacks_total_after = broker.llen("rehearsal:callbacks")
    callbacks_after = broker.lrange("rehearsal:callbacks", callback_start, -1)
    matching_after = count_matching_revalidation_callbacks(callbacks_after, expected_paths)
    assert callbacks_total_after == callbacks_total_before
    assert matching_after == matching_before
    assert durable_after == durable_before
    evidence = {
        "operationRunId": str(run_id),
        "killedInFlightTaskId": broker.get("rehearsal:loss_inflight_task_id"),
        "callbackCursor": callback_start,
        "killedInFlightCallbackIndex": int(
            broker.get("rehearsal:loss_matched_callback_index")
        ),
        "terminalAttemptCount": terminal_attempt,
        "terminalResult": terminal_result,
        "expectedEffectDigest": revalidation_effect_digest(expected_paths),
        "preSuccessRawCallbackAttempts": matching_before,
        "preSuccessRedeliverySemantics": "bounded_nonmutating_cache_revalidation",
        "duplicateDeliveryTaskId": duplicate_task.id,
        "duplicateDeliveryResult": duplicate_result,
        "postSuccessCallbackDelta": callbacks_total_after - callbacks_total_before,
        "postSuccessMatchingCallbackDelta": matching_after - matching_before,
        "postSuccessDurableEffectDelta": {
            key: durable_after["counts"][key] - durable_before["counts"][key]
            for key in durable_before["counts"]
        },
        "operationRunUnchanged": durable_after["operationRunSha256"]
        == durable_before["operationRunSha256"],
        "durableBefore": durable_before,
        "durableAfter": durable_after,
        "reconcile": result,
    }
    write_json(Path("/artifacts/worker-loss-recovery.json"), evidence)
    emit("durable_success_suppresses_exact_duplicate_delivery_side_effects", **evidence)


def synthetic_flow() -> None:
    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    path = f"/api/v1/public/hospitals/{manifest['slug']}/contents/{manifest['contentId']}"
    code, payload = get_json(f"http://api-new:8000{path}")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert manifest["oldBodyMarker"] in serialized
    assert "image_url" in serialized and payload.get("image_url") is None
    aged_path = (
        f"/api/v1/public/hospitals/{manifest['slug']}/contents/"
        f"{manifest['agedContentId']}"
    )
    aged_code, aged_payload = get_json(f"http://api-new:8000{aged_path}")
    assert "오래된 참고자료 검증 시각" in json.dumps(aged_payload, ensure_ascii=False)
    get_json("http://api-new:8000/api/v1/public/hospitals/not-this-tenant", expected=404)
    session_factory, _ = db_objects()
    from sqlalchemy import text

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT h.schedule_set, ci.image_url, ci.pending_revision, "
                "cr.reference_checks FROM content_items ci JOIN hospitals h ON h.id=ci.hospital_id "
                "JOIN content_revisions cr ON cr.id=ci.active_revision_id WHERE ci.id=:id"
            ),
            {"id": manifest["agedContentId"]},
        ).one()
    assert row.schedule_set is False and row.image_url is None and row.pending_revision is None
    assert row.reference_checks[0]["verdict"] == "pass"
    emit(
        "new_public_reader_accepts_no_image_scheduleless_aged_approved_reference",
        status=code,
        agedReferenceStatus=aged_code,
        activeBody=True,
    )


def old_drained() -> None:
    from app.core.celery_app import celery_app

    inspect = celery_app.control.inspect(timeout=3)
    active = inspect.active() or {}
    reserved = inspect.reserved() or {}
    scheduled = inspect.scheduled() or {}
    old_nodes = [name for name in set(active) | set(reserved) | set(scheduled) if "old" in name]
    assert not old_nodes, old_nodes
    emit("baseline_writer_zero_instances_and_inflight", oldNodes=old_nodes)


def _run_json_cli(module: str) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, "-m", module],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            f"{module}: expected one JSON line, got stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    payload = json.loads(lines[0])
    if not isinstance(payload, dict):
        raise AssertionError(f"{module}: expected JSON object")
    return result.returncode, payload


def legacy_incident_reconciliation() -> None:
    from sqlalchemy import text

    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    incident_ids = manifest["legacyIncidentIds"]
    session_factory, _ = db_objects()
    with session_factory() as db:
        before_incidents = db.execute(
            text(
                "SELECT id::text, incident_type, state::text, recovered_at, operation_run_id::text "
                "FROM incidents WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
            ),
            {"ids": incident_ids},
        ).mappings().all()
        before_runs = db.execute(
            text(
                "SELECT id::text FROM operation_runs WHERE id BETWEEN "
                "'16000000-0000-0000-0000-000000000501'::uuid AND "
                "'16000000-0000-0000-0000-000000000510'::uuid ORDER BY id"
            )
        ).scalars().all()
        outbox_before = {
            row_id: hashlib.sha256(payload.encode()).hexdigest()
            for row_id, payload in db.execute(
                text("SELECT id::text, to_jsonb(o)::text FROM notification_outbox o ORDER BY id")
            ).all()
        }
    assert len(before_incidents) == 7 and len(before_runs) == 10

    pre_code, preflight_before = _run_json_cli(
        "app.utils.legacy_publish_retirement_preflight"
    )
    assert pre_code == 1 and preflight_before["status"] == "BLOCKED"
    assert preflight_before["convertible_legacy_incidents"] == 6
    assert preflight_before["unknown_legacy_incidents"] == 1

    first_code, first = _run_json_cli("app.utils.reconcile_legacy_task_incidents")
    second_code, second = _run_json_cli("app.utils.reconcile_legacy_task_incidents")
    assert first_code == second_code == 0
    assert first == {
        "status": "APPLIED",
        "converted": 3,
        "superseded": 1,
        "recovered": 2,
        "unknown": 1,
    }
    assert second == {
        "status": "APPLIED",
        "converted": 0,
        "superseded": 0,
        "recovered": 0,
        "unknown": 1,
    }
    post_code, preflight_after = _run_json_cli(
        "app.utils.legacy_publish_retirement_preflight"
    )
    assert post_code == 0 and preflight_after["status"] == "READY"
    assert preflight_after["convertible_legacy_incidents"] == 0
    assert preflight_after["unknown_legacy_incidents"] == 1

    with session_factory() as db:
        after = db.execute(
            text(
                "SELECT id::text, incident_type, state::text, recovered_at, operation_run_id::text "
                "FROM incidents WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
            ),
            {"ids": incident_ids},
        ).mappings().all()
        runs_after = db.execute(
            text(
                "SELECT id::text FROM operation_runs WHERE id BETWEEN "
                "'16000000-0000-0000-0000-000000000501'::uuid AND "
                "'16000000-0000-0000-0000-000000000510'::uuid ORDER BY id"
            )
        ).scalars().all()
        audits = db.execute(
            text(
                "SELECT action, count(*) FROM admin_audit_logs "
                "WHERE target_id = ANY(CAST(:ids AS text[])) GROUP BY action ORDER BY action"
            ),
            {"ids": incident_ids},
        ).all()
        outbox_after = {
            row_id: hashlib.sha256(payload.encode()).hexdigest()
            for row_id, payload in db.execute(
                text("SELECT id::text, to_jsonb(o)::text FROM notification_outbox o ORDER BY id")
            ).all()
        }
    indexed = {row["id"]: row for row in after}
    duplicate_rows = [indexed[incident_ids[0]], indexed[incident_ids[1]]]
    assert sorted(row["state"] for row in duplicate_rows) == ["ACKNOWLEDGED", "OPEN"]
    superseded = next(row for row in duplicate_rows if row["state"] == "ACKNOWLEDGED")
    assert superseded["recovered_at"] is None
    same_period = indexed[incident_ids[2]]
    assert same_period["state"] == "ACKNOWLEDGED" and same_period["recovered_at"] is not None
    different_month = indexed[incident_ids[3]]
    assert different_month["state"] == "OPEN"
    assert different_month["incident_type"] == "OPERATION_TERMINAL_FAILED"
    unknown = indexed[incident_ids[4]]
    assert unknown["state"] == "OPEN" and unknown["incident_type"] == "BACKGROUND_TASK_FAILED"
    failure_before_success = indexed[incident_ids[5]]
    assert (
        failure_before_success["state"] == "ACKNOWLEDGED"
        and failure_before_success["recovered_at"] is not None
    )
    failure_after_success = indexed[incident_ids[6]]
    assert failure_after_success["state"] == "OPEN"
    assert failure_after_success["incident_type"] == "OPERATION_TERMINAL_FAILED"
    assert [row["id"] for row in before_incidents] == [row["id"] for row in after]
    assert list(before_runs) == list(runs_after)
    assert dict(audits) == {
        "legacy_operation_incident_converted": 3,
        "legacy_operation_incident_recovered": 2,
        "legacy_operation_incident_superseded": 1,
    }
    assert outbox_after == outbox_before
    evidence = {
        "preflightBefore": preflight_before,
        "firstRun": first,
        "secondRun": second,
        "preflightAfter": preflight_after,
        "incidentIdsPreserved": incident_ids,
        "operationRunIdsPreserved": list(runs_after),
        "auditCounts": dict(audits),
        "outboxRowsUnchanged": True,
        "duplicateSupersessionRecoveredAt": superseded["recovered_at"],
        "samePeriodSuccessRecovered": True,
        "differentMonthRemainsOpen": True,
        "failureAfterOlderSuccessRemainsOpen": True,
        "unknownRemainsOpen": True,
    }
    write_json(Path("/artifacts/legacy-incident-reconciliation.json"), evidence)
    emit("legacy_incident_reconciliation_is_truthful_and_idempotent", **evidence)


def post_drain_reconcile() -> None:
    from sqlalchemy import text

    session_factory, _ = db_objects()
    with session_factory() as db:
        result = db.execute(text("SELECT * FROM reconcile_content_revisions()" )).one()
        db.commit()
        mismatches = db.execute(
            text(
                "SELECT count(*) FROM content_items ci JOIN content_revisions cr "
                "ON cr.id=ci.active_revision_id WHERE ci.title IS DISTINCT FROM cr.title "
                "OR ci.body IS DISTINCT FROM cr.body OR ci.references_list IS DISTINCT FROM cr.references_list"
            )
        ).scalar_one()
    assert mismatches == 0
    emit(
        "post_drain_reconciliation_is_repeatable_and_mirrors_match",
        created=result.created_count,
        cleared=result.cleared_count,
        unchanged=result.unchanged_count,
        mismatches=mismatches,
    )


def switch() -> None:
    from sqlalchemy import text

    session_factory, _ = db_objects()
    with session_factory() as db:
        totals = db.execute(
            text(
                "SELECT count(*) FILTER (WHERE active_revision_id IS NOT NULL) active, "
                "count(*) FILTER (WHERE active_revision_id IS NOT NULL AND status::text='PUBLISHED') published "
                "FROM content_items"
            )
        ).one()
        mismatch = db.execute(
            text(
                "SELECT count(*) FROM content_items ci JOIN content_revisions cr "
                "ON cr.id=ci.active_revision_id WHERE ci.title IS DISTINCT FROM cr.title "
                "OR ci.body IS DISTINCT FROM cr.body"
            )
        ).scalar_one()
    assert totals.active > 0 and mismatch == 0
    manifest = {"active": totals.active, "published": totals.published, "mismatch": mismatch}
    write_json(Path("/artifacts/parity-manifest.json"), manifest)
    emit("pointer_switch_requires_full_parity", **manifest)


def budget_reset() -> None:
    from sqlalchemy import text

    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    content_id = manifest["budgetContentId"]
    session_factory, _ = db_objects()
    with session_factory() as db:
        summary = db.execute(
            text("SELECT essence_check_summary FROM content_items WHERE id=:id"),
            {"id": content_id},
        ).scalar_one()
        audits = db.execute(
            text(
                "SELECT actor, action, target_type, target_id, detail "
                "FROM admin_audit_logs WHERE action='LEGACY_BUDGET_REPLACED' "
                "AND target_id=:id ORDER BY created_at"
            ),
            {"id": content_id},
        ).mappings().all()
    budget = summary["generation_attempt"]["budget"]
    reset = budget["reset_record"]
    assert budget["legacy_state"] == "REPLACED"
    assert reset["actor"] == "qa.owner@example.test"
    assert reset["reason"] == "Task 16 격리 리허설에서 이전 사용량을 확인할 수 없어 교체합니다."
    assert isinstance(reset["idempotency_key"], str) and reset["idempotency_key"]
    assert len(audits) == 1
    audit = dict(audits[0])
    assert audit["actor"] == "qa.owner@example.test"
    assert audit["target_type"] == "content_item" and audit["target_id"] == content_id
    evidence = {"budget": budget, "audit": audit, "auditCount": len(audits)}
    write_json(Path("/artifacts/legacy-budget-reset.json"), evidence)
    emit("legacy_budget_owner_click_audited_once", actor=audit["actor"], auditCount=1)


def _immutable_row_digests() -> dict[str, dict[str, str]]:
    from sqlalchemy import text

    session_factory, _ = db_objects()
    result: dict[str, dict[str, str]] = {}
    queries = {
        "content_revisions": "SELECT id::text, to_jsonb(cr)::text FROM content_revisions cr ORDER BY id",
        "monthly_report_artifacts": (
            "SELECT id::text, to_jsonb(a)::text FROM monthly_report_artifacts a ORDER BY id"
        ),
        "monthly_delivery_events": (
            "SELECT id::text, to_jsonb(e)::text FROM monthly_delivery_events e ORDER BY id"
        ),
    }
    with session_factory() as db:
        for table, query in queries.items():
            rows = db.execute(text(query)).all()
            result[table] = {
                row_id: hashlib.sha256(payload.encode()).hexdigest()
                for row_id, payload in rows
            }
    return result


def negative_boundaries() -> None:
    from sqlalchemy import text

    artifacts = Path("/artifacts")
    manifest = json.loads((artifacts / "fixture-manifest.json").read_text(encoding="utf-8"))
    hospital_id = manifest["hospitalId"]
    slug = manifest["slug"]
    content_id = manifest["contentId"]
    other_hospital_id = manifest["otherHospitalId"]
    other_slug = manifest["otherSlug"]
    other_content_id = manifest["otherContentId"]
    unsafe_content_id = manifest["unsafeContentId"]
    withdrawn_id = manifest["legacyIds"]["withdraw"]
    report_id = manifest["reportIds"]["COMPLETE"]
    other_report_id = manifest["otherReportId"]
    headers = admin_write_headers()

    _, other_payload = get_json(
        f"http://api-new:8000/api/v1/public/hospitals/{other_slug}/contents/{other_content_id}"
    )
    assert "task16-other-tenant-body" in json.dumps(other_payload, ensure_ascii=False)
    assert (
        f"/hospitals/{other_slug}/contents/{other_content_id}/image"
        in str(other_payload.get("image_url"))
    )
    main_site_status, main_site_html = request_status(
        f"http://reputation.rehearsal.example.test:3000/{slug}/contents/{content_id}",
        expected=200,
    )
    other_site_status, other_site_html = request_status(
        f"http://reputation.rehearsal.example.test:3000/{other_slug}/contents/{other_content_id}",
        expected=200,
    )
    assert b"task16-active-body" in main_site_html
    assert b"task16-other-tenant-body" in other_site_html
    main_slug_other_site_status, main_slug_other_site_body = request_status(
        f"http://reputation.rehearsal.example.test:3000/{slug}/contents/{other_content_id}",
        expected=404,
    )
    other_slug_main_site_status, other_slug_main_site_body = request_status(
        f"http://reputation.rehearsal.example.test:3000/{other_slug}/contents/{content_id}",
        expected=404,
    )
    tenant_markers = (b"task16-active-body", b"task16-other-tenant-body")
    assert not any(marker in main_slug_other_site_body for marker in tenant_markers)
    assert not any(marker in other_slug_main_site_body for marker in tenant_markers)
    cross_tenant_content_statuses = [
        get_json(
            f"http://api-new:8000/api/v1/public/hospitals/{slug}/contents/{other_content_id}",
            expected=404,
        )[0],
        get_json(
            f"http://api-new:8000/api/v1/public/hospitals/{other_slug}/contents/{content_id}",
            expected=404,
        )[0],
        main_slug_other_site_status,
        other_slug_main_site_status,
    ]
    unknown_api_status, _ = get_json(
        "http://api-new:8000/api/v1/public/hospitals/rehearsal-unknown-clinic",
        expected=404,
    )
    unknown_site_status, unknown_site_body = request_status(
        "http://reputation.rehearsal.example.test:3000/rehearsal-unknown-clinic",
        expected=404,
    )
    assert not any(marker in unknown_site_body for marker in tenant_markers)
    cross_tenant_report_status, _ = request_status(
        f"http://api-new:8000/api/v1/admin/hospitals/{hospital_id}/reports/{other_report_id}",
        headers=headers,
        expected=404,
    )
    cross_tenant_image_statuses = [
        request_status(
            f"http://api-new:8000/api/v1/public/hospitals/{slug}/contents/{other_content_id}/image",
            expected=404,
        )[0],
        request_status(
            f"http://api-new:8000/api/v1/public/hospitals/{other_slug}/contents/{content_id}/image",
            expected=404,
        )[0],
    ]

    unsafe_status, unsafe_payload = get_json(
        f"http://api-new:8000/api/v1/public/hospitals/{slug}/contents/{unsafe_content_id}"
    )
    unsafe_serialized = json.dumps(unsafe_payload, ensure_ascii=False)
    assert "task16-unsafe-image-body" in unsafe_serialized
    assert unsafe_payload.get("image_url") is None and "javascript:" not in unsafe_serialized
    unsafe_site_status, unsafe_site_html = request_status(
        f"http://reputation.rehearsal.example.test:3000/{slug}/contents/{unsafe_content_id}",
        expected=200,
    )
    assert b"task16-unsafe-image-body" in unsafe_site_html
    assert b"javascript:" not in unsafe_site_html.lower()

    withdrawn_api_status, _ = get_json(
        f"http://api-new:8000/api/v1/public/hospitals/{slug}/contents/{withdrawn_id}",
        expected=404,
    )
    withdrawn_site_status, _ = request_status(
        f"http://reputation.rehearsal.example.test:3000/{slug}/contents/{withdrawn_id}",
        expected=404,
    )

    session_factory, _ = db_objects()
    with session_factory() as db:
        events_before = db.execute(text("SELECT count(*) FROM monthly_delivery_events")).scalar_one()
    wrong_hash_status, wrong_hash_body = request_status(
        f"http://api-new:8000/api/v1/admin/hospitals/{hospital_id}/reports/{report_id}/mark-sent",
        method="POST",
        payload={
            "artifact_sha256": "0" * 64,
            "recipient_label": "거부되어야 하는 수신자",
            "channel": "TEST_ONLY",
        },
        headers=headers,
        expected=409,
    )
    assert b"artifact_mismatch" in wrong_hash_body
    wrong_report_status, _ = request_status(
        f"http://api-new:8000/api/v1/admin/hospitals/{hospital_id}/reports/{other_report_id}/mark-sent",
        method="POST",
        payload={
            "artifact_sha256": "0" * 64,
            "recipient_label": "다른 테넌트 보고서",
            "channel": "TEST_ONLY",
        },
        headers=headers,
        expected=404,
    )
    wrong_audience_status, _ = request_status(
        f"http://api-new:8000/api/v1/admin/hospitals/{hospital_id}/reports/{report_id}/download?audience=owner",
        headers=headers,
        expected=422,
    )
    with session_factory() as db:
        events_after = db.execute(text("SELECT count(*) FROM monthly_delivery_events")).scalar_one()
    assert events_after == events_before

    _, broker = db_objects()
    broker.set("rehearsal:forward_revalidation", "1")
    forwarded_before = int(broker.get("rehearsal:revalidation_forwarded") or 0)
    pause_status, _ = request_json(
        f"http://api-new:8000/api/v1/admin/hospitals/{other_hospital_id}/pause",
        method="POST",
        headers=headers,
    )
    forwarded_after_pause = int(broker.get("rehearsal:revalidation_forwarded") or 0)
    assert forwarded_after_pause > forwarded_before
    paused_statuses = [
        get_json(f"http://api-new:8000/api/v1/public/hospitals/{other_slug}", expected=404)[0],
        get_json(
            f"http://api-new:8000/api/v1/public/hospitals/{other_slug}/contents/{other_content_id}",
            expected=404,
        )[0],
        request_status(f"http://reputation.rehearsal.example.test:3000/{other_slug}", expected=404)[0],
        request_status(
            f"http://reputation.rehearsal.example.test:3000/{other_slug}/contents/{other_content_id}",
            expected=404,
        )[0],
    ]
    resume_status, _ = request_json(
        f"http://api-new:8000/api/v1/admin/hospitals/{other_hospital_id}/resume",
        method="POST",
        headers=headers,
    )
    resumed_status = get_json(f"http://api-new:8000/api/v1/public/hospitals/{other_slug}")[0]
    resumed_site_status = request_status(
        f"http://reputation.rehearsal.example.test:3000/{other_slug}", expected=200
    )[0]

    before = _immutable_row_digests()
    assert before["content_revisions"]
    assert before["monthly_report_artifacts"]
    assert before["monthly_delivery_events"]
    write_json(artifacts / "immutable-history-before.json", before)
    evidence = {
        "ownSiteStatuses": [main_site_status, other_site_status],
        "crossTenantContentStatuses": cross_tenant_content_statuses,
        "crossTenantSiteBodiesExcludeTenantMarkers": True,
        "unknownApiStatus": unknown_api_status,
        "unknownSiteStatus": unknown_site_status,
        "unknownSiteBodyExcludesTenantMarkers": True,
        "crossTenantReportStatus": cross_tenant_report_status,
        "crossTenantImageStatuses": cross_tenant_image_statuses,
        "unsafeApiStatus": unsafe_status,
        "unsafeSiteStatus": unsafe_site_status,
        "withdrawnApiStatus": withdrawn_api_status,
        "withdrawnSiteStatus": withdrawn_site_status,
        "wrongHashStatus": wrong_hash_status,
        "wrongReportStatus": wrong_report_status,
        "wrongAudienceStatus": wrong_audience_status,
        "deliveryEventDelta": events_after - events_before,
        "pauseStatus": pause_status,
        "pausedPublicStatuses": paused_statuses,
        "resumeStatus": resume_status,
        "resumedPublicStatus": resumed_status,
        "resumedSiteStatus": resumed_site_status,
        "actualSiteRevalidationForwarded": forwarded_after_pause - forwarded_before,
        "immutableCounts": {table: len(rows) for table, rows in before.items()},
    }
    write_json(artifacts / "negative-boundaries.json", evidence)
    emit("real_tenant_pause_withdraw_image_and_report_negatives", **evidence)


def immutable_history() -> None:
    artifacts = Path("/artifacts")
    before = json.loads((artifacts / "immutable-history-before.json").read_text(encoding="utf-8"))
    after = _immutable_row_digests()
    appended: dict[str, list[str]] = {}
    for table, original_rows in before.items():
        current_rows = after[table]
        changed = {
            row_id: {"before": digest, "after": current_rows.get(row_id)}
            for row_id, digest in original_rows.items()
            if current_rows.get(row_id) != digest
        }
        assert not changed, {table: changed}
        appended[table] = sorted(set(current_rows) - set(original_rows))
    assert appended["content_revisions"], "candidate PASS must append a revision"
    assert appended["monthly_delivery_events"], "valid delivery must append an event"
    evidence = {
        "preexistingRowsIdentical": True,
        "beforeCounts": {table: len(rows) for table, rows in before.items()},
        "afterCounts": {table: len(rows) for table, rows in after.items()},
        "appendedIds": appended,
    }
    write_json(artifacts / "immutable-history-after.json", evidence)
    emit("preexisting_revision_artifact_and_delivery_history_is_immutable", **evidence)


def candidate_pass() -> None:
    from sqlalchemy import text

    from app.core.celery_app import celery_app
    from app.workers.dispatch_auth import build_dispatch_headers

    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    task = celery_app.send_task(
        "app.workers.post_publish_ai_review.review_post_publish_samples",
        headers=build_dispatch_headers("post-publish-ai-review"),
    )
    result = task.get(timeout=180)
    assert result.get("candidate_published") == 1, result
    session_factory, broker = db_objects()
    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT ci.title, ci.body, ci.pending_revision, ci.active_revision_id, "
                "cr.title revision_title, cr.body revision_body "
                "FROM content_items ci JOIN content_revisions cr ON cr.id=ci.active_revision_id "
                "WHERE ci.id=:id"
            ),
            {"id": manifest["contentId"]},
        ).one()
    assert row.pending_revision is None
    assert row.title == row.revision_title == "검사 전 확인할 준비 사항 승인본"
    assert row.body == row.revision_body and "task16-approved-body" in row.body
    receipts = [json.loads(value) for value in broker.lrange("rehearsal:provider_receipts", 0, -1)]
    assert any(receipt["path"] == "/api/v1/chat/completions" for receipt in receipts)
    path = f"/api/v1/public/hospitals/{manifest['slug']}/contents/{manifest['contentId']}"
    _, payload = get_json(f"http://api-new:8000{path}")
    assert "task16-approved-body" in json.dumps(payload, ensure_ascii=False)
    evidence = {
        "taskId": task.id,
        "workerResult": result,
        "activeRevisionId": str(row.active_revision_id),
        "providerReceiptCount": len(receipts),
    }
    write_json(Path("/artifacts/candidate-pass.json"), evidence)
    emit("provider_driven_candidate_pass_swaps_pointer_and_mirror", **evidence)


def report_delivery() -> None:
    from sqlalchemy import text

    session_factory, _ = db_objects()
    with session_factory() as db:
        rows = db.execute(
            text(
                "SELECT e.report_id, e.artifact_id, e.recipient, e.metadata, "
                "r.hospital_id, a.report_id artifact_report_id, a.sha256, a.path, a.byte_size "
                "FROM monthly_delivery_events e "
                "JOIN monthly_reports r ON r.id=e.report_id "
                "JOIN monthly_report_artifacts a ON a.id=e.artifact_id "
                "WHERE r.hospital_id=:hospital_id"
            ),
            {"hospital_id": "16000000-0000-0000-0000-000000000001"},
        ).mappings().all()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["hospital_id"] == uuid.UUID("16000000-0000-0000-0000-000000000001")
    assert row["report_id"] == row["artifact_report_id"]
    assert row["recipient"] == "김바른 원장"
    assert row["metadata"]["channel"] == "대면"
    assert row["metadata"]["artifact_sha256"] == row["sha256"]
    pdf_path = Path(row["path"])
    data = pdf_path.read_bytes()
    assert len(data) == row["byte_size"]
    assert hashlib.sha256(data).hexdigest() == row["sha256"]
    result = {
        "reportId": str(row["report_id"]),
        "artifactId": str(row["artifact_id"]),
        "hospitalId": str(row["hospital_id"]),
        "sha256": row["sha256"],
        "byteSize": row["byte_size"],
        "recipient": row["recipient"],
        "channel": row["metadata"]["channel"],
    }
    write_json(Path("/artifacts/report-delivery-binding.json"), result)
    emit("admin_download_hash_is_bound_to_selected_report_delivery_event", **result)


def pdf() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from pypdf import PdfReader
    from weasyprint import HTML

    from app.models.monthly_control import (
        MonthlyDeliveryEvent,
        MonthlyMeasurementManifest,
        MonthlyReportArtifact,
    )
    from app.models.report import MonthlyReport
    from app.services.doctor_pdf_contracts import DoctorArtifactMetadata
    from app.services.report_artifact_validation import validate_persisted_doctor_artifact

    artifacts = Path("/artifacts")
    assertions: list[dict[str, Any]] = []
    rows: list[tuple[str, Path, bytes, str, int]] = []
    for month, quality, headline in (
        (9, "COMPLETE", "필수 측정 완료"),
        (8, "LIMITED", "일부 측정 한계"),
        (7, "UNAVAILABLE", "측정 결과를 확인할 수 없음"),
    ):
        path = artifacts / f"report-{quality.lower()}.pdf"
        HTML(
            string=(
                "<html lang='ko'><meta charset='utf-8'><body>"
                f"<h1>리허설 바른의원 {quality}</h1><p>{headline}</p>"
                "<p>닫힌 기간 2026년 9월 · 원장 전달용 · TEST ONLY</p>"
                "<a href='https://rehearsal.example.test'>공개 콘텐츠 확인</a></body></html>"
            )
        ).write_pdf(path)
        data = path.read_bytes()
        reader = PdfReader(path)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert len(data) > 1000 and len(reader.pages) >= 1 and quality in extracted
        assertions.append(
            {
                "quality": quality,
                "path": str(path),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "pages": len(reader.pages),
            }
        )
        rows.append((quality, path, data, hashlib.sha256(data).hexdigest(), len(reader.pages)))
    # Prove wrong artifact identity is rejected by the real persisted-artifact
    # validator, rather than comparing two locally generated strings.
    assert assertions[0]["sha256"] != assertions[1]["sha256"]
    session_factory, _ = db_objects()
    hospital_id = uuid.UUID("16000000-0000-0000-0000-000000000001")
    other_hospital_id = uuid.UUID("16000000-0000-0000-0000-000000000002")
    now = datetime.now(UTC)
    report_ids: dict[str, str] = {}
    with session_factory() as db:
        for month, (quality, path, data, digest, page_count) in zip((9, 8, 7), rows, strict=True):
            manifest = MonthlyMeasurementManifest(
                hospital_id=hospital_id,
                period_year=2026,
                period_month=month,
                configured_platforms=["chatgpt", "gemini"],
                platform_provenance={"source": "task16-fixture"},
                closes_at=now,
                closed_at=now,
            )
            db.add(manifest)
            db.flush()
            planned = 2
            success = 2 if quality == "COMPLETE" else 1 if quality == "LIMITED" else 0
            pending = planned - success
            answer_failed = pending
            quality_storage = "COMPLETE" if quality == "COMPLETE" else "DEGRADED"
            platform = {
                "platform": "chatgpt",
                "planned_slots": planned,
                "received_answers": success,
                "confirmed_slots": success,
                "ambiguous_slots": 0,
                "answer_failed_slots": answer_failed,
                "judgment_failed_slots": 0,
                "pending_slots": pending,
                "confirmed_mentioned_count": 1 if success else 0,
                "confirmed_sample_count": success,
            }
            report = MonthlyReport(
                hospital_id=hospital_id,
                period_year=2026,
                period_month=month,
                report_type="MONTHLY",
                manifest_id=manifest.id,
                cutoff_at=now,
                quality=quality_storage,
                planned_count=planned,
                success_count=success,
                failed_count=planned - success,
                excluded_count=0,
                customer_ready=quality == "COMPLETE",
                delivery_blockers=(
                    []
                    if quality == "COMPLETE"
                    else ["MEASUREMENT_LIMITED" if quality == "LIMITED" else "MEASUREMENT_UNAVAILABLE"]
                ),
                pdf_path=str(path),
                doctor_pdf_path=str(path),
                sov_summary={
                    "sov_pct": 50.0 if success else None,
                    "change_pct": None,
                    "observation_adequacy": {
                        "status": quality,
                        "planned_slots": planned,
                        "received_answers": success,
                        "confirmed_slots": success,
                        "ambiguous_slots": 0,
                        "answer_failed_slots": answer_failed,
                        "judgment_failed_slots": 0,
                        "pending_slots": pending,
                        "platforms": [platform],
                    },
                    "comparison": {"status": "NON_COMPARABLE", "reason": "NO_PRIOR_MANIFEST", "change_pct": None},
                    "platforms": [platform],
                    "queries": [],
                },
                content_summary={"published_count": 2, "promised_count": 12, "operations": {"delivery_blockers": [], "delivery_warnings": []}},
                essence_summary={
                    "approved_philosophy_exists": True,
                    "source_count": 1,
                    "processed_source_count": 1,
                    "needs_review_content_count": 0,
                    "missing_philosophy_content_count": 0,
                    "medical_risk_findings": [],
                },
            )
            db.add(report)
            db.flush()
            metadata = DoctorArtifactMetadata(
                validation_version="doctor-pdf-v2",
                validation_source="SYSTEM",
                page_count=page_count,
                page_size="A4",
                glyph_count=max(1, len("".join(PdfReader(path).pages[0].extract_text() or ""))),
                font_family="Pretendard",
                font_embedded=True,
                korean_to_unicode=True,
                link_count=1,
                expected_link_present=True,
                required_text_present=True,
                sha256=digest,
                byte_size=len(data),
            )
            valid_shape = SimpleNamespace(
                report_id=report.id,
                audience="DOCTOR",
                path=str(path),
                sha256=digest,
                byte_size=len(data),
                validated=True,
                validation_metadata=metadata.model_dump(mode="json"),
            )
            assert validate_persisted_doctor_artifact(report, valid_shape).valid
            wrong_shape = SimpleNamespace(**{**vars(valid_shape), "audience": "AE"})
            assert not validate_persisted_doctor_artifact(report, wrong_shape).valid
            wrong_hash_shape = SimpleNamespace(
                **{**vars(valid_shape), "sha256": "0" * 64}
            )
            wrong_hash_validation = validate_persisted_doctor_artifact(report, wrong_hash_shape)
            assert not wrong_hash_validation.valid
            artifact = MonthlyReportArtifact(
                report_id=report.id,
                audience="DOCTOR",
                path=str(path),
                sha256=digest,
                byte_size=len(data),
                validated=True,
                validated_at=now,
                validation_metadata=metadata.model_dump(mode="json"),
            )
            db.add(artifact)
            db.flush()
            report_ids[quality] = str(report.id)
            matching = next(item for item in assertions if item["quality"] == quality)
            matching.update(
                {
                    "reportId": str(report.id),
                    "artifactId": str(artifact.id),
                    "wrongAudienceRejected": True,
                    "wrongHashRejected": True,
                }
            )

        complete = next(row for row in rows if row[0] == "COMPLETE")
        _, complete_path, complete_data, complete_digest, complete_pages = complete
        other_manifest = MonthlyMeasurementManifest(
            hospital_id=other_hospital_id,
            period_year=2026,
            period_month=6,
            configured_platforms=["chatgpt"],
            platform_provenance={"source": "task16-existing-other-tenant"},
            closes_at=now,
            closed_at=now,
        )
        db.add(other_manifest)
        db.flush()
        other_report = MonthlyReport(
            hospital_id=other_hospital_id,
            period_year=2026,
            period_month=6,
            report_type="MONTHLY",
            manifest_id=other_manifest.id,
            cutoff_at=now,
            quality="COMPLETE",
            planned_count=1,
            success_count=1,
            failed_count=0,
            excluded_count=0,
            customer_ready=True,
            delivery_blockers=[],
            pdf_path=str(complete_path),
            doctor_pdf_path=str(complete_path),
            sov_summary={"observation_adequacy": {"status": "COMPLETE"}},
            content_summary={"published_count": 1, "promised_count": 12},
            essence_summary={"approved_philosophy_exists": True},
            sent_at=now,
        )
        db.add(other_report)
        db.flush()
        other_metadata = DoctorArtifactMetadata(
            validation_version="doctor-pdf-v2",
            validation_source="SYSTEM",
            page_count=complete_pages,
            page_size="A4",
            glyph_count=1,
            font_family="Pretendard",
            font_embedded=True,
            korean_to_unicode=True,
            link_count=1,
            expected_link_present=True,
            required_text_present=True,
            sha256=complete_digest,
            byte_size=len(complete_data),
        )
        other_artifact = MonthlyReportArtifact(
            report_id=other_report.id,
            audience="DOCTOR",
            path=str(complete_path),
            sha256=complete_digest,
            byte_size=len(complete_data),
            validated=True,
            validated_at=now,
            validation_metadata=other_metadata.model_dump(mode="json"),
        )
        db.add(other_artifact)
        db.flush()
        db.add(
            MonthlyDeliveryEvent(
                report_id=other_report.id,
                artifact_id=other_artifact.id,
                event_type="DELIVERED",
                actor_id=uuid.UUID("16000000-0000-0000-0000-000000000301"),
                recipient="이다른 원장",
                metadata_json={
                    "channel": "대면",
                    "artifact_sha256": complete_digest,
                    "provenance": "task16-preexisting-delivery",
                },
            )
        )
        db.commit()
        other_report_id = str(other_report.id)
        other_artifact_id = str(other_artifact.id)
    fixture_manifest_path = artifacts / "fixture-manifest.json"
    fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
    fixture_manifest.update(
        {
            "reportIds": report_ids,
            "otherReportId": other_report_id,
            "otherArtifactId": other_artifact_id,
        }
    )
    write_json(fixture_manifest_path, fixture_manifest)
    write_json(artifacts / "pdf-assertions.json", assertions)
    emit("complete_limited_unavailable_pdf_bytes_and_identity", reports=len(assertions))


def rollback() -> None:
    import redis

    manifest = json.loads(Path("/artifacts/fixture-manifest.json").read_text(encoding="utf-8"))
    path = f"/api/v1/public/hospitals/{manifest['slug']}/contents/{manifest['contentId']}"
    code, payload = get_json(f"http://api-compatible:8000{path}")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "task16-approved-body" in serialized
    assert "task16-rollback-pending-body" not in serialized
    aged_code, aged_payload = get_json(
        "http://api-compatible:8000/api/v1/public/hospitals/"
        f"{manifest['slug']}/contents/{manifest['agedContentId']}"
    )
    assert "오래된 참고자료 검증 시각" in json.dumps(aged_payload, ensure_ascii=False)
    withdrawn_code, _ = get_json(
        "http://api-compatible:8000/api/v1/public/hospitals/"
        f"{manifest['slug']}/contents/{manifest['legacyIds']['withdraw']}",
        expected=404,
    )
    try:
        urllib.request.urlopen("http://admin-new:3001/login", timeout=2)
    except (urllib.error.URLError, TimeoutError):
        admin_stopped = True
    else:
        admin_stopped = False
    assert admin_stopped, "Admin must remain stopped during compatible-reader rollback"
    from app.core.celery_app import celery_app

    assert not (celery_app.control.inspect(timeout=2).stats() or {}), "workers must be stopped"
    broker = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    assert not broker.exists("redbeat::lock"), "Beat lock must be absent during rollback"
    receipt = {
        "publicStatus": code,
        "activeBody": True,
        "pendingCandidateHidden": True,
        "agedApprovedReferenceStatus": aged_code,
        "withdrawnStatus": withdrawn_code,
        "adminStopped": admin_stopped,
        "workersStopped": True,
        "beatStopped": True,
    }
    write_json(Path("/artifacts/rollback.json"), receipt)
    emit("compatible_public_read_with_all_mutation_roles_paused", **receipt)


def final() -> None:
    _, broker = db_objects()
    unexpected = broker.lrange("rehearsal:unexpected_requests", 0, -1)
    assert unexpected == [], unexpected
    receipts = [json.loads(value) for value in broker.lrange("rehearsal:provider_receipts", 0, -1)]
    write_json(Path("/artifacts/provider-receipts.json"), receipts)
    required = (
        "fixture-manifest.json",
        "legacy-mutation-matrix.json",
        "old-queued-message.json",
        "worker-loss-recovery.json",
        "legacy-incident-reconciliation.json",
        "candidate-pass.json",
        "report-delivery-binding.json",
        "queue-canaries.json",
        "parity-manifest.json",
        "pdf-assertions.json",
        "negative-boundaries.json",
        "immutable-history-before.json",
        "immutable-history-after.json",
        "rollback.json",
        "browser/actions-new-flow.json",
        "browser/actions-verify-pass.json",
        "browser/actions-rollback.json",
        "browser/visual-capture-manifest.json",
    )
    missing = [name for name in required if not (Path("/artifacts") / name).is_file()]
    assert not missing, missing
    visual_manifest = json.loads(
        Path("/artifacts/browser/visual-capture-manifest.json").read_text(encoding="utf-8")
    )
    assert visual_manifest["completeForReview"] is True
    sessions = {entry["mode"]: entry for entry in visual_manifest["sessions"]}
    expected_captures = {
        "new-flow": {
            "new-public-375",
            "new-public-768",
            "new-public-1280",
            "new-profile-1280",
            "new-public-unsafe-image-excluded-1280",
            "admin-pending-preview",
            "admin-candidate-cancelled",
            "admin-pending-pass-candidate",
            "admin-report-dialog-1",
            "admin-report-dialog-1-internal",
            "admin-report-dialog-2",
            "admin-report-dialog-3",
            "admin-report-list",
            "admin-report-list-three-statuses",
            "admin-budget-reset-available",
            "admin-budget-reset-action-ready",
            "admin-budget-reset-result",
            "admin-budget-reset-confirmed-state",
        },
        "verify-pass": {
            "pass-public-375",
            "pass-public-768",
            "pass-public-1280",
            "pass-profile-1280",
            "admin-candidate-pass",
        },
        "rollback": {
            "rollback-public-375",
            "rollback-public-768",
            "rollback-public-1280",
            "rollback-profile-1280",
        },
    }
    assert set(sessions) == set(expected_captures)
    for mode, expected_names in expected_captures.items():
        captures = sessions[mode]["captures"]
        assert {capture["name"] for capture in captures} == expected_names
        for capture in captures:
            screenshot = Path("/artifacts/browser") / f"{capture['name']}.png"
            screenshot_bytes = screenshot.read_bytes()
            assert capture["pngSignature"] == "89504e470d0a1a0a"
            assert hashlib.sha256(screenshot_bytes).hexdigest() == capture["sha256"]
            assert capture["image"]["width"] > 0 and capture["image"]["height"] > 0
            assert capture["horizontalOverflow"] is False
            assert isinstance(capture["koreanClippingCount"], int)
    emit("all_required_artifacts_and_no_unexpected_egress", receipts=len(receipts))


def component_final() -> None:
    _, broker = db_objects()
    unexpected = broker.lrange("rehearsal:unexpected_requests", 0, -1)
    assert unexpected == [], unexpected
    required = (
        "legacy-mutation-matrix.json",
        "old-queued-message.json",
        "worker-loss-recovery.json",
        "legacy-incident-reconciliation.json",
        "candidate-pass.json",
        "report-delivery-binding.json",
        "pdf-assertions.json",
        "negative-boundaries.json",
        "immutable-history-after.json",
        "browser/actions-new-flow.json",
        "browser/actions-verify-pass.json",
    )
    missing = [name for name in required if not (Path("/artifacts") / name).is_file()]
    assert not missing, missing
    write_json(
        Path("/artifacts/component-result.json"),
        {
            "status": "NON_FINAL_COMPONENT_PASS",
            "fullTask16Credited": False,
            "rollbackTested": False,
            "missing": [],
        },
    )
    emit("non_final_component_scenarios_complete", artifacts=list(required))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase")
    parser.add_argument("--artifacts")
    parser.add_argument("--old")
    parser.add_argument("--new")
    parser.add_argument("--compatible")
    args = parser.parse_args()
    phases = {
        "image-identities": lambda: image_identities(args),
        "migration": migration,
        "baseline-characterize": baseline_characterize,
        "rollback-smoke-seed": rollback_smoke_seed,
        "legacy-mutations": legacy_mutations,
        "writer-canaries": writer_canaries,
        "old-queue-prime": old_queue_prime,
        "old-queue-drain": old_queue_drain,
        "worker-loss-prime": worker_loss_prime,
        "worker-loss-observe": worker_loss_observe,
        "worker-loss-recover": worker_loss_recover,
        "synthetic-flow": synthetic_flow,
        "old-drained": old_drained,
        "legacy-incident-reconciliation": legacy_incident_reconciliation,
        "post-drain-reconcile": post_drain_reconcile,
        "switch": switch,
        "budget-reset": budget_reset,
        "negative-boundaries": negative_boundaries,
        "candidate-pass": candidate_pass,
        "report-delivery": report_delivery,
        "pdf": pdf,
        "rollback": rollback,
        "immutable-history": immutable_history,
        "final": final,
        "component-final": component_final,
    }
    if args.phase not in phases:
        raise SystemExit(f"unknown phase: {args.phase}")
    phases[args.phase]()


if __name__ == "__main__":
    main()
