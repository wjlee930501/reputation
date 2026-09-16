"""Boot the unmodified image entrypoint for API, prefork Worker, Beat and migration."""
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import drive as driver

ROOT = Path(sys.argv[1]).resolve()
DB = "reputation_release_image_smoke"
PREFIX = f"reputation-final-e2e-stock-{time.time_ns()}-"
NETWORK = "reputation-final-e2e-net"
IMAGE = driver.IMAGE
config = dict(line.split("=", 1) for line in (ROOT / "qa.env").read_text().splitlines() if "=" in line)
assert config["APP_ENV"] == "test"
for key in ("DATABASE_URL", "SYNC_DATABASE_URL"):
    assert urlsplit(config[key]).hostname == "qa-db"
    assert urlsplit(config[key]).path == "/reputation_release_e2e"
BASE = ["docker", "run", "--platform", "linux/amd64", "--network", NETWORK,
        "--label", "reputation.validation=final-e2e", "--env-file", str(ROOT / "qa.env")]
for key, value in {
    "DATABASE_URL": config["DATABASE_URL"].rsplit("/", 1)[0] + "/" + DB,
    "SYNC_DATABASE_URL": config["SYNC_DATABASE_URL"].rsplit("/", 1)[0] + "/" + DB,
    "REDIS_URL": config["REDIS_URL"].rsplit("/", 1)[0] + "/13", "COST_GUARD_REDIS_URL": config["REDIS_URL"].rsplit("/", 1)[0] + "/14",
    "REPORT_OUTPUT_DIR": "/tmp/reports", "SLACK_WEBHOOK_URL": "", "SLACK_WEBHOOK_URL_DEV": "",
    "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt", "PORT": "8080",
    "REPUTATION_RELEASE_REVISION": "stock-" + (ROOT / "runtime-sha").read_text().strip(),
}.items():
    BASE += ["-e", f"{key}={value}"]


def run(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=75)
    if result.returncode:
        print(result.stdout.decode()[-7000:], flush=True)
        raise RuntimeError("Isolated Docker command failed; see captured output")
    return result.stdout.decode()

existing = run(["docker", "exec", "reputation-final-e2e-db", "psql", "-U", "geo_test",
                "-d", "postgres", "-tAc", f"SELECT count(*) FROM pg_database WHERE datname='{DB}'"]).strip()
if existing == "0":
    run(["docker", "exec", "reputation-final-e2e-db", "createdb", "-U", "geo_test", "-E", "UTF8", DB])
result = {"image": IMAGE, "production_entrypoint_unmodified": True, "APP_ENV": "test",
          "no_runtime_adapter_or_source_mount": True, "roles": {}}
(ROOT / "stock-migration.log").write_text(run(BASE + ["--rm", "-e", "SERVICE=migrate", IMAGE]))
started = []
try:
    for role in ("api", "worker", "beat"):
        name = PREFIX + role
        # An existing name is never removed implicitly.
        run(BASE + ["-d", "--name", name, "-e", f"SERVICE={role}", IMAGE])
        started.append(name)
    for role in ("api", "worker", "beat"):
        path = "/health/live" if role == "api" else "/ready"
        url = "http://127.0.0.1:8080" + path
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            probe = subprocess.run(["docker", "exec", PREFIX + role, "curl", "-fsS", url],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Stock {role} never became ready")
        meta = json.loads(run(["docker", "inspect", PREFIX + role]))[0]
        result["roles"][role] = {"status": 200, "path": path, "user": meta["Config"]["User"],
                                "entrypoint": meta["Config"]["Entrypoint"], "command": meta["Config"]["Cmd"]}
    code = '''
import json,time
from app.core.celery_app import celery_app
from app.workers.canary_tasks import EXPECTED_QUEUES,read_queue_canaries
from app.workers.dispatch_envelope import build_dispatch_headers
sent={q:celery_app.send_task(f"app.workers.canary_tasks.canary_{q}",queue=q,headers=build_dispatch_headers(f"canary-{q}")).id for q in EXPECTED_QUEUES}
for _ in range(30):
    facts=read_queue_canaries()
    if facts.current and all(facts.queue_results[q]["task_id"]==sent[q] for q in sent): break
    time.sleep(1)
else: raise RuntimeError("Stock worker canaries not verified")
print(json.dumps({"queues":list(EXPECTED_QUEUES),"release":facts.release_revision,"verified":True}))
'''
    result["prefork_worker_canaries"] = json.loads(run(["docker", "exec", PREFIX + "api", "python", "-c", code]))
    (ROOT / "stock-runtime-results.json").write_text(json.dumps(result, indent=2))
    print("PASS stock migration, API, prefork Worker, Beat and seven signed queue canaries")
finally:
    for name in started:
        (ROOT / (name + ".log")).write_text(run(["docker", "logs", name]))
        run(["docker", "stop", name])
        run(["docker", "rm", name])
