"""매니페스트가 없는 달도 '전달 불가'가 아니다 — 보고서가 없으면 자동으로 만들 미생성이다(D38)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.monthly_control import MonthlyMeasurementManifest
from app.services import monthly_report_gap_notifications as notifications

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)  # KST 10/2 09:00 → 9월 판정


def _hospital(db: Session, name: str) -> Hospital:
    hospital = Hospital(
        name=name, slug=f"gap-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    db.flush()
    return hospital


def test_manifest_missing_month_is_an_automatic_missing_report_not_undeliverable(pg_conn, monkeypatch):
    db = Session(bind=pg_conn, join_transaction_mode="create_savepoint")
    no_manifest = _hospital(db, "가코호트밖의원")
    with_manifest = _hospital(db, "나코호트의원")
    db.add(
        MonthlyMeasurementManifest(
            hospital_id=with_manifest.id,
            period_year=2026,
            period_month=9,
            configured_platforms=["chatgpt", "gemini"],
            platform_provenance={},
            closes_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    db.flush()
    monkeypatch.setattr(
        notifications,
        "eligible_hospital_ids",
        lambda *_args: [no_manifest.id, with_manifest.id],
    )

    period_key, gaps = notifications.load_monthly_report_gaps(db, NOW)

    assert period_key == "2026-09"
    assert sorted(gaps, key=lambda gap: gap.hospital_name) == [
        notifications.MonthlyReportGap("가코호트밖의원", "MISSING", recoverable=True),
        notifications.MonthlyReportGap("나코호트의원", "MISSING", recoverable=True),
    ]
    db.close()
