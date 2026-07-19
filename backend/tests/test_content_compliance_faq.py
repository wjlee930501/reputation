"""P1-2 / A1 — FAQ 분리 필드 금지 표현 게이트 + 참고 자료 보정 경로 테스트."""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api


def _hospital(hospital_id=None, **overrides):
    base = dict(
        id=hospital_id or uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status="ONBOARDING",
        site_live=False,
        treatments=[{"name": "어깨 통증 치료", "description": "상태에 따라 설명합니다."}],
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
        title="어깨 통증 진료 안내",
        body="환자 상태에 따라 진료 방향을 설명합니다.",
        meta_description="어깨 통증 진료 안내입니다.",
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
        essence_status="ALIGNED",
        essence_check_summary=None,
        body_updated_at=None,
        references_list=[{"title": "질병관리청 자료", "url": "https://kdca.go.kr/guide"}],
        faq_question=None,
        faq_answer_summary=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _NoExecuteDB:
    """행 잠금 fallback 경로(no execute attr)를 타는 최소 fake."""

    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


class _ScalarNone:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def first(self):
        return None

    def all(self):
        return []


class _PatchDB(_NoExecuteDB):
    """update_content 성공 경로용 — philosophy 조회만 execute로 들어온다."""

    async def execute(self, statement):
        return _ScalarNone()


class _AuditDB(_NoExecuteDB):
    def __init__(self):
        super().__init__()
        self.added = []

    def add(self, value):
        self.added.append(value)


def _wire(monkeypatch, item, hospital):
    async def fake_get_content(db, content_id, hospital_id):
        return item

    async def fake_get_hospital(db, hospital_id):
        return hospital

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "_get_hospital", fake_get_hospital)


async def test_publish_content_blocks_forbidden_expression_in_faq_fields(monkeypatch):
    """발행 게이트가 faq_answer_summary의 금지 표현을 잡아야 한다 (P1-2)."""
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        faq_question="어깨 통증은 어느 과로 가야 하나요?",
        faq_answer_summary="저희는 완치를 보장합니다.",
    )
    _wire(monkeypatch, item, hospital)
    db = _NoExecuteDB()

    with pytest.raises(HTTPException) as exc_info:
        await content_api.publish_content(
            hospital.id, item.id, content_api.PublishBody(published_by="AE"), db=db
        )

    assert exc_info.value.status_code == 400
    assert "완치" in exc_info.value.detail["violations"]
    assert item.essence_status == "NEEDS_ESSENCE_REVIEW"
    assert db.committed is True  # 검수 상태는 기록되어야 함


async def test_update_content_rejects_forbidden_expression_in_faq_patch(monkeypatch):
    """ContentPatch로 FAQ 필드를 수정할 때도 동일한 금지 표현 검사를 받는다 (P1-2)."""
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id)
    _wire(monkeypatch, item, hospital)

    with pytest.raises(HTTPException) as exc_info:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(faq_answer_summary="성공률 100% 치료입니다."),
            db=_NoExecuteDB(),
        )

    assert exc_info.value.status_code == 400
    violations = exc_info.value.detail["violations"]
    assert "성공률" in violations
    assert "100%" in violations
    assert item.faq_answer_summary is None  # 저장되지 않아야 함


async def test_update_content_edits_faq_fields(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id)
    _wire(monkeypatch, item, hospital)

    response = await content_api.update_content(
        hospital.id,
        item.id,
        content_api.ContentPatch(
            faq_question="어깨 통증은 어느 과로 가야 하나요?",
            faq_answer_summary="3주 이상 지속되면 정형외과 진료를 권합니다.",
        ),
        db=_PatchDB(),
    )

    assert item.faq_question == "어깨 통증은 어느 과로 가야 하나요?"
    assert response["faq_question"] == "어깨 통증은 어느 과로 가야 하나요?"
    assert response["faq_answer_summary"] == "3주 이상 지속되면 정형외과 진료를 권합니다."


async def test_update_content_patches_references_with_whitelisted_source(monkeypatch):
    """A1 — references 보정 경로: 화이트리스트 출처는 저장되고 발행 차단이 풀린다."""
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, references_list=[])
    _wire(monkeypatch, item, hospital)

    response = await content_api.update_content(
        hospital.id,
        item.id,
        content_api.ContentPatch(
            references=[{"title": "질병관리청 어깨 통증 가이드", "url": "https://kdca.go.kr/shoulder"}],
        ),
        db=_PatchDB(),
    )

    assert len(item.references_list) == 1
    assert item.references_list[0]["title"] == "질병관리청 어깨 통증 가이드"
    assert item.references_list[0]["url"] == "https://kdca.go.kr/shoulder"
    assert response["references"][0]["url"] == "https://kdca.go.kr/shoulder"
    assert response["compliance"]["references_count"] == 1


async def test_update_content_rejects_non_whitelisted_reference(monkeypatch):
    """A1 — 화이트리스트 밖 출처는 조용히 떨구지 않고 400으로 명시 거절."""
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, references_list=[])
    _wire(monkeypatch, item, hospital)

    with pytest.raises(HTTPException) as exc_info:
        await content_api.update_content(
            hospital.id,
            item.id,
            content_api.ContentPatch(
                references=[{"title": "개인 블로그", "url": "https://my-blog.example.com/post"}],
            ),
            db=_PatchDB(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["accepted_count"] == 0
    assert item.references_list == []


async def test_reschedule_content_moves_unpublished_slot_and_audits(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        scheduled_date=date(2026, 7, 4),
        carried_over_from=None,
    )
    _wire(monkeypatch, item, hospital)
    db = _AuditDB()

    response = await content_api.reschedule_content(
        hospital.id,
        item.id,
        content_api.ContentRescheduleBody(scheduled_date=date(2099, 8, 1)),
        db=db,
    )

    assert item.scheduled_date == date(2099, 8, 1)
    assert item.carried_over_from == date(2026, 7, 4)
    assert response["scheduled_date"] == "2099-08-01"
    assert db.committed is True
    assert db.added[0].action == "reschedule_content"


async def test_reschedule_content_rejects_published_item(monkeypatch):
    hospital = _hospital()
    item = _content_item(hospital_id=hospital.id, status=content_api.ContentStatus.PUBLISHED)
    _wire(monkeypatch, item, hospital)

    with pytest.raises(HTTPException) as exc_info:
        await content_api.reschedule_content(
            hospital.id,
            item.id,
            content_api.ContentRescheduleBody(scheduled_date=date(2099, 8, 1)),
            db=_AuditDB(),
        )

    assert exc_info.value.status_code == 409


async def test_cancel_content_is_terminal_and_audited(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        generation_claimed_at="in-progress",
    )
    _wire(monkeypatch, item, hospital)
    db = _AuditDB()

    response = await content_api.cancel_content(hospital.id, item.id, db=db)

    assert item.status == content_api.ContentStatus.CANCELLED
    assert item.generation_claimed_at is None
    assert response["status"] == "CANCELLED"
    assert response["compliance"]["publishable"] is False
    assert db.added[0].action == "cancel_content"
    assert db.committed is True
