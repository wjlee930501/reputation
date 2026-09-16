"""Bounded stage driver for the owned isolated Docker release rehearsal."""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
assert str(ROOT).startswith("/private/tmp/reputation-approval-")
BASE = json.loads((ROOT / "docker-base.json").read_text())
assert BASE[:2] == ["docker", "run"] and "reputation-final-e2e-net" in BASE
IMAGE = "reputation-verify-backend:" + (ROOT / "runtime-sha").read_text().strip()
NETWORK = json.loads(
    subprocess.check_output(
        ["docker", "network", "inspect", "reputation-final-e2e-net"]
    )
)[0]
assert NETWORK["Internal"], "External network forbidden"


def command(args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT).decode()


def sql(query):
    return command(
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
            query,
        ]
    ).strip()


def probe(action):
    return command(
        [
            "docker",
            "exec",
            "reputation-final-e2e-worker",
            "python",
            "/qa/runtime.py",
            "probe",
            action,
        ]
    )


def stage(name, condition, *, timeout=120):
    container = f"reputation-final-e2e-beat-{name}"
    existing = subprocess.run(
        ["docker", "inspect", container],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if existing.returncode == 0:
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL
        )
    command(BASE + ["-d", "--name", container, IMAGE, "/qa/runtime.py", "beat", name])
    try:
        began = time.monotonic()
        deadline = began + timeout
        while time.monotonic() < deadline:
            if condition() and time.monotonic() - began >= 6:
                time.sleep(3)  # repeated ticks must preserve terminal identities
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"{name} condition not satisfied within {timeout}s")
    finally:
        command(["docker", "stop", container])
        (ROOT / f"final-beat-{name}.log").write_text(
            command(["docker", "logs", container])
        )
        (ROOT / f"final-{name}-status.json").write_text(probe("status"))
    print(f"PASS stage {name}", flush=True)


def clock(value):
    (ROOT / "clock.txt").write_text(value)


def main():
    if sys.argv[2] == "pipeline":
        clock("2026-09-15T23:00:00+09:00")
        stage(
            "generate",
            lambda: (
                sql(
                    "SELECT count(*) FROM content_items WHERE body IS NOT NULL AND image_content_hash IS NOT NULL"
                )
                == "12"
            ),
        )
        clock("2026-09-16T08:00:00+09:00")
        stage(
            "publish",
            lambda: (
                sql("SELECT count(*) FROM content_items WHERE status='PUBLISHED'")
                == "12"
            ),
        )
        stage(
            "recover",
            lambda: (
                sql(
                    "SELECT count(*) FROM operation_runs WHERE operation_type='SITE_REVALIDATION' AND state='SUCCEEDED'"
                )
                == "12"
            ),
        )
        stage(
            "index",
            lambda: (
                sql(
                    "SELECT count(*) FROM operation_runs WHERE operation_type='INDEXNOW_SUBMISSION' AND state='SUCCEEDED'"
                )
                == "12"
            ),
        )
        stage("baseline", lambda: int(sql("SELECT count(*) FROM sov_records")) >= 150)
        probe("convert")
        clock("2026-09-30T22:00:00+09:00")
        stage(
            "measure",
            lambda: (
                sql(
                    "SELECT count(*) FROM operation_runs WHERE operation_type='RUN_SOV' AND state='SUCCEEDED' AND result_summary->>'measurement_month'='2026-09'"
                )
                == "1"
            ),
        )
        assert (
            sql(
                "SELECT count(*) FROM measurement_observation_slots WHERE judgment_status='CONFIRMED'"
            )
            == "150"
        )
        clock("2026-10-01T09:00:00+09:00")
        stage(
            "report",
            lambda: (
                sql(
                    "SELECT count(*) FROM operation_runs o JOIN hospitals h ON h.id=o.hospital_id WHERE h.slug='e2e-clinic-0' AND o.operation_type='SCHEDULED_MONTHLY_REPORT' AND o.state='SUCCEEDED'"
                )
                == "1"
            ),
            timeout=180,
        )
        probe("report-fixture")
        stage(
            "heartbeat",
            lambda: (
                int(
                    sql(
                        "SELECT count(*) FROM notification_outbox WHERE notification_type='FLEET_HEARTBEAT' AND state='SENT'"
                    )
                )
                >= 1
            ),
            timeout=120,
        )
        (ROOT / "final-pipeline.json").write_text(probe("status"))
    elif sys.argv[2] == "finish":
        clock("2026-10-01T09:00:00+09:00")
        stage(
            "report",
            lambda: (
                sql(
                    "SELECT count(*) FROM operation_runs o JOIN hospitals h ON h.id=o.hospital_id WHERE h.slug='e2e-clinic-0' AND o.operation_type='SCHEDULED_MONTHLY_REPORT' AND o.state='SUCCEEDED'"
                )
                == "1"
            ),
            timeout=180,
        )
        probe("report-fixture")
        stage(
            "heartbeat",
            lambda: (
                int(
                    sql(
                        "SELECT count(*) FROM notification_outbox WHERE notification_type='FLEET_HEARTBEAT' AND state='SENT'"
                    )
                )
                >= 1
            ),
            timeout=120,
        )
        (ROOT / "final-pipeline.json").write_text(probe("status"))
    else:
        raise SystemExit("Choose pipeline or finish")


if __name__ == "__main__":
    main()
