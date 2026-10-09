# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy", "celery", "redis"]
# ///
# How to run: imported by check.py inside the disposable production image.
"""PostgreSQL fencing scenarios; fixture rows are deliberately uncertified."""
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.content import (
    ContentItem,
    ContentRevision,
    ContentRevisionApprovalStatus,
    ContentSchedule,
    ContentStatus,
    ContentType,
)
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, OperationRun
from app.services.admin_passwords import hash_admin_password
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256
from app.services.content_publication import image_certification_current
from app.services.image_engine import IMAGE_POLICY_VERSION, image_subject_hash
from app.services.reference_verification import item_topic_fingerprint, reference_url_fingerprint
from app.workers import published_image_refresh as refresh
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.nightly_generation_batch import write_back_published_image


def seed() -> uuid.UUID:
    with SyncSessionLocal() as db:
        hospital = Hospital(id=uuid.uuid4(), name="TEST ONLY integrity fixture",
                            slug="rehearsal-integrity", status=HospitalStatus.ACTIVE,
                            site_live=True, site_built=True, profile_complete=False)
        schedule = ContentSchedule(id=uuid.uuid4(), hospital_id=hospital.id,
                                   plan="PLAN_12", publish_days=[1, 3],
                                   active_from=date(2026, 9, 1))
        db.add_all([hospital, schedule])
        db.flush()
        eligible_id = uuid.uuid4()
        for sequence in range(1, 52):
            item = ContentItem(
                id=eligible_id if sequence == 51 else uuid.uuid4(),
                hospital_id=hospital.id, schedule_id=schedule.id,
                content_type=ContentType.DISEASE, sequence_no=sequence, total_count=51,
                scheduled_date=date(2026, 9, 1), status=ContentStatus.PUBLISHED,
                title="TEST ONLY image refresh candidate", body="TEST ONLY; not publishable",
                content_revision=3, published_at=datetime(2026, 9, 1, tzinfo=UTC),
                image_fallback_source="HOSPITAL_HERO",
                essence_check_summary={} if sequence == 51 else {
                    refresh.GENERATION_ATTEMPT_KEY: {"retry_class": "OPERATOR_REQUIRED"}},
            )
            db.add(item)
        db.commit()
        return eligible_id


def fencing(item_id: uuid.UUID) -> None:
    with SyncSessionLocal() as first, SyncSessionLocal() as second:
        first.execute(select(ContentItem).where(ContentItem.id == item_id).with_for_update())
        assert refresh._claim_image_refresh(second, item_id) is None
        first.rollback()
        old = refresh._claim_image_refresh(first, item_id)
        assert old is not None
        assert refresh._claim_image_refresh(second, item_id) is None
        second.rollback()
        item = first.get(ContentItem, item_id)
        assert item is not None
        item.generation_claimed_at = datetime.now(UTC) - timedelta(hours=4)
        first.commit()
        new = refresh._claim_image_refresh(second, item_id)
        assert new is not None and new != old
        assert write_back_published_image(
            first, item_id=item_id, expected_title=item.title,
            expected_revision=item.content_revision, expected_claim_token=old,
            values={"image_prompt": "STALE_TEST_WRITE"},
        ) == 0
        first.rollback()
        refresh._remember_claimed_failure(first, item, old, item.title,
                                          item.content_revision, "IMAGE_GENERATION_FAILED")
        second.expire_all()
        current = second.get(ContentItem, item_id)
        assert current is not None and current.generation_claim_token == new
        assert current.image_prompt != "STALE_TEST_WRITE"
        assert not image_certification_current(current)
        refresh.release_generation_claim(second, item_id, new)
        second.commit()


def occupy_prefix_leases(item_id: uuid.UUID) -> None:
    """Create a second synthetic selector case without resetting provider budgets."""
    with SyncSessionLocal() as db:
        target = db.get(ContentItem, item_id)
        assert target is not None
        rows = db.scalars(select(ContentItem).where(
            ContentItem.hospital_id == target.hospital_id,
            ContentItem.id != item_id,
        )).all()
        assert len(rows) == 50
        for row in rows:
            row.essence_check_summary = {}
            row.generation_claim_token = uuid.uuid4()
            row.generation_claimed_at = datetime.now(UTC)
        summary = dict(target.essence_check_summary)
        summary[refresh.GENERATION_ATTEMPT_KEY] = {
            **summary[refresh.GENERATION_ATTEMPT_KEY],
            "next_retry_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
        target.essence_check_summary = summary
        db.commit()


def seed_mixed_version() -> dict[str, str]:
    """Seed realistic lowercase legacy provenance without any external dependency."""

    now = datetime.now(UTC)
    hospital_id = uuid.UUID("16000000-0000-0000-0000-000000000001")
    content_id = uuid.UUID("16000000-0000-0000-0000-000000000101")
    aged_content_id = uuid.UUID("16000000-0000-0000-0000-000000000102")
    budget_content_id = uuid.UUID("16000000-0000-0000-0000-000000000103")
    other_hospital_id = uuid.UUID("16000000-0000-0000-0000-000000000002")
    other_content_id = uuid.UUID("16000000-0000-0000-0000-000000000104")
    unsafe_content_id = uuid.UUID("16000000-0000-0000-0000-000000000105")
    budget_incident_id = uuid.UUID("16000000-0000-0000-0000-000000000401")
    legacy_ids = {
        role: str(uuid.UUID(f"16000000-0000-0000-0000-{offset:012d}"))
        for offset, role in enumerate(
            ("edit", "publish", "reject", "withdraw", "regenerate"), start=10
        )
    }
    with SyncSessionLocal() as db:
        existing = db.get(Hospital, hospital_id)
        if existing is not None:
            return {
                "hospitalId": str(hospital_id),
                "contentId": str(content_id),
                "slug": existing.slug,
                "oldTitle": "검사 전 확인할 준비 사항",
                "oldBodyMarker": "승인된 공개 본문 표식 task16-active-body",
                "agedContentId": str(aged_content_id),
                "budgetContentId": str(budget_content_id),
                "budgetIncidentId": str(budget_incident_id),
                "otherHospitalId": str(other_hospital_id),
                "otherContentId": str(other_content_id),
                "otherSlug": "rehearsal-other-clinic",
                "unsafeContentId": str(unsafe_content_id),
                "legacyIds": legacy_ids,
            }
        hospital = Hospital(
            id=hospital_id,
            name="리허설 바른의원",
            slug="rehearsal-bareun-clinic",
            status=HospitalStatus.ACTIVE,
            plan="PLAN_12",
            address="서울특별시 중구 테스트로 16",
            phone="02-0000-0016",
            business_hours={"mon": "09:00-18:00"},
            website_url="https://rehearsal.example.test",
            blog_url="https://rehearsal.example.test/blog",
            kakao_channel_url="https://pf.kakao.com/rehearsal-test-only",
            google_business_profile_url="https://business.google.com/rehearsal-test-only",
            google_maps_url="https://maps.google.com/?cid=1600000000000000001",
            naver_place_url="https://m.place.naver.com/hospital/1600000001",
            latitude=37.5665,
            longitude=126.978,
            region=["서울 중구"],
            specialties=["외과"],
            keywords=["대장내시경"],
            director_name="김바른",
            director_career="외과 전문의",
            director_philosophy="환자의 질문을 먼저 확인합니다.",
            treatments=[{"name": "대장내시경", "description": "검사 전 상태를 확인합니다."}],
            profile_complete=True,
            v0_report_done=True,
            site_built=True,
            site_live=True,
            # The old writer matrix first exercises its real publication gate. The
            # harness turns this off after the old mutations so the mixed-version
            # reader scenario still proves schedule-less public compatibility.
            schedule_set=True,
        )
        schedule = ContentSchedule(
            id=uuid.UUID("16000000-0000-0000-0000-000000000011"),
            hospital_id=hospital_id,
            plan="PLAN_12",
            publish_days=[1, 3],
            active_from=date(2026, 10, 1),
            is_active=True,
        )
        db.add_all([hospital, schedule])
        db.flush()
        philosophy = HospitalContentPhilosophy(
            id=uuid.UUID("16000000-0000-0000-0000-000000000021"),
            hospital_id=hospital_id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            is_base=True,
            positioning_statement="환자의 질문을 먼저 확인하는 외과 진료",
            doctor_voice="근거를 쉬운 말로 설명합니다.",
            content_principles=["핵심 답변을 먼저 제시합니다."],
            tone_guidelines=["차분하고 구체적으로 설명합니다."],
            must_use_messages=[],
            avoid_messages=[],
            treatment_narratives=[{"name": "대장내시경", "angle": "검사 전 준비"}],
            local_context={"region": "서울 중구"},
            medical_ad_risk_rules=[],
            evidence_map={},
            source_asset_ids=["legacy-source-16"],
            unsupported_gaps=[],
            conflict_notes=[],
            source_snapshot_hash="b" * 64,
            created_by="qa.owner@example.test",
            reviewed_by="qa.owner@example.test",
            approved_at=now - timedelta(days=40),
        )
        db.add(philosophy)
        db.flush()
        # Store a realistic public institution URL. The isolated network prevents
        # any outbound fetch; the fixture models previously verified evidence.
        reference_url = (
            "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
            "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5296"
        )
        reference = {"title": "국가건강정보포털 검사 안내", "url": reference_url}
        aged = (now - timedelta(days=365)).isoformat()
        check = {
            "url": reference_url,
            "url_fingerprint": reference_url_fingerprint(reference_url),
            "final_url": reference_url,
            "verdict": "pass",
            "reason": "page_verified",
            "status": 200,
            "page_title": "국가건강정보포털 검사 안내",
            "text_len": 240,
            "curated": True,
            "checked_at": aged,
            "verified_at": aged,
            "topic_fingerprint": "a" * 64,
        }
        item = ContentItem(
            id=content_id,
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ContentType.NOTICE,
            sequence_no=1,
            total_count=12,
            scheduled_date=date(2026, 10, 1),
            status=ContentStatus.PUBLISHED,
            title="검사 전 확인할 준비 사항",
            body="승인된 공개 본문 표식 task16-active-body\n\n## 검사 전\n의료진 안내를 확인합니다.",
            meta_description="대장내시경 검사 전 확인 사항",
            image_url=None,
            references_list=[],
            reference_checks=[],
            content_revision=4,
            generated_at=now,
            published_at=now - timedelta(days=30),
            published_by="qa.owner@example.test",
            first_published_at=now - timedelta(days=30),
            first_published_by="qa.owner@example.test",
            essence_status="ALIGNED",
            content_philosophy_id=philosophy.id,
            generation_philosophy_id=philosophy.id,
            last_reviewed_philosophy_id=philosophy.id,
            content_brief={
                "schema_version": "content-brief-v2",
                "target_query": "병원 진료 공지",
                "treatment_narrative": {
                    "source": "approved_philosophy",
                    "angle": "진료 전에 확인할 공지",
                },
                "source_snapshot": {"hash": "b" * 64, "source_asset_ids": ["legacy-source-16"]},
            },
            essence_check_summary={
                "generation_provenance": {
                    "provider": "fixture",
                    "source_asset_ids": ["legacy-source-16"],
                }
            },
        )
        db.add(item)
        db.flush()
        revision = ContentRevision(
            id=uuid.UUID("16000000-0000-0000-0000-000000000201"),
            content_item_id=item.id,
            edition_no=1,
            legacy_content_revision=4,
            title=item.title or "",
            body=item.body or "",
            meta_description=item.meta_description,
            references_list=[],
            reference_checks=[],
            source_snapshot={"schema_version": "content-brief-v2", "source_asset_ids": ["legacy-source-16"]},
            generation_provenance={"provider": "fixture", "source_asset_ids": ["legacy-source-16"]},
            generation_philosophy_id=philosophy.id,
            last_reviewed_philosophy_id=philosophy.id,
            source_snapshot_hash="b" * 64,
            source_fingerprint="c" * 64,
            approval_hash="d" * 64,
            approval_status=ContentRevisionApprovalStatus.APPROVED,
            approved_at=now - timedelta(days=30),
            approved_by="qa.owner@example.test",
        )
        db.add(revision)
        db.flush()
        item.active_revision_id = revision.id
        aged_item = ContentItem(
            id=aged_content_id,
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ContentType.DISEASE,
            sequence_no=2,
            total_count=12,
            scheduled_date=date(2026, 10, 2),
            status=ContentStatus.PUBLISHED,
            title="오래된 승인 참고자료가 있는 검사 안내",
            body="오래된 참고자료 검증 시각은 승인된 판의 공개를 숨기지 않습니다.",
            references_list=[reference],
            reference_checks=[check],
            content_revision=2,
            generated_at=now,
            published_at=now - timedelta(days=20),
            published_by="qa.owner@example.test",
            first_published_at=now - timedelta(days=20),
            first_published_by="qa.owner@example.test",
            essence_status="ALIGNED",
            content_philosophy_id=philosophy.id,
            generation_philosophy_id=philosophy.id,
            last_reviewed_philosophy_id=philosophy.id,
            content_brief={
                "schema_version": "content-brief-v2",
                "target_query": "검사 전 준비",
                "treatment_narrative": {"source": "approved_philosophy", "angle": "검사 안내"},
                "source_snapshot": {"hash": "b" * 64, "source_asset_ids": ["legacy-source-16"]},
            },
            essence_check_summary={"generation_provenance": {"source_asset_ids": ["legacy-source-16"]}},
        )
        # The PASS belongs to this exact article topic. Its old timestamp is
        # intentional: an approved immutable revision remains readable by contract.
        check["topic_fingerprint"] = item_topic_fingerprint(aged_item)
        aged_item.reference_checks = [dict(check)]
        db.add(aged_item)
        db.flush()
        aged_revision = ContentRevision(
            id=uuid.UUID("16000000-0000-0000-0000-000000000202"),
            content_item_id=aged_item.id,
            edition_no=1,
            legacy_content_revision=2,
            title=aged_item.title or "",
            body=aged_item.body or "",
            references_list=[reference],
            reference_checks=[check],
            source_snapshot=aged_item.content_brief or {},
            generation_provenance={"source_asset_ids": ["legacy-source-16"]},
            generation_philosophy_id=philosophy.id,
            last_reviewed_philosophy_id=philosophy.id,
            source_snapshot_hash="b" * 64,
            source_fingerprint="e" * 64,
            approval_hash="f" * 64,
            approval_status=ContentRevisionApprovalStatus.APPROVED,
            approved_at=now - timedelta(days=20),
            approved_by="qa.owner@example.test",
        )
        db.add(aged_revision)
        db.flush()
        aged_item.active_revision_id = aged_revision.id
        other_hospital = Hospital(
            id=other_hospital_id,
            name="리허설 다른의원",
            slug="rehearsal-other-clinic",
            status=HospitalStatus.ACTIVE,
            plan="PLAN_12",
            address="서울특별시 종로구 격리로 2",
            phone="02-0000-0002",
            business_hours={"mon": "09:00-18:00"},
            region=["서울 종로구"],
            specialties=["가정의학과"],
            keywords=["건강상담"],
            director_name="이다른",
            director_career="가정의학과 전문의",
            director_philosophy="일상 건강 질문을 차분히 듣습니다.",
            treatments=[{"name": "건강상담", "description": "일상 건강 상태를 확인합니다."}],
            profile_complete=True,
            v0_report_done=True,
            site_built=True,
            site_live=True,
            schedule_set=True,
        )
        other_schedule = ContentSchedule(
            id=uuid.UUID("16000000-0000-0000-0000-000000000012"),
            hospital_id=other_hospital_id,
            plan="PLAN_12",
            publish_days=[2, 4],
            active_from=date(2026, 10, 1),
            is_active=True,
        )
        db.add_all([other_hospital, other_schedule])
        db.flush()
        other_philosophy = HospitalContentPhilosophy(
            id=uuid.UUID("16000000-0000-0000-0000-000000000022"),
            hospital_id=other_hospital_id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            is_base=True,
            positioning_statement="일상 건강 질문을 확인하는 가정의학과 진료",
            doctor_voice="환자가 이해하기 쉬운 말로 설명합니다.",
            content_principles=["확인된 사실만 설명합니다."],
            tone_guidelines=["차분하고 구체적으로 설명합니다."],
            must_use_messages=[],
            avoid_messages=[],
            treatment_narratives=[{"name": "건강상담", "angle": "방문 전 안내"}],
            local_context={"region": "서울 종로구"},
            medical_ad_risk_rules=[],
            evidence_map={},
            source_asset_ids=["other-source-16"],
            unsupported_gaps=[],
            conflict_notes=[],
            source_snapshot_hash="1" * 64,
            created_by="qa.owner@example.test",
            reviewed_by="qa.owner@example.test",
            approved_at=now - timedelta(days=10),
        )
        db.add(other_philosophy)
        db.flush()
        other_item = ContentItem(
            id=other_content_id,
            hospital_id=other_hospital_id,
            schedule_id=other_schedule.id,
            content_type=ContentType.NOTICE,
            sequence_no=1,
            total_count=12,
            scheduled_date=date(2026, 10, 3),
            status=ContentStatus.PUBLISHED,
            title="다른 병원 공개 안내",
            body="기존하는 두 번째 병원 테넌트 본문 task16-other-tenant-body",
            image_url=f"gs://reputation-images/{'8' * 64}-other-tenant.png",
            image_content_hash="8" * 64,
            image_policy_verified_at=now,
            image_policy_version=IMAGE_POLICY_VERSION,
            references_list=[],
            reference_checks=[],
            content_revision=1,
            generated_at=now,
            published_at=now - timedelta(days=5),
            published_by="qa.owner@example.test",
            first_published_at=now - timedelta(days=5),
            first_published_by="qa.owner@example.test",
            essence_status="ALIGNED",
            content_philosophy_id=other_philosophy.id,
            generation_philosophy_id=other_philosophy.id,
            last_reviewed_philosophy_id=other_philosophy.id,
            content_brief={
                "schema_version": "content-brief-v2",
                "target_query": "다른 병원 방문 안내",
                "treatment_narrative": {
                    "source": "approved_philosophy",
                    "angle": "방문 전 확인 안내",
                },
                "source_snapshot": {
                    "hash": "1" * 64,
                    "source_asset_ids": ["other-source-16"],
                },
            },
            essence_check_summary={"generation_provenance": {"source_asset_ids": ["other-source-16"]}},
        )
        unsafe_item = ContentItem(
            id=unsafe_content_id,
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ContentType.NOTICE,
            sequence_no=3,
            total_count=12,
            scheduled_date=date(2026, 10, 3),
            status=ContentStatus.PUBLISHED,
            title="안전하지 않은 이미지 주소가 제외되는 안내",
            body="본문은 공개되지만 안전하지 않은 이미지 주소는 제외됩니다 task16-unsafe-image-body",
            image_url="javascript:alert('task16')",
            image_content_hash="a" * 64,
            image_policy_verified_at=now,
            image_policy_version=IMAGE_POLICY_VERSION,
            references_list=[],
            reference_checks=[],
            content_revision=1,
            generated_at=now,
            published_at=now - timedelta(days=4),
            published_by="qa.owner@example.test",
            first_published_at=now - timedelta(days=4),
            first_published_by="qa.owner@example.test",
            essence_status="ALIGNED",
            content_philosophy_id=philosophy.id,
            generation_philosophy_id=philosophy.id,
            last_reviewed_philosophy_id=philosophy.id,
            content_brief={
                "schema_version": "content-brief-v2",
                "target_query": "이미지 없이 제공되는 병원 공지",
                "treatment_narrative": {
                    "source": "approved_philosophy",
                    "angle": "안전한 공개 이미지 처리 안내",
                },
                "source_snapshot": {
                    "hash": "b" * 64,
                    "source_asset_ids": ["legacy-source-16"],
                },
            },
            essence_check_summary={"generation_provenance": {"source_asset_ids": ["legacy-source-16"]}},
        )
        db.add_all([other_item, unsafe_item])
        other_item.image_subject_hash = image_subject_hash(
            other_item.content_type, other_item.title
        )
        db.flush()
        for revision_id, content in (
            (uuid.UUID("16000000-0000-0000-0000-000000000203"), other_item),
            (uuid.UUID("16000000-0000-0000-0000-000000000204"), unsafe_item),
        ):
            content_revision = ContentRevision(
                id=revision_id,
                content_item_id=content.id,
                edition_no=1,
                legacy_content_revision=1,
                title=content.title or "",
                body=content.body or "",
                references_list=[],
                reference_checks=[],
                source_snapshot=content.content_brief or {},
                generation_provenance=content.essence_check_summary or {},
                generation_philosophy_id=content.generation_philosophy_id,
                last_reviewed_philosophy_id=content.last_reviewed_philosophy_id,
                source_snapshot_hash="2" * 64,
                source_fingerprint="3" * 64,
                approval_hash="4" * 64,
                approval_status=ContentRevisionApprovalStatus.APPROVED,
                approved_at=content.published_at,
                approved_by="qa.owner@example.test",
            )
            db.add(content_revision)
            db.flush()
            content.active_revision_id = content_revision.id
        image_hash = "9" * 64
        for offset, (role, state) in enumerate(
            (
                ("edit", ContentStatus.PUBLISHED),
                ("publish", ContentStatus.DRAFT),
                ("reject", ContentStatus.PUBLISHED),
                ("withdraw", ContentStatus.PUBLISHED),
                ("regenerate", ContentStatus.REJECTED),
            ),
            start=10,
        ):
            legacy_id = uuid.UUID(f"16000000-0000-0000-0000-{offset:012d}")
            assert legacy_ids[role] == str(legacy_id)
            generated = state != ContentStatus.REJECTED
            legacy = ContentItem(
                id=legacy_id,
                hospital_id=hospital_id,
                schedule_id=schedule.id,
                content_type=ContentType.NOTICE,
                sequence_no=offset,
                total_count=20,
                scheduled_date=date(2026, 10, offset),
                status=state,
                title=f"legacy {role} 진료 안내" if generated else None,
                body=("방문 전 진료시간을 확인하고 필요한 내용을 의료진과 상담해 주세요." * 35) if generated else None,
                meta_description=f"legacy {role} fixture" if generated else None,
                image_url=f"https://storage.googleapis.com/reputation-images/{image_hash}-task16.png" if generated else None,
                image_content_hash=image_hash if generated else None,
                image_policy_verified_at=now if generated else None,
                image_policy_version=IMAGE_POLICY_VERSION if generated else None,
                references_list=[],
                reference_checks=[],
                content_revision=1,
                generated_at=now if generated else None,
                published_at=(now - timedelta(days=5)) if state == ContentStatus.PUBLISHED else None,
                published_by="qa.owner@example.test" if state == ContentStatus.PUBLISHED else None,
                first_published_at=(now - timedelta(days=5)) if state == ContentStatus.PUBLISHED else None,
                first_published_by="qa.owner@example.test" if state == ContentStatus.PUBLISHED else None,
                essence_status="ALIGNED" if generated else None,
                content_philosophy_id=philosophy.id,
                generation_philosophy_id=philosophy.id,
                last_reviewed_philosophy_id=philosophy.id,
                content_brief={
                    "schema_version": "content-brief-v2",
                    "target_query": "병원 운영 공지",
                    "treatment_narrative": {"source": "approved_philosophy", "angle": "방문 안내"},
                    "source_snapshot": {"hash": "b" * 64, "source_asset_ids": ["legacy-source-16"]},
                },
                essence_check_summary={"generation_provenance": {"source_asset_ids": ["legacy-source-16"]}},
            )
            if generated:
                legacy.image_subject_hash = image_subject_hash(legacy.content_type, legacy.title)
                legacy.essence_check_summary = {
                    **legacy.essence_check_summary,
                    "ai_review": {
                        "schema_version": "content-review-v2",
                        "status": "PASS",
                        "confidence": 0.99,
                        "findings": [],
                        "summary": "task16 legacy fixture pass",
                        "model": "test-only/reviewer",
                        "candidate_sha256": candidate_sha256(legacy),
                        "coverage": candidate_review_coverage(legacy),
                    },
                }
            db.add(legacy)
        owner_id = uuid.UUID("16000000-0000-0000-0000-000000000301")
        db.add(
            AdminUser(
                id=owner_id,
                email="qa.owner@example.test",
                name="Task 16 QA",
                role="OWNER",
                password_hash=hash_admin_password("task16-only-password"),
                is_active=True,
                is_operations_test=True,
            )
        )
        # This is a faithful pre-budget-schema row: no counter survived, so the
        # application must fail closed until an OWNER records the one-time reset.
        budget_item = ContentItem(
            id=budget_content_id,
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ContentType.NOTICE,
            sequence_no=19,
            total_count=20,
            scheduled_date=date(2026, 10, 19),
            status=ContentStatus.REJECTED,
            title="레거시 예산 확인이 필요한 진료 안내",
            body="운영자가 실제 화면에서 예산 교체 사유를 기록하는 리허설 원고입니다." * 20,
            content_revision=1,
            content_philosophy_id=philosophy.id,
            generation_philosophy_id=philosophy.id,
            essence_check_summary={GENERATION_ATTEMPT_KEY: {}},
        )
        db.add(budget_item)
        # These fixture rows use UUID foreign-key values rather than ORM
        # relationships. Flush their referenced rows explicitly so PostgreSQL,
        # rather than SQLAlchemy's unrelated-instance ordering, remains the gate.
        db.flush()
        failed_run = OperationRun(
            id=uuid.UUID("16000000-0000-0000-0000-000000000402"),
            hospital_id=hospital_id,
            operation_type="REGENERATE_CONTENT",
            state="FAILED",
            requested_by_id=owner_id,
            attempt_count=1,
            total_count=1,
            failure_count=1,
            request_payload={"source_id": str(budget_content_id)},
            safe_error_code="LEGACY_SPEND_UNKNOWN",
            safe_error_message="이전 생성 기록의 사용량을 확인할 수 없습니다.",
            requested_at=now - timedelta(minutes=8),
            started_at=now - timedelta(minutes=7),
            completed_at=now - timedelta(minutes=6),
        )
        db.add(failed_run)
        db.flush()
        budget_incident = Incident(
            id=budget_incident_id,
            hospital_id=hospital_id,
            operation_run_id=failed_run.id,
            dedupe_key="task16:legacy-budget-reset",
            incident_type="CONTENT_GENERATION_FAILED",
            state="OPEN",
            severity="MEDIUM",
            customer_impact="다음 콘텐츠 생성이 예산 확인 전까지 중단됩니다.",
            owner_id=owner_id,
            sla_due_at=now + timedelta(hours=1),
            source_type="CONTENT_GENERATION",
            source_id=str(budget_content_id),
            safe_error_code="LEGACY_SPEND_UNKNOWN",
            safe_error_message="이전 생성 기록의 사용량을 확인할 수 없습니다.",
            next_action="처리 사유를 기록하고 레거시 예산을 한 번 교체해 다시 시도합니다.",
            admin_path=f"/operations?queue=incidents&hospital_id={hospital_id}",
            first_seen_at=now - timedelta(minutes=6),
            last_seen_at=now - timedelta(minutes=6),
        )
        db.add(budget_incident)
        legacy_incident_ids: list[str] = []

        def add_legacy_operation(
            *,
            ordinal: int,
            target: str | None,
            month: int,
            state: str,
            requested_offset: int,
            with_incident: bool,
        ) -> OperationRun:
            run = OperationRun(
                id=uuid.UUID(f"16000000-0000-0000-0000-{500 + ordinal:012d}"),
                hospital_id=hospital_id,
                operation_type="GENERATE_MONTHLY_REPORT",
                state=state,
                requested_by_id=owner_id,
                idempotency_key=f"task16-legacy-{ordinal}-2026-{month:02d}",
                attempt_count=1,
                total_count=1,
                success_count=1 if state == "SUCCEEDED" else 0,
                failure_count=0 if state == "SUCCEEDED" else 1,
                request_payload={
                    "_dispatch": {
                        "target_type": "hospital",
                        "target_id": target,
                        "task_args": [str(hospital_id), 2026, month],
                    }
                },
                safe_error_code=None if state == "SUCCEEDED" else "TASK_FAILED",
                requested_at=now + timedelta(minutes=requested_offset),
                started_at=now + timedelta(minutes=requested_offset, seconds=5),
                completed_at=now + timedelta(minutes=requested_offset, seconds=10),
            )
            db.add(run)
            db.flush()
            if with_incident:
                incident_id = uuid.UUID(
                    f"16000000-0000-0000-0000-{600 + ordinal:012d}"
                )
                legacy_incident_ids.append(str(incident_id))
                db.add(
                    Incident(
                        id=incident_id,
                        hospital_id=hospital_id,
                        operation_run_id=run.id,
                        dedupe_key=f"task16:legacy-background:{ordinal}",
                        incident_type="BACKGROUND_TASK_FAILED",
                        state="OPEN",
                        severity="MEDIUM",
                        customer_impact="레거시 백그라운드 작업 실패를 운영자가 확인해야 합니다.",
                        source_type="BACKGROUND_TASK",
                        source_id=str(run.id),
                        safe_error_code="TASK_FAILED",
                        safe_error_message="레거시 작업 실패",
                        next_action="정규 도메인 사고로 변환합니다.",
                        admin_path="/operations",
                        first_seen_at=run.completed_at,
                        last_seen_at=run.completed_at,
                    )
                )
            return run

        duplicate_target = "task16-duplicate-monthly"
        add_legacy_operation(
            ordinal=1, target=duplicate_target, month=9, state="FAILED",
            requested_offset=1, with_incident=True,
        )
        add_legacy_operation(
            ordinal=2, target=duplicate_target, month=9, state="FAILED",
            requested_offset=2, with_incident=True,
        )
        same_period_target = "task16-same-period-success"
        add_legacy_operation(
            ordinal=3, target=same_period_target, month=9, state="FAILED",
            requested_offset=3, with_incident=True,
        )
        add_legacy_operation(
            ordinal=4, target=same_period_target, month=9, state="SUCCEEDED",
            requested_offset=4, with_incident=False,
        )
        different_month_target = "task16-different-month-success"
        add_legacy_operation(
            ordinal=5, target=different_month_target, month=9, state="FAILED",
            requested_offset=5, with_incident=True,
        )
        add_legacy_operation(
            ordinal=6, target=different_month_target, month=10, state="SUCCEEDED",
            requested_offset=6, with_incident=False,
        )
        add_legacy_operation(
            ordinal=7, target=None, month=9, state="FAILED",
            requested_offset=7, with_incident=True,
        )
        later_failure_target = "task16-failure-success-failure"
        add_legacy_operation(
            ordinal=8, target=later_failure_target, month=9, state="FAILED",
            requested_offset=8, with_incident=True,
        )
        add_legacy_operation(
            ordinal=9, target=later_failure_target, month=9, state="SUCCEEDED",
            requested_offset=9, with_incident=False,
        )
        add_legacy_operation(
            ordinal=10, target=later_failure_target, month=9, state="FAILED",
            requested_offset=10, with_incident=True,
        )
        db.commit()
    return {
        "hospitalId": str(hospital_id),
        "contentId": str(content_id),
        "slug": "rehearsal-bareun-clinic",
        "oldTitle": "검사 전 확인할 준비 사항",
        "oldBodyMarker": "승인된 공개 본문 표식 task16-active-body",
        "agedContentId": str(aged_content_id),
        "budgetContentId": str(budget_content_id),
        "budgetIncidentId": str(budget_incident_id),
        "otherHospitalId": str(other_hospital_id),
        "otherContentId": str(other_content_id),
        "otherSlug": "rehearsal-other-clinic",
        "unsafeContentId": str(unsafe_content_id),
        "legacyIds": legacy_ids,
        "legacyIncidentIds": legacy_incident_ids,
    }
