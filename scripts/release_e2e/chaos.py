"""Fault-inject only the owned Site; never edit a completed business outcome."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
import drive as driver  # noqa: E402 - validates the owned root before DB commands

sql, probe, stage = driver.sql, driver.probe, driver.stage


def facts():
    return sql(
        "SELECT json_agg(json_build_object('id',id,'status',status,'published',first_published_at,'revision',content_revision,'body_hash',md5(body),'image_hash',image_content_hash) ORDER BY id) FROM content_items WHERE body IS NOT NULL"
    )


original = facts()
assert (
    sql(
        "SELECT count(*) FROM notification_outbox WHERE notification_type='INCIDENT_OPEN'"
    )
    == "0"
)
subprocess.run(
    ["docker", "stop", "reputation-final-e2e-site"],
    check=True,
    stdout=subprocess.DEVNULL,
)
try:
    probe("positive-intent")
    probe("exception-intent")
    stage(
        "recover",
        lambda: (
            sql(
                "SELECT count(*) FROM operation_runs WHERE operation_type='SITE_REVALIDATION' AND state='RUNNING' AND attempt_count=1"
            )
            == "2"
        ),
    )
    assert facts() == original
    assert (
        sql(
            "SELECT count(*) FROM notification_outbox WHERE notification_type='INCIDENT_OPEN'"
        )
        == "0"
    )
    (ROOT / "chaos-retrying.json").write_text(probe("status"))
    for attempt in (2, 3):
        probe("age-exception")  # accelerate only the negative tenant's backoff clock
        stage(
            "recover",
            lambda: (
                sql(
                    f"SELECT count(*) FROM operation_runs o JOIN hospitals h ON h.id=o.hospital_id WHERE h.slug='e2e-clinic-1' AND o.operation_type='SITE_REVALIDATION' AND o.attempt_count={attempt}"
                )
                == "1"
            ),
        )
    assert (
        sql(
            "SELECT count(*) FROM operation_runs o JOIN hospitals h ON h.id=o.hospital_id WHERE h.slug='e2e-clinic-1' AND o.operation_type='SITE_REVALIDATION' AND o.state='FAILED'"
        )
        == "1"
    )
    assert (
        sql(
            "SELECT count(*) FROM notification_outbox WHERE notification_type='INCIDENT_OPEN'"
        )
        == "1"
    )
    assert facts() == original
    (ROOT / "chaos-exhausted.json").write_text(probe("status"))
finally:
    subprocess.run(
        ["docker", "start", "reputation-final-e2e-site"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
probe("age-recovery")
stage(
    "recover",
    lambda: (
        sql(
            "SELECT count(*) FROM operation_runs WHERE operation_type='SITE_REVALIDATION' AND state='SUCCEEDED'"
        )
        == "13"
    ),
)
assert facts() == original
stage(
    "heartbeat",
    lambda: (
        sql(
            "SELECT count(*) FROM notification_outbox WHERE notification_type='INCIDENT_OPEN' AND state='SENT'"
        )
        == "1"
    ),
)
assert (
    sql(
        "SELECT count(*) FROM notification_outbox WHERE notification_type='INCIDENT_OPEN'"
    )
    == "1"
)
assert (
    sql(
        "SELECT count(*) FROM notification_outbox WHERE notification_type='FLEET_HEARTBEAT'"
    )
    == "1"
)
(ROOT / "chaos-final.json").write_text(probe("status"))
(ROOT / "chaos-results.json").write_text(
    json.dumps(
        {
            "site_outage_retried_without_early_operator_alert": True,
            "terminal_negative_incident_outbox_rows": 1,
            "positive_revalidation_recovered": True,
            "published_content_id_body_image_first_publication_unchanged": True,
            "repeated_heartbeat_rows": 1,
            "live_slack_deliveries": 0,
        },
        indent=2,
    )
)
print("PASS all isolated outage, retry, exhaustion and deduplication checks")
