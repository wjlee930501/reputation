"""Real PostgreSQL checks for durable service-period monthly cohort membership."""

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.sov import AIQueryTarget
from app.services.sov_tracking_set import iter_monthly_sov_cohort
from tests.db_env import require_db_url


def _hospital(db: Session, question_count: int) -> Hospital:
    hospital = Hospital(
        name=f"코호트-{question_count}-{uuid.uuid4().hex[:8]}",
        slug=f"cohort-{uuid.uuid4().hex}",
        status=HospitalStatus.ACTIVE,
        monthly_sov_cohort=True,
    )
    db.add(hospital)
    db.flush()
    for index in range(question_count):
        db.add(
            AIQueryTarget(
                hospital_id=hospital.id,
                name=f"질문 {index}",
                target_intent="LOCAL",
                platforms=["chatgpt", "gemini"],
                in_tracking_set=True,
            )
        )
    db.flush()
    return hospital


def test_ten_to_nine_and_zero_question_hospitals_stay_in_service_period_cohort() -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
    with Session(engine) as db:
        ten_to_nine = _hospital(db, 9)
        zero_question = _hospital(db, 0)

        cohort_ids = {hospital.id for hospital in iter_monthly_sov_cohort(db)}

        assert ten_to_nine.id in cohort_ids
        assert zero_question.id in cohort_ids
        db.rollback()
    engine.dispose()
