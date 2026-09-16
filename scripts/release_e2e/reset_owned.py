"""Start a fresh round ONLY in the named, already-owned disposable E2E database."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
assert str(ROOT).startswith("/private/tmp/reputation-approval-")
BASE = json.loads((ROOT / "docker-base.json").read_text())
assert BASE[:2] == ["docker", "run"] and "reputation-final-e2e-net" in BASE
IMAGE = "reputation-verify-backend:" + (ROOT / "runtime-sha").read_text().strip()
network = json.loads(
    subprocess.check_output(
        ["docker", "network", "inspect", "reputation-final-e2e-net"]
    )
)[0]
assert network["Internal"]
for role in ("db", "queue", "api", "worker", "capture"):
    item = json.loads(
        subprocess.check_output(["docker", "inspect", "reputation-final-e2e-" + role])
    )[0]
    assert item["Config"]["Labels"].get("reputation.validation") == "final-e2e"
assert (
    subprocess.check_output(
        [
            "docker",
            "exec",
            "reputation-final-e2e-db",
            "psql",
            "-U",
            "geo_test",
            "-d",
            "reputation_release_e2e",
            "-tAc",
            "SELECT current_database()",
        ]
    )
    .decode()
    .strip()
    == "reputation_release_e2e"
)
for role in ("api", "worker", "capture"):
    subprocess.run(
        ["docker", "rm", "-f", "reputation-final-e2e-" + role],
        check=True,
        stdout=subprocess.DEVNULL,
    )
subprocess.run(
    [
        "docker",
        "exec",
        "reputation-final-e2e-db",
        "psql",
        "-U",
        "geo_test",
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "DROP DATABASE reputation_release_e2e WITH (FORCE)",
    ],
    check=True,
    stdout=subprocess.DEVNULL,
)
subprocess.run(
    [
        "docker",
        "exec",
        "reputation-final-e2e-db",
        "createdb",
        "-U",
        "geo_test",
        "-E",
        "UTF8",
        "reputation_release_e2e",
    ],
    check=True,
)
subprocess.run(
    ["docker", "exec", "reputation-final-e2e-queue", "redis-cli", "FLUSHALL"],
    check=True,
    stdout=subprocess.DEVNULL,
)
(ROOT / "clock.txt").write_text("2026-09-15T23:00:00+09:00")
(ROOT / "wire.jsonl").write_text("")
with (ROOT / "final-migration.log").open("w") as log:
    subprocess.run(
        BASE + ["--rm", IMAGE, "-m", "alembic", "upgrade", "head"],
        check=True,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
subprocess.run(BASE + ["--rm", IMAGE, "/qa/runtime.py", "seed"], check=True)
for role, alias, args in [
    ("capture", "qa-capture", ["/qa/capture.py"]),
    ("api", "qa-api", ["/qa/runtime.py", "api"]),
    ("worker", "qa-worker", ["/qa/runtime.py", "worker"]),
]:
    aliases = ["--network-alias", alias]
    if role == "capture":
        aliases += ["--network-alias", "e2e-clinic-0.site.example.test"]
    subprocess.run(
        BASE + ["-d", "--name", "reputation-final-e2e-" + role, *aliases, IMAGE, *args],
        check=True,
        stdout=subprocess.DEVNULL,
    )
print("Fresh migrated E2E round ready:", IMAGE, flush=True)
