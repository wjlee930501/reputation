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
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.services.content_publication import image_certification_current
from app.workers import published_image_refresh as refresh
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
