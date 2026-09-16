"""Real Celery/Redis rehearsal on the isolated release databases only."""
import json
import os
import sys
import subprocess
import time

from verify_release_candidate import ARTIFACT_ROOT, REDIS_PORT, UI, configure, isolate_network

configure(UI)
os.environ["REDIS_URL"] = f"redis://127.0.0.1:{REDIS_PORT}/8"
os.environ["REPUTATION_RELEASE_REVISION"] = "local-" + subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True).strip()
os.environ["FLEET_HEARTBEAT_HOUR_KST"] = "0"
isolate_network()

from app.core.celery_app import celery_app  # noqa: E402
from app.workers.canary_tasks import EXPECTED_QUEUES, read_queue_canaries  # noqa: E402
from app.workers.dispatch_envelope import build_dispatch_headers  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "help"
if mode == "worker":
    celery_app.worker_main(["worker", "--pool=solo", "--concurrency=1",
        "--loglevel=warning", "--without-gossip", "--without-mingle",
        "--without-heartbeat", "--hostname=release-verification@local",
        "-Q", ",".join(EXPECTED_QUEUES)])
elif mode != "probe":
    raise SystemExit("Choose worker or probe")

if mode == "probe":
    from sqlalchemy import func, select
    from app.core.database import SyncSessionLocal
    from app.models.operations import NotificationOutbox

    sent = {}
    for queue in EXPECTED_QUEUES:
        result = celery_app.send_task(f"app.workers.canary_tasks.canary_{queue}",
            queue=queue, headers=build_dispatch_headers(f"canary-{queue}"))
        sent[queue] = result.id
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        facts = read_queue_canaries()
        if facts.current and all(facts.queue_results[q]["task_id"] == sent[q] for q in sent):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Unverified worker queues: {facts.missing_or_stale_queues}")
    for _ in range(2):
        celery_app.send_task("app.workers.notification_tasks.enqueue_fleet_heartbeat",
            queue="default", headers=build_dispatch_headers("fleet-heartbeat"))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with SyncSessionLocal() as db:
            count = db.scalar(select(func.count()).select_from(NotificationOutbox).where(
                NotificationOutbox.notification_type == "FLEET_HEARTBEAT"))
        if count == 1:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Fleet heartbeat never reached its durable outbox")
    time.sleep(1)
    with SyncSessionLocal() as db:
        rows = db.scalars(select(NotificationOutbox).where(
            NotificationOutbox.notification_type == "FLEET_HEARTBEAT")).all()
        assert len(rows) == 1 and rows[0].state == "PENDING"
    evidence = {"queues": list(EXPECTED_QUEUES), "current_release": facts.release_revision,
                "all_queue_canaries_verified": facts.current,
                "duplicate_heartbeat_outbox_rows": len(rows),
                "external_slack_deliveries": 0}
    (ARTIFACT_ROOT / "approval-broker.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)
