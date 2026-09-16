"""Repeat completed Beat ticks without repurchasing AI work or rewriting facts."""
import json
import sys
from pathlib import Path

import drive as driver

ROOT = Path(sys.argv[1]).resolve()


def facts():
    queries = {
        "content": "SELECT json_agg(json_build_object('id',id,'status',status,'first',first_published_at,'body',md5(body),'image',image_content_hash,'revision',content_revision) ORDER BY id) FROM content_items",
        "slots": "SELECT json_agg(json_build_object('id',id,'answer',answer_hash,'record',sov_record_id,'answers',answer_attempt_count,'judgments',judgment_attempt_count) ORDER BY id) FROM measurement_observation_slots",
        "artifacts": "SELECT json_agg(json_build_object('id',id,'sha256',sha256,'report_id',report_id) ORDER BY id) FROM monthly_report_artifacts",
        "heartbeat": "SELECT count(*) FROM notification_outbox WHERE notification_type='FLEET_HEARTBEAT'",
        "records": "SELECT count(*) FROM sov_records",
    }
    return {name: driver.sql(query) for name, query in queries.items()}


def provider_attempts():
    rows = [json.loads(line) for line in (ROOT / "wire.jsonl").read_text().splitlines()]
    return [row for row in rows if row["path"] not in {"/indexnow"} and not row["path"].startswith("/slack/")]

before = facts()
purchases_before = len(provider_attempts())
assert before["records"] == "300" and before["heartbeat"] == "1"
for stage, clock in (
    ("generate", "2026-09-15T23:00:00+09:00"),
    ("publish", "2026-09-16T08:00:00+09:00"),
    ("measure", "2026-09-30T22:00:00+09:00"),
    ("report", "2026-10-01T09:00:00+09:00"),
    ("heartbeat", "2026-10-01T09:00:00+09:00"),
):
    driver.clock(clock)
    driver.stage(stage, lambda: True)
    assert facts() == before, f"Completed facts changed after repeated {stage} ticks"
    assert len(provider_attempts()) == purchases_before, f"AI was repurchased by {stage} replay"
result = {
    "repeated_stages": ["generate", "publish", "measure", "report", "heartbeat"],
    "additional_provider_attempts": len(provider_attempts()) - purchases_before,
    "content_measurement_and_artifact_identities_preserved": True,
    "heartbeat_rows": 1,
}
(ROOT / "replay-results.json").write_text(json.dumps(result, indent=2))
print("PASS completed-stage replay: zero AI repurchases; all persisted identities unchanged")
