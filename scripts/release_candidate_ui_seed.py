"""Synthetic UI fixtures in the dedicated migrated release_ui DB only."""
import json
import secrets

from verify_release_candidate import ARTIFACT_ROOT, CONTEXT, UI, configure, isolate_network

configure(UI)
isolate_network()

import arrow  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.models.admin_user import AdminUser  # noqa: E402
from app.models.hospital import Hospital, HospitalStatus, Plan  # noqa: E402
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType  # noqa: E402
from app.models.essence import HospitalContentPhilosophy, HospitalSourceAsset, PhilosophyStatus, SourceType, SourceStatus  # noqa: E402
from app.models.report import MonthlyReport  # noqa: E402
from app.services.admin_passwords import hash_admin_password  # noqa: E402

engine = create_engine(settings.SYNC_DATABASE_URL)
password = secrets.token_urlsafe(24)
with Session(engine) as db:
    assert db.scalar(text("SELECT current_database()")) == "reputation_release_ui"
    assert db.scalar(text("SELECT count(*) FROM hospitals")) == 0, "UI fixtures require an empty database"
    owner = AdminUser(email="release-owner@example.invalid", name="검증 운영자",
                      role="OWNER", password_hash=hash_admin_password(password), is_active=True)
    db.add(owner)
    now = arrow.now("Asia/Seoul")
    hospitals = []
    for index, name in enumerate(("검증 한빛정형외과", "검증 온유내과"), 1):
        h = Hospital(name=name, slug=f"release-qa-{index}", status=HospitalStatus.ACTIVE,
            plan=Plan.PLAN_12, profile_complete=True, site_built=True, site_live=True,
            v0_report_done=True, schedule_set=True, address="서울특별시 강남구 테헤란로 1",
            address_detail="검증용 2층", phone="02-000-0000", director_name=f"검증원장 {index}",
            specialties=["정형외과" if index == 1 else "내과"], region=["서울", "강남구"],
            keywords=["진료 안내"], treatments=["일반 진료"],
            business_hours={"mon": "09:00-18:00", "tue": "09:00-18:00", "sun": "휴진"})
        db.add(h)
        db.flush()
        source = HospitalSourceAsset(hospital_id=h.id, source_type=SourceType.INTERNAL_NOTE,
            title="검증용 병원 운영 자료", raw_text="환자가 이해하기 쉬운 진료 안내를 제공합니다.",
            status=SourceStatus.PROCESSED, content_hash="a" * 64, processed_at=now.datetime)
        db.add(source)
        db.flush()
        db.add(HospitalContentPhilosophy(hospital_id=h.id, version=1,
            status=PhilosophyStatus.APPROVED, is_base=True, approved_at=now.datetime,
            source_asset_ids=[str(source.id)], positioning_statement="설명 중심의 진료 안내",
            content_principles=["확인된 사실만 안내합니다."]))
        schedule = ContentSchedule(hospital_id=h.id, plan="PLAN_12",
            publish_days=list(range(7)), active_from=now.floor("month").date(), is_active=True)
        db.add(schedule)
        db.flush()
        for number in range(1, 13):
            db.add(ContentItem(hospital_id=h.id, schedule_id=schedule.id,
                content_type=ContentType.FAQ, sequence_no=number, total_count=12,
                scheduled_date=now.floor("month").shift(days=(number - 1) * 2).date(),
                status=ContentStatus.DRAFT, title=f"검증 질문 {number}"))
        hospitals.append({"id": str(h.id), "name": name, "slug": h.slug})
        previous = now.shift(months=-1)
        db.add(MonthlyReport(hospital_id=h.id, period_year=previous.year,
            period_month=previous.month, quality="DEGRADED", report_type="MONTHLY",
            planned_count=20, success_count=10, failed_count=10,
            customer_ready=False, delivery_blockers=["측정 미완료"],
            sov_summary={"sov_pct": None}, content_summary={}))
    db.commit()
    credentials = ARTIFACT_ROOT / "ui-fixtures.json"
    credentials.write_text(json.dumps({"email": owner.email, "password": password,
                                       "hospitals": hospitals, "ports": CONTEXT}, ensure_ascii=False))
    credentials.chmod(0o600)
    print("Created synthetic UI fixtures:", len(hospitals), "hospitals; credentials kept local")
engine.dispose()
