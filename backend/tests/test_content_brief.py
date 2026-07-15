import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
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
        body_updated_at=None,
        references_list=None,
        faq_question=None,
        faq_answer_summary=None,
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


def _exposure_action(action_id=None, hospital_id=None, target_id=None, action_type="CONTENT"):
    return SimpleNamespace(
        id=action_id or uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        query_target_id=target_id,
        action_type=action_type,
        title="환자 질문 답변 콘텐츠 보강",
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
    assert serialized["display"]["content_type_label"] == "자주 묻는 질문"
    assert serialized["display"]["status_label"] == "초안"
    assert serialized["display"]["brief_status_label"] == "콘텐츠 가이드 승인"
    assert serialized["display"]["essence_status_label"] is None
    assert serialized["display"]["review"] == {
        "label": "생성 전",
        "reason": "야간 자동 생성 대기",
        "publishable": False,
    }
    assert serialized["compliance"]["status"] == "BLOCKED"
    assert "본문 생성이 필요합니다." in serialized["compliance"]["blockers"]
    assert serialized["brief_approved_at"] == approved_at.isoformat()
    assert serialized["brief_approved_by"] == "Ops"


def test_serialize_item_exposes_forbidden_expression_compliance_blocker():
    item = _content_item(
        title="최고 진료 안내",
        body="환자 상태에 따라 설명합니다.",
        meta_description="",
        essence_status="ALIGNED",
    )

    serialized = content_api._serialize_item(item, full=True)

    assert serialized["compliance"]["status"] == "BLOCKED"
    assert serialized["compliance"]["forbidden_violations"] == ["최고"]
    assert "의료광고 금지 표현이 포함되어 있습니다." in serialized["compliance"]["blockers"]


def test_serialize_item_blocks_publish_without_references():
    item = _content_item(
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점을 정리합니다.",
        essence_status="ALIGNED",
        references_list=[],
    )

    serialized = content_api._serialize_item(item, full=True)

    assert serialized["compliance"]["status"] == "BLOCKED"
    assert serialized["compliance"]["references_count"] == 0
    assert "권위 있는 참고 자료가 1개 이상 필요합니다." in serialized["compliance"]["blockers"]


async def test_publish_content_rejects_generated_content_without_references(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    hospital.status = "ACTIVE"
    hospital.site_live = False
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점을 정리합니다.",
        essence_status="ALIGNED",
        references_list=[],
    )

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    async def fake_get_philosophy(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return None

    monkeypatch.setattr(content_api, "_get_approved_philosophy", fake_get_philosophy)

    class FakeDB:
        async def commit(self):
            return None

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital_id,
            item_id,
            content_api.PublishBody(published_by="AE"),
            db=FakeDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["missing"] == "references"


async def test_publish_content_blocks_concurrent_publish_under_lock(monkeypatch):
    # 동시 발행 경합: in-memory 읽기는 DRAFT지만 행 잠금 후 권위 상태가 PUBLISHED이면 차단 (API-1).
    from app.models.content import ContentStatus

    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    hospital.status = "ACTIVE"
    hospital.site_live = False
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점.",
        essence_status="ALIGNED",
        status="DRAFT",
    )

    async def fake_get_content(db, rid, rhid):
        return item

    async def fake_get_hospital(db, rhid):
        return hospital

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)

    class _LockResult:
        def scalar_one_or_none(self):
            return ContentStatus.PUBLISHED

    class _LockingDB:
        async def execute(self, statement):
            return _LockResult()

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital_id, item_id, content_api.PublishBody(published_by="AE"), db=_LockingDB()
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Already published"


async def test_publish_content_records_manual_screener_and_audit(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    hospital.status = "ACTIVE"
    hospital.site_live = False
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점.",
        essence_status="ALIGNED",
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
        status="DRAFT",
    )
    audit_calls = []
    published_notifications = []

    class FakeDB:
        commit_calls = 0

        async def commit(self):
            self.commit_calls += 1

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_philosophy(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _philosophy()

    async def fake_write_audit_log(*args, **kwargs):
        audit_calls.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    async def fake_notify_content_published(hospital_name, title):
        published_notifications.append((hospital_name, title))
        return True

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_approved_philosophy", fake_get_philosophy)
    monkeypatch.setattr(
        content_api,
        "assess_content_publication",
        lambda _item, philosophy: SimpleNamespace(
            publishable=True,
            code=None,
            message=None,
            violations=(),
            essence_status=content_api.ESSENCE_STATUS_ALIGNED,
            essence_summary={"ok": True},
            philosophy_id=getattr(philosophy, "id", None),
        ),
    )
    monkeypatch.setattr(content_api, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr(content_api.notifier, "notify_content_published", fake_notify_content_published)

    db = FakeDB()
    result = await content_api.publish_content(
        hospital_id,
        item_id,
        content_api.PublishBody(published_by="  김민지 AE  "),
        db=db,
    )

    assert result["detail"] == "Published"
    assert item.status == content_api.ContentStatus.PUBLISHED
    assert item.published_at is not None
    assert item.published_by == "김민지 AE"
    assert db.commit_calls == 1
    assert audit_calls == [
        {
            "action": "publish_content",
            "hospital_id": hospital.id,
            "actor": content_api.default_actor(),
            "target_type": "content_item",
            "target_id": item.id,
            "detail": {
                "title": item.title,
                "content_type": "FAQ",
                "scheduled_date": str(item.scheduled_date),
                "claimed_by": "김민지 AE",
                "essence_status": content_api.ESSENCE_STATUS_ALIGNED,
                "mode": "manual_recovery",
            },
        }
    ]
    assert published_notifications == [(hospital.name, item.title)]


async def test_post_publish_review_records_authenticated_actor_and_is_idempotent(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="공개된 글",
        status=content_api.ContentStatus.PUBLISHED,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )
    audits = []

    class FakeDB:
        commits = 0

        async def commit(self):
            self.commits += 1

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        return item

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "write_audit_log", fake_audit)
    monkeypatch.setattr(content_api, "default_actor", lambda: "operator@example.com")
    db = FakeDB()

    first = await content_api.complete_post_publish_review(
        hospital_id,
        item_id,
        content_api.PostPublishReviewBody(note="공개 페이지 확인"),
        db=db,
    )
    second = await content_api.complete_post_publish_review(
        hospital_id,
        item_id,
        content_api.PostPublishReviewBody(),
        db=db,
    )

    assert first["detail"] == "Post-publish review completed"
    assert second["detail"] == "Already reviewed"
    assert item.post_publish_reviewed_by == "operator@example.com"
    assert db.commits == 1
    assert audits[0]["action"] == "post_publish_review_completed"
    assert audits[0]["detail"]["note"] == "공개 페이지 확인"


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
    assert item.content_philosophy_id == philosophy.id
    assert item.brief_status == "APPROVED"
    assert item.brief_approved_by == "Ops"
    assert item.brief_approved_at is not None
    assert response["query_target_id"] == str(target_id)
    assert response["content_brief"]["target_query"] == "강남 치질 수술 회복 기간은?"


async def test_update_content_brief_blocks_measurement_exposure_action(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    action_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(id=item_id, hospital_id=hospital_id)
    action = _exposure_action(
        action_id=action_id,
        hospital_id=hospital_id,
        action_type="MEASUREMENT",
    )

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def refresh(self, refreshed_item):
            raise AssertionError("Measurement exposure actions should not be linked")

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_exposure_action_or_404", fake_get_action)

    db = FakeDB()
    with pytest.raises(HTTPException) as exc_info:
        await content_api.update_content_brief(
            hospital_id,
            item_id,
            ContentBriefUpdate(exposure_action_id=action_id),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert "content-producing AI exposure work items" in exc_info.value.detail
    assert db.committed is False
    assert item.exposure_action_id is None
    assert action.linked_content_id is None


async def test_update_content_brief_blocks_published_content_action_link(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    action_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(id=item_id, hospital_id=hospital_id, status="PUBLISHED")
    action_was_loaded = False

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def refresh(self, refreshed_item):
            raise AssertionError("Published content should not be relinked to exposure actions")

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        nonlocal action_was_loaded
        action_was_loaded = True
        raise AssertionError("Published content should be rejected before loading the action")

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_exposure_action_or_404", fake_get_action)

    db = FakeDB()
    with pytest.raises(HTTPException) as exc_info:
        await content_api.update_content_brief(
            hospital_id,
            item_id,
            ContentBriefUpdate(exposure_action_id=action_id),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert "published content" in exc_info.value.detail
    assert action_was_loaded is False
    assert db.committed is False
    assert item.exposure_action_id is None


async def test_update_content_brief_blocks_approval_without_approved_philosophy(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        content_brief={"target_query": "manual"},
    )

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def refresh(self, refreshed_item):
            raise AssertionError("Brief approval without philosophy should not commit")

    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        assert requested_item_id == item_id
        assert requested_hospital_id == hospital_id
        return item

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_philosophy(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return None

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_approved_philosophy", fake_get_philosophy)

    db = FakeDB()
    with pytest.raises(HTTPException) as exc_info:
        await content_api.update_content_brief(
            hospital_id,
            item_id,
            ContentBriefUpdate(
                brief_status="APPROVED",
                brief_approved_by="Ops",
            ),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert "approved clinic writing standard" in exc_info.value.detail
    assert db.committed is False
    assert item.brief_status is None
    assert item.brief_approved_at is None
    assert item.brief_approved_by is None


async def test_update_content_brief_reassigns_action_without_stale_links(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    target_id = uuid.uuid4()
    old_action_id = uuid.uuid4()
    new_action_id = uuid.uuid4()
    replaced_item_id = uuid.uuid4()

    hospital = _hospital(hospital_id)
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        exposure_action_id=old_action_id,
    )
    target = _query_target(target_id=target_id, hospital_id=hospital_id)
    old_action = _exposure_action(
        action_id=old_action_id,
        hospital_id=hospital_id,
        target_id=target_id,
    )
    old_action.linked_content_id = item_id
    new_action = _exposure_action(
        action_id=new_action_id,
        hospital_id=hospital_id,
        target_id=target_id,
    )
    new_action.linked_content_id = replaced_item_id
    replaced_item = _content_item(
        id=replaced_item_id,
        hospital_id=hospital_id,
        exposure_action_id=new_action_id,
    )

    class FakeDB:
        committed = False

        async def commit(self):
            self.committed = True

        async def refresh(self, refreshed_item):
            assert refreshed_item is item

        async def get(self, model, requested_id):
            if requested_id == replaced_item_id:
                return replaced_item
            return None

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
        if requested_action_id == old_action_id:
            return old_action
        if requested_action_id == new_action_id:
            return new_action
        raise AssertionError(f"Unexpected action id: {requested_action_id}")

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_query_target_or_404", fake_get_target)
    monkeypatch.setattr(content_api, "_get_exposure_action_or_404", fake_get_action)

    db = FakeDB()
    response = await content_api.update_content_brief(
        hospital_id,
        item_id,
        ContentBriefUpdate(
            exposure_action_id=new_action_id,
            content_brief={"target_query": "manual"},
        ),
        db=db,
    )

    assert db.committed is True
    assert item.exposure_action_id == new_action_id
    assert item.query_target_id == target_id
    assert old_action.linked_content_id is None
    assert new_action.linked_content_id == item_id
    assert replaced_item.exposure_action_id is None
    assert response["exposure_action_id"] == str(new_action_id)


async def test_update_content_brief_unlinks_action_clears_work_queue_link(monkeypatch):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    action_id = uuid.uuid4()

    hospital = _hospital(hospital_id)
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        exposure_action_id=action_id,
    )
    action = _exposure_action(action_id=action_id, hospital_id=hospital_id)
    action.linked_content_id = item_id

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

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)
    monkeypatch.setattr(content_api, "_get_exposure_action_or_404", fake_get_action)

    db = FakeDB()
    response = await content_api.update_content_brief(
        hospital_id,
        item_id,
        ContentBriefUpdate(
            exposure_action_id=None,
            content_brief={"target_query": "manual"},
        ),
        db=db,
    )

    assert db.committed is True
    assert item.exposure_action_id is None
    assert action.linked_content_id is None
    assert response["exposure_action_id"] is None
