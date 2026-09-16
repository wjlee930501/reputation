"""Synthetic onboarded hospitals; no generated content, measurements or reports seeded."""

import json
import secrets
from datetime import timedelta
from pathlib import Path

import arrow
from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval
from app.models.sov import AIQueryTarget, AIQueryVariant, QueryMatrix
from app.services.admin_passwords import hash_admin_password
from app.services.essence_engine import compute_sources_snapshot_hash
from sqlalchemy import text

ROOT = Path("/evidence")
now = arrow.now("Asia/Seoul").shift(days=1).floor("day").datetime
password = secrets.token_urlsafe(24)
with SyncSessionLocal() as db:
    assert db.scalar(text("SELECT current_database()")) == "reputation_release_e2e"
    assert db.scalar(text("SELECT count(*) FROM hospitals")) == 0
    owner = AdminUser(
        email="e2e-owner@example.invalid",
        name="검증 운영자",
        role="OWNER",
        password_hash=hash_admin_password(password),
        is_active=True,
    )
    db.add(owner)
    hospitals = []
    for index, name in enumerate(("검증 한빛정형외과", "검증 예외의원")):
        h = Hospital(
            name=name,
            slug=f"e2e-clinic-{index}",
            status=HospitalStatus.ACTIVE,
            plan=Plan.PLAN_12,
            profile_complete=True,
            site_built=True,
            site_live=True,
            v0_report_done=True,
            schedule_set=True,
            monthly_sov_cohort=False,
            address="서울특별시 강남구 테헤란로 1",
            address_detail="검증용 2층",
            phone="02-000-0000",
            director_name="검증 원장",
            specialties=["정형외과"],
            region=["서울", "강남구"],
            keywords=["허리 통증"],
            treatments=[{"name": "허리 통증 진료", "description": "진료 안내"}],
            business_hours={"mon": "09:00-18:00"},
            created_at=now - timedelta(days=20),
        )
        db.add(h)
        db.flush()
        db.add(
            HospitalServiceInterval(
                hospital_id=h.id,
                started_at=now - timedelta(days=20),
                provenance="ACTIVATION",
            )
        )
        schedule = ContentSchedule(
            hospital_id=h.id,
            plan="PLAN_12",
            publish_days=list(range(7)),
            active_from=now.date().replace(day=1),
            is_active=True,
        )
        db.add(schedule)
        db.flush()
        for number in range(1, 13 if index == 0 else 2):
            db.add(
                ContentItem(
                    hospital_id=h.id,
                    schedule_id=schedule.id,
                    content_type=ContentType.FAQ,
                    sequence_no=number,
                    total_count=12,
                    scheduled_date=now.date(),
                    status=ContentStatus.DRAFT,
                )
            )
        if index == 0:
            source = HospitalSourceAsset(
                hospital_id=h.id,
                source_type=SourceType.INTERNAL_NOTE,
                title="검증용 승인 자료",
                raw_text="서울 강남구 허리 통증 진료 안내. 설명 중심으로 의료진과 상담합니다.",
                content_hash="a" * 64,
                status=SourceStatus.PROCESSED,
                processed_at=now,
            )
            db.add(source)
            db.flush()
            db.add(
                HospitalContentPhilosophy(
                    hospital_id=h.id,
                    version=1,
                    status=PhilosophyStatus.APPROVED,
                    is_base=True,
                    approved_at=now,
                    source_asset_ids=[str(source.id)],
                    source_snapshot_hash=compute_sources_snapshot_hash([source]),
                    positioning_statement="환자가 이해하기 쉬운 진료 안내",
                    content_principles=["확인된 사실만 안내합니다."],
                )
            )
            for number in range(15):
                query = QueryMatrix(
                    hospital_id=h.id,
                    query_text=f"서울 강남구 허리 통증 병원 진료 안내 {number}",
                    query_intent="LOCAL",
                    priority="HIGH",
                    is_active=True,
                )
                target = AIQueryTarget(
                    hospital_id=h.id,
                    name=query.query_text,
                    target_intent="LOCAL",
                    region_terms=["서울", "강남구"],
                    specialty="정형외과",
                    condition_or_symptom="허리 통증",
                    priority="HIGH",
                    status="ACTIVE",
                    in_tracking_set=True,
                    platforms=["chatgpt", "gemini"],
                )
                db.add_all([query, target])
                db.flush()
                for platform in ("chatgpt", "gemini"):
                    db.add(
                        AIQueryVariant(
                            query_target_id=target.id,
                            query_matrix_id=query.id,
                            query_text=query.query_text,
                            platform=platform,
                            language="ko",
                            is_active=True,
                        )
                    )
        hospitals.append({"id": str(h.id), "name": name, "slug": h.slug})
    db.commit()
    (ROOT / "ui-fixtures.json").write_text(
        json.dumps(
            dict(email=owner.email, password=password, hospitals=hospitals),
            ensure_ascii=False,
        )
    )
    (ROOT / "ui-fixtures.json").chmod(0o600)
    print(
        "Seeded 2 synthetic hospitals, 13 empty slots, 15 LOCAL targets; zero measured/generated artifacts."
    )
