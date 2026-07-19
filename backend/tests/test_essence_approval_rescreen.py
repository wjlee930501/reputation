import uuid
from types import SimpleNamespace

from app.api.admin.essence import _rescreen_content_items
from app.models.essence import PhilosophyStatus


def test_new_philosophy_rescreens_and_relinks_existing_content():
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    philosophy = SimpleNamespace(
        id=new_id,
        version=2,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="근거 중심 설명",
        doctor_voice="차분한 설명",
        patient_promise="개인차 안내",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=[],
        avoid_messages=[],
        treatment_narratives=[],
        local_context={},
        medical_ad_risk_rules=[],
    )
    safe = SimpleNamespace(
        id=uuid.uuid4(),
        title="치루의 원인",
        body="항문샘 감염과 진찰 과정을 설명합니다.",
        meta_description="진찰 결과에 따라 안내합니다.",
        faq_question=None,
        faq_answer_summary=None,
        content_philosophy_id=old_id,
        essence_status="ALIGNED",
        essence_check_summary={},
    )
    unsafe = SimpleNamespace(
        id=uuid.uuid4(),
        title="완치 보장 치료",
        body="누구나 완치할 수 있습니다.",
        meta_description=None,
        faq_question=None,
        faq_answer_summary=None,
        content_philosophy_id=old_id,
        essence_status="ALIGNED",
        essence_check_summary={},
    )

    counts = _rescreen_content_items([safe, unsafe], philosophy)

    assert counts == {"total": 2, "aligned": 1, "needs_review": 1}
    assert safe.content_philosophy_id == new_id
    assert safe.essence_status == "ALIGNED"
    assert unsafe.content_philosophy_id == new_id
    assert unsafe.essence_status == "NEEDS_ESSENCE_REVIEW"
