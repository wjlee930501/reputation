import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.admin import content as content_api
from app.schemas.content import ContentBriefUpdate
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.content_brief import build_content_brief, content_brief_matches_inputs
from app.services.image_engine import (
    IMAGE_POLICY_VERSION,
    image_content_hash_from_url,
    image_subject_hash,
)


def _hospital(hospital_id=None, **overrides):
    base = dict(
        id=hospital_id or uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        # 수동 발행 게이트 기본값 — 공개 운영 중 + 일정 설정 완료 (H-09).
        status="ACTIVE",
        site_live=True,
        schedule_set=True,
        # 공개 사이트가 실제로 이 병원의 글을 내보내는 상태.
        profile_complete=True,
        site_built=True,
        treatments=[{"name": "치질 수술", "description": "상태에 따라 수술 여부와 회복 계획을 설명합니다."}],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


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


@pytest.fixture
def verified_actor():
    """수동 발행은 확인된 로그인 actor를 요구한다 (H-09)."""
    actor = "ae@example.com"
    token = set_request_actor(actor)
    yield actor
    reset_request_actor(token)


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
    assert brief["avoid_messages"][0] == "완치 보장"
    assert any("의료광고 공통 금지 표현" in item for item in brief["avoid_messages"])
    assert brief["medical_risk_rules"][0] == "치료 효과를 보장하지 않습니다."
    assert any("완치·안전성을 단정" in item for item in brief["medical_risk_rules"])
    assert brief["internal_link_target"] is None


def test_content_brief_match_tracks_normalized_director_delta_ids():
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id)
    philosophy = _philosophy()
    first_delta_id = uuid.uuid4()
    second_delta_id = uuid.uuid4()
    philosophy.director_delta_ids = [second_delta_id, first_delta_id, second_delta_id]

    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        philosophy=philosophy,
    )

    assert brief["philosophy_reference"]["director_delta_ids"] == sorted(
        [str(first_delta_id), str(second_delta_id)]
    )
    assert content_brief_matches_inputs(brief, philosophy=philosophy, query_target=None)

    philosophy.director_delta_ids = [first_delta_id]
    assert not content_brief_matches_inputs(brief, philosophy=philosophy, query_target=None)


def test_build_content_brief_uses_target_query_instead_of_content_type_as_treatment():
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, content_type="DISEASE")
    target = _query_target(hospital_id=hospital.id)
    target.name = "경증응급 외상 치료 비용이 얼마나 드는지 알려줘"
    target.variants[0].query_text = target.name
    target.treatment = None
    target.condition_or_symptom = None

    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=target,
    )

    assert brief["target_query"] == target.name
    assert brief["treatment_narrative"]["treatment"] == target.name
    assert brief["treatment_narrative"]["treatment"] != "DISEASE"


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

    serialized = content_api._serialize_item(
        item, full=True, public_philosophy_id=None, hospital_serving=True
    )

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

    serialized = content_api._serialize_item(
        item, full=True, public_philosophy_id=None, hospital_serving=True
    )

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
        faq_question="치질 수술 전 무엇을 확인해야 하나요?",
        faq_answer_summary="증상과 회복 계획을 진료에서 함께 확인합니다.",
        image_url="https://storage.googleapis.com/reputation/content.png",
        image_policy_verified_at=datetime.now(timezone.utc),
    )

    serialized = content_api._serialize_item(
        item, full=True, public_philosophy_id=None, hospital_serving=True
    )

    assert serialized["compliance"]["status"] == "BLOCKED"
    assert serialized["compliance"]["references_count"] == 0
    assert "권위 있는 참고 자료가 1개 이상 필요합니다." in serialized["compliance"]["blockers"]


def test_serialize_item_blocks_publish_with_only_non_whitelisted_references():
    item = _content_item(
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점을 정리합니다.",
        essence_status="ALIGNED",
        references_list=[{"title": "광고 블로그", "url": "https://ad-blog.example.com/promo"}],
        faq_question="치질 수술 전 무엇을 확인해야 하나요?",
        faq_answer_summary="증상과 회복 계획을 진료에서 함께 확인합니다.",
        image_url="https://storage.googleapis.com/reputation/content.png",
        image_policy_verified_at=datetime.now(timezone.utc),
    )

    serialized = content_api._serialize_item(
        item, full=True, public_philosophy_id=None, hospital_serving=True
    )

    assert serialized["compliance"]["status"] == "BLOCKED"
    assert serialized["compliance"]["references_count"] == 0
    assert "권위 있는 참고 자료가 1개 이상 필요합니다." in serialized["compliance"]["blockers"]


async def test_publish_content_rejects_generated_content_without_references(monkeypatch, verified_actor):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점을 정리합니다.",
        essence_status="ALIGNED",
        references_list=[],
        faq_question="치질 수술 전에 무엇을 확인해야 하나요?",
        faq_answer_summary="환자 상태를 확인한 뒤 진료 방향을 정합니다.",
        image_url="gs://bucket/content.png",
        image_policy_verified_at=datetime.now(timezone.utc),
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
            content_api.PublishBody(),
            db=FakeDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["missing"] == "references"


async def test_publish_content_rejects_non_whitelisted_references(monkeypatch, verified_actor):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    item = _content_item(
        id=item_id,
        hospital_id=hospital_id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점을 정리합니다.",
        essence_status="ALIGNED",
        references_list=[{"title": "광고 블로그", "url": "https://ad-blog.example.com/promo"}],
        faq_question="치질 수술 전에 무엇을 확인해야 하나요?",
        faq_answer_summary="환자 상태를 확인한 뒤 진료 방향을 정합니다.",
        image_url="gs://bucket/content.png",
        image_policy_verified_at=datetime.now(timezone.utc),
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

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital_id,
            item_id,
            content_api.PublishBody(),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["missing"] == "references"


async def test_publish_content_blocks_concurrent_publish_under_lock(monkeypatch, verified_actor):
    # 동시 발행 경합: in-memory 읽기는 DRAFT지만 행 잠금 후 권위 상태가 PUBLISHED이면 차단 (API-1).
    from app.models.content import ContentStatus

    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
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
            hospital_id, item_id, content_api.PublishBody(), db=_LockingDB()
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Already published"


async def test_publish_content_records_manual_screener_and_audit(monkeypatch, verified_actor):
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
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

    db = FakeDB()
    result = await content_api.publish_content(
        hospital_id,
        item_id,
        content_api.PublishBody(),
        db=db,
    )

    assert result["detail"] == "Published"
    assert result["notification_state"] == "NOT_REQUIRED"
    assert item.status == content_api.ContentStatus.PUBLISHED
    assert item.published_at is not None
    assert item.published_by == verified_actor
    assert item.first_published_at == item.published_at
    assert item.first_published_by == verified_actor
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
                "claimed_by": verified_actor,
                "essence_status": content_api.ESSENCE_STATUS_ALIGNED,
                "mode": "manual_recovery",
            },
        }
    ]


def _publishable_item(hospital):
    """수동 발행 게이트를 통과할 수 있는 최소 원고."""
    return _content_item(
        hospital_id=hospital.id,
        title="치질 수술 전 확인할 점",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="진료 전 확인할 점.",
        essence_status="ALIGNED",
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
        status="DRAFT",
    )


class _PublishDB:
    """수동 발행 경로용 세션 더블 — 행 잠금 없이 조회와 commit만 제공한다."""

    def __init__(self, hospital, item):
        self._rows = {hospital.id: hospital, item.id: item}
        self.added = []
        self.commit_calls = 0

    async def get(self, _model, pk):
        return self._rows.get(pk)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_calls += 1


def _allow_assessment(monkeypatch):
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

    async def fake_get_philosophy(db, requested_hospital_id):
        return _philosophy()

    monkeypatch.setattr(content_api, "_get_approved_philosophy", fake_get_philosophy)


async def test_manual_publish_refuses_a_hospital_that_is_not_publicly_serving(verified_actor):
    # 수동 복구 발행도 자동 발행과 같은 공개 게이트를 지나야 한다 (H-09).
    hospital = _hospital(status="PAUSED")
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital.id, item.id, content_api.PublishBody(), db=db
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "HOSPITAL_NOT_PUBLIC"
    assert item.status != content_api.ContentStatus.PUBLISHED


async def test_manual_publish_refuses_without_a_schedule(verified_actor):
    hospital = _hospital(schedule_set=False)
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital.id, item.id, content_api.PublishBody(), db=db
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SCHEDULE_NOT_SET"
    assert item.status != content_api.ContentStatus.PUBLISHED


async def test_manual_publish_records_the_verified_actor_not_the_request_body(
    monkeypatch, verified_actor
):
    hospital = _hospital()
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    _allow_assessment(monkeypatch)

    await content_api.publish_content(hospital.id, item.id, content_api.PublishBody(), db=db)

    assert item.published_by == verified_actor
    assert item.first_published_by == verified_actor


async def test_manual_publish_requires_a_verified_actor():
    hospital = _hospital()
    item = _publishable_item(hospital)
    db = _PublishDB(hospital, item)
    token = set_request_actor(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await content_api.publish_content(
                hospital.id, item.id, content_api.PublishBody(), db=db
            )
    finally:
        reset_request_actor(token)

    assert exc_info.value.status_code == 403
    assert item.status != content_api.ContentStatus.PUBLISHED


def test_whitespace_only_title_or_body_is_not_generated():
    from app.services.content_publication import assess_content_publication

    item = _publishable_item(_hospital())
    item.title = "   "

    assert assess_content_publication(item, None).code == "CONTENT_NOT_GENERATED"


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

    async def fake_public_philosophy_id(db, requested_hospital_id):
        return None

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "write_audit_log", fake_audit)
    monkeypatch.setattr(content_api, "default_actor", lambda: "operator@example.com")
    monkeypatch.setattr(
        content_api, "get_public_approved_philosophy_id", fake_public_philosophy_id
    )
    # 이 테스트는 액터 기록과 멱등성만 본다. 공개 보류 게이트 자체는 바로 아래 테스트가
    # 실제 판정으로 검증한다.
    monkeypatch.setattr(
        content_api,
        "assess_public_visibility",
        lambda item, philosophy_id: content_api.PublicVisibility(visible=True, blockers=()),
    )
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


def _published_certified_item(**overrides):
    """운영 기준 연결만 빼면 공개 가시성 검사를 전부 통과하는 발행 글.

    사유가 여러 개 겹치면 어느 검사가 막았는지 단정할 수 없다 — 이 더블은 승인 기준
    하나만 어긋나게 두어 차단 사유를 격리한다.
    """
    title = "치질 원인과 치료"
    image_url = f"https://storage.googleapis.com/reputation-images/content/{'a' * 64}-ok.png"
    base = dict(
        content_type="DISEASE",
        title=title,
        body="환자 상태에 따라 설명합니다.",
        status=content_api.ContentStatus.PUBLISHED,
        published_at=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
        essence_status=content_api.ESSENCE_STATUS_ALIGNED,
        essence_check_summary={},
        content_philosophy_id=uuid.uuid4(),
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo.do",
            }
        ],
        image_url=image_url,
        image_policy_verified_at=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
        image_content_hash=image_content_hash_from_url(image_url),
        image_subject_hash=image_subject_hash("DISEASE", title),
        image_policy_version=IMAGE_POLICY_VERSION,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )
    base.update(overrides)
    return _content_item(**base)


class _ReviewFakeDB:
    commits = 0

    async def commit(self):
        self.commits += 1


def _stub_post_publish_review(monkeypatch, item, audits):
    async def fake_get_content(db, requested_item_id, requested_hospital_id):
        return item

    async def fake_audit(*_args, **kwargs):
        audits.append(kwargs)

    async def fake_public_philosophy_id(db, requested_hospital_id):
        return None

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "write_audit_log", fake_audit)
    monkeypatch.setattr(
        content_api, "get_public_approved_philosophy_id", fake_public_philosophy_id
    )


async def test_post_publish_review_is_refused_while_the_public_page_withholds_the_item(
    monkeypatch,
):
    """공개 보류 중인 글에 "공개 내용 확인"을 기록하면 admin만 확인 완료로 굳는다(H-01)."""
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    item = _published_certified_item(id=item_id, hospital_id=hospital_id)
    audits = []
    _stub_post_publish_review(monkeypatch, item, audits)
    db = _ReviewFakeDB()

    with pytest.raises(HTTPException) as excinfo:
        await content_api.complete_post_publish_review(
            hospital_id,
            item_id,
            content_api.PostPublishReviewBody(note="공개 페이지 확인"),
            db=db,
        )

    assert excinfo.value.status_code == 409
    assert "공개 페이지에서 보류 중인 글" in excinfo.value.detail
    # 승인 기준 하나만 어긋난 더블이므로 사유도 그 하나뿐이다.
    assert excinfo.value.detail.endswith("현재 승인된 콘텐츠 운영 기준과 다른 기준으로 생성됨")
    # 기록도 감사 로그도 남지 않는다.
    assert item.post_publish_reviewed_at is None
    assert db.commits == 0
    assert audits == []


async def test_withheld_gate_runs_before_the_already_reviewed_shortcut(monkeypatch):
    """이미 확인된 글이라도 공개 보류로 바뀌었으면 "확인 완료"를 돌려주지 않는다.

    멱등 반환이 게이트보다 먼저 서면, 확인 후 제목이 바뀌어 인증이 무효가 된 글도
    admin은 계속 "확인 완료"로 보여 준다 — 공개 페이지에는 없는 글인데.
    """
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    item = _published_certified_item(
        id=item_id,
        hospital_id=hospital_id,
        post_publish_reviewed_at=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        post_publish_reviewed_by="operator@example.com",
    )
    audits = []
    _stub_post_publish_review(monkeypatch, item, audits)
    db = _ReviewFakeDB()

    with pytest.raises(HTTPException) as excinfo:
        await content_api.complete_post_publish_review(
            hospital_id,
            item_id,
            content_api.PostPublishReviewBody(),
            db=db,
        )

    assert excinfo.value.status_code == 409
    assert db.commits == 0
    assert audits == []


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

        async def execute(self, statement):
            # 직렬화가 행 상태의 차단 링크(인시던트·실패한 실행)를 배치 조회한다.
            return SimpleNamespace(all=list, scalar_one_or_none=lambda: None)

        async def get(self, _model, _pk):
            # 직렬화가 병원 공개 게이트를 한 번 읽는다.
            return hospital

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

    async def fake_public_philosophy_id(db, requested_hospital_id):
        # 이 더블 DB에는 승인된 운영 기준 행이 없다. None은 '대조를 건너뛴다'가 아니라
        # PHILOSOPHY_MISMATCH 차단이며, 그래서 PUBLISHED 항목은 '공개 보류'로 직렬화된다
        # (이 테스트들은 그 표시를 보지 않는다).
        return None

    monkeypatch.setattr(
        content_api, "get_public_approved_philosophy_id", fake_public_philosophy_id
    )

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

        async def execute(self, statement):
            # 직렬화가 행 상태의 차단 링크(인시던트·실패한 실행)를 배치 조회한다.
            return SimpleNamespace(all=list, scalar_one_or_none=lambda: None)

        async def get(self, _model, requested_id):
            if requested_id == replaced_item_id:
                return replaced_item
            # 직렬화가 병원 공개 게이트를 한 번 읽는다.
            return hospital if requested_id == hospital.id else None

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

    async def fake_public_philosophy_id(db, requested_hospital_id):
        # 이 더블 DB에는 승인된 운영 기준 행이 없다. None은 '대조를 건너뛴다'가 아니라
        # PHILOSOPHY_MISMATCH 차단이며, 그래서 PUBLISHED 항목은 '공개 보류'로 직렬화된다
        # (이 테스트들은 그 표시를 보지 않는다).
        return None

    monkeypatch.setattr(
        content_api, "get_public_approved_philosophy_id", fake_public_philosophy_id
    )

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

        async def execute(self, statement):
            # 직렬화가 행 상태의 차단 링크(인시던트·실패한 실행)를 배치 조회한다.
            return SimpleNamespace(all=list, scalar_one_or_none=lambda: None)

        async def get(self, _model, _pk):
            # 직렬화가 병원 공개 게이트를 한 번 읽는다.
            return hospital

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

    async def fake_public_philosophy_id(db, requested_hospital_id):
        # 이 더블 DB에는 승인된 운영 기준 행이 없다. None은 '대조를 건너뛴다'가 아니라
        # PHILOSOPHY_MISMATCH 차단이며, 그래서 PUBLISHED 항목은 '공개 보류'로 직렬화된다
        # (이 테스트들은 그 표시를 보지 않는다).
        return None

    monkeypatch.setattr(
        content_api, "get_public_approved_philosophy_id", fake_public_philosophy_id
    )

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
