"""Read durable facts; explicit chaos actions only age retry clocks, never outcomes."""

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyReportArtifact
from app.models.operations import OperationRun
from app.models.report import MonthlyReport
from app.services.public_surface_intents import enqueue_public_surface_intent
from sqlalchemy import select, text

ROOT = Path("/evidence")
action = sys.argv[2] if len(sys.argv) > 2 else "status"
with SyncSessionLocal() as db:
    assert db.scalar(text("SELECT current_database()")) == "reputation_release_e2e"
    positive = db.scalar(select(Hospital).where(Hospital.slug == "e2e-clinic-0"))
    negative = db.scalar(select(Hospital).where(Hospital.slug == "e2e-clinic-1"))
    if action == "positive-intent":
        enqueue_public_surface_intent(db, positive)
        db.commit()
    elif action == "exception-intent":
        enqueue_public_surface_intent(db, negative)
        db.commit()
    elif action in ("age-exception", "age-recovery"):
        hospital = negative if action == "age-exception" else positive
        runs = db.scalars(
            select(OperationRun).where(
                OperationRun.hospital_id == hospital.id,
                OperationRun.operation_type == "SITE_REVALIDATION",
                OperationRun.state == "RUNNING",
            )
        ).all()
        for run in runs:
            run.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()
    elif action == "convert":
        count = db.scalar(
            text("SELECT count(*) FROM sov_records WHERE hospital_id=:id"),
            {"id": positive.id},
        )
        assert count > 0, "Conversion requires actual baseline measurements"
        positive.monthly_sov_cohort = True
        db.commit()
    elif action == "report-fixture":
        report = db.scalar(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == positive.id,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.created_at.desc())
        )
        assert report is not None and report.doctor_pdf_path != report.pdf_path
        artifact = db.scalar(
            select(MonthlyReportArtifact).where(
                MonthlyReportArtifact.report_id == report.id,
                MonthlyReportArtifact.audience == "DOCTOR",
            )
        )
        raw = Path(artifact.path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == artifact.sha256
        assert artifact.validated and artifact.byte_size == len(raw)
        (ROOT / "happy-report.json").write_text(
            json.dumps(
                {
                    "hospital_id": str(positive.id),
                    "report_id": str(report.id),
                    "sha256": artifact.sha256,
                    "path": artifact.path,
                    "internal_path": report.pdf_path,
                    "quality": report.quality,
                },
                indent=2,
            )
        )
    elif action != "status":
        raise ValueError(action)
    tables = {
        "content": "SELECT h.slug,c.status,count(*) AS count,count(c.body) AS bodies,count(c.image_content_hash) AS images FROM content_items c JOIN hospitals h ON h.id=c.hospital_id GROUP BY 1,2",
        "runs": "SELECT operation_type,state,safe_error_code,count(*) AS count FROM operation_runs GROUP BY 1,2,3",
        "reports": "SELECT hospital_id,report_type,quality,planned_count,success_count,failed_count,customer_ready,delivery_blockers FROM monthly_reports",
        "notifications": "SELECT notification_type,state,attempt_count,dedupe_key FROM notification_outbox",
        "measurements": "SELECT ai_platform,count(*) AS count FROM sov_records GROUP BY 1",
        "slots": "SELECT scope,answer_status,judgment_status,count(*) AS count FROM measurement_observation_slots GROUP BY 1,2,3",
    }
    facts = {
        key: [dict(row) for row in db.execute(text(sql)).mappings()]
        for key, sql in tables.items()
    }
    print(json.dumps(facts, default=str, ensure_ascii=False, indent=2))
