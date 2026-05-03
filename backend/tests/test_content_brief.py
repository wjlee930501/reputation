import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.admin import content as content_api
from app.schemas.content import ContentBriefUpdate
from app.services.content_brief import build_content_brief


def _hospital(hospital_id=None):
    return SimpleNamespace(
        id=hospital_id or uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        treatments=[{"name": "치질 수술", "description": "상태에 따라 수술 여부와 회복 계획을 설명합니다."}],
    )


def _content_item(**overrides):
    base = dict(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        content_type="FAQ",
        sequence_no=1,
        total_count=8,
        title=None,
        body=None,
        meta_description=None,
        image_url=None,
        image_prompt=None,
        scheduled_date=date(2026, 6, 1),
        status="DRAFT",
        generated_at=None,
        published_at=None,
        published_by=None,
        content_philosophy_id=None,
        query_target_id=None,
        exposure_action_id=None,
        content_brief=None,
        brief_status=None,
        brief_approved_at=None,
        brief_approved_by=None,
        essence_status=None,
        essence_check_summary=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _query_target(target_id=None, hospital_id=None):
    return SimpleNamespace(
        id=target_id or uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        name="강남 치질 수술 추천",
        target_intent="추천형",
        region_terms=["강남"],
        specialty="대장항문외과",
        condition_or_symptom="치질",
        treatment="치질 수술",
        decision_criteria=["회복 기간", "통증 부담"],
        patient_language="ko",
        platforms=["CHATGPT"],
        competitor_names=[],
        priority="HIGH",
        status="ACTIVE",
        target_month="2026-06",
        variants=[
            SimpleNamespace(
                query_text="강남 치질 수술 회복 기간은?",
                is_active=True,
                created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        ],
    )


def _exposure_action(action_id=None, hospital_id=None, target_id=None):
    return SimpleNamespace(
        id=action_id or uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        query_target_id=target_id,
        action_type="CONTENT",
        title="타깃 질의 답변 콘텐츠 보강",
        description="AI 답변에서 병원 언급을 보강할 콘텐츠가 필요합니다.",
        due_month="2026-06",
        status="OPEN",
        linked_content_id=None,
    )


def _philosophy(philosophy_id=None):
    return SimpleNamespace(
        id=philosophy_id or uuid.uuid4(),
        version=2,
        positioning_statement="충분히 설명하는 대장항문 진료",
        doctor_voice="차분하고 구체적인 설명",
        patient_promise="상태에 맞는 선택지를 설명합니다.",
        content_principles=["진단 전 단정하지 않기"],
        tone_guidelines=["불안을 키우지 않는 톤"],
        must_use_messages=["상태 확인 후 치료 방향을 정합니다."],
        avoid_messages=["완치 보장"],
        medical_ad_risk_rules=["치료 효과를 보장하지 않습니다."],
        treatment_narratives=[
            {"treatment": "치질 수술", "angle": "증상 단계와 회복 계획을 함께 설명합니다."}
        ],
    )


def test_build_content_brief_uses_query_target_action_and_philosophy():
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id)
    target = _query_target(hospital_id=hospital.id)
    action = _exposure_action(hospital_id=hospital.id, target_id=target.id)
    philosophy = _philosophy()

    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=target,
        exposure_action=action,
        philosophy=philosophy,
    )

    assert brief["target_query"] == "강남 치질 수술 회복 기간은?"
    assert brief["patient_intent"] == "추천형"
    assert brief["philosophy_reference"]["id"] == str(philosophy.id)
    assert brief["treatment_narrative"]["source"] == "approved_philosophy"
    assert brief["must_use_messages"] == ["상태 확인 후 치료 방향을 정합니다."]
    assert brief["avoid_messages"] == ["완치 보장"]
    assert brief["medical_risk_rules"] == ["치료 효과를 보장하지 않습니다."]
    assert brief["internal_link_target"]["path"] == f"/test-clinic/contents/{item.id}"


def test_content_brief_schema_rejects_unknown_status():
    with pytest.raises(ValidationError):
        ContentBriefUpdate(brief_status="READY")


def test_serialize_item_includes_brief_and_query_links():
    target_id = uuid.uuid4()
    action_id = uuid.uuid4()
    approved_at = datetime(2026, 5, 3, 9, 0, tzinfo=timezone.utc)
    item = _content_item(
        query_target_id=target_id,
        exposure_action_id=action_id,
        content_brief={"target_query": "강남 치질 수술 회복 기간은?"},
        brief_status="APPROVED",
        brief_approved_at=approved_at,
        brief_approved_by="Ops",
    )

    serialized = content_api._serialize_item(item, full=True)

    assert serialized["query_target_id"] == str(target_id)
    assert serialized["exposure_action_id"] == str(action_id)
    assert serialized["content_brief"]["target_query"] == "강남 치질 수술 회복 기간은?"
    assert serialized["brief_status"] == "APPROVED"
    assert serialized["brief_approved_at"] == approved_at.isoformat()
    assert serialized["brief_approved_by"] == "Ops"


async def test_update_content_brief_links_action_infers_target_and_approves(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    target_id = uuid.uuid4()
    action_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(id=item_id, hospital_id=hospital_id)
    target = _query_target(target_id=target_id, hospital_id=hospital_id)
    action = _exposure_action(action_id=action_id, hospital_id=hospital_id, target_id=target_id)
    philosophy = _philosophy()

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def refresh(self, refreshed_item):
            assert refreshed_item is item

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_target(db, requested_hospital_id, requested_target_id):
        assert requested_hospital_id == hospital_id
        assert requested_target_id == target_id
        return target

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_get_philosophy(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return philosophy

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_query_target_or_404", fake_get_target)
    monkeypatch.setattr(content_api, "_get_exposure_action_or_404", fake_get_action)
    monkeypatch.setattr(content_api, "_get_approved_philosophy", fake_get_philosophy)

    db = FakeDB()
    response = await content_api.update_content_brief(
        hospital_id,
        item_id,
        ContentBriefUpdate(
            exposure_action_id=action_id,
            brief_status="APPROVED",
            brief_approved_by="Ops",
            regenerate_brief=True,
        ),
        db=db,
    )

    assert db.committed is True
    assert item.query_target_id == target_id
    assert item.exposure_action_id == action_id
    assert action.linked_content_id == item_id
    assert item.brief_status == "APPROVED"
    assert item.brief_approved_by == "Ops"
    assert item.brief_approved_at is not None
    assert response["query_target_id"] == str(target_id)
    assert response["content_brief"]["target_query"] == "강남 치질 수술 회복 기간은?"
