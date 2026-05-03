import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.admin import exposure_actions as exposure_actions_api
from app.services.exposure_action_engine import build_exposure_recommendations


class _FakeDB:
    def __init__(self, hospital):
        self.hospital = hospital

    async def get(self, model, item_id):
        return self.hospital


def _target(*, priority="HIGH", status="ACTIVE", target_month="2026-05"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        name="강남 치질 수술 추천",
        target_intent="추천형",
        priority=priority,
        status=status,
        target_month=target_month,
        variants=[],
    )


def _record(target_id, *, is_mentioned=False, competitors=None, source_urls=None):
    return SimpleNamespace(
        ai_query_target_id=target_id,
        query_id=None,
        measurement_status="SUCCESS",
        is_mentioned=is_mentioned,
        competitor_mentions=competitors or [],
        source_urls=source_urls,
        measured_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )


def _hospital(hospital_id):
    return SimpleNamespace(
        id=hospital_id,
        name="테스트의원",
        slug="test-clinic",
        treatments=[
            {"name": "치질 수술", "description": "상태별 치료 방향을 설명합니다."}
        ],
    )


def _action(
    *,
    hospital_id,
    action_id=None,
    target_id=None,
    action_type="CONTENT",
    status="OPEN",
    completed_at=None,
):
    target_id = target_id or uuid.uuid4()
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=action_id or uuid.uuid4(),
        hospital_id=hospital_id,
        query_target_id=target_id,
        gap_id=uuid.uuid4(),
        action_type=action_type,
        title="타깃 질의 답변 콘텐츠 보강",
        description="AI 답변에서 병원 언급을 보강할 콘텐츠가 필요합니다.",
        owner="MotionLabs Ops",
        due_month="2026-05",
        status=status,
        linked_content_id=None,
        linked_content=None,
        linked_report_id=None,
        completed_at=completed_at,
        created_at=now,
        updated_at=now,
        gap=SimpleNamespace(
            gap_type="MISSING_MENTION",
            severity="HIGH",
            evidence={"mention_rate": 0.0},
        ),
        query_target=SimpleNamespace(
            id=target_id,
            name="강남 치질 수술 추천",
            target_intent="추천형",
            region_terms=["강남"],
            specialty="대장항문외과",
            condition_or_symptom="치질",
            treatment="치질 수술",
            decision_criteria=["회복 기간", "통증 부담"],
            priority="HIGH",
            status="ACTIVE",
            target_month="2026-05",
            variants=[
                SimpleNamespace(
                    query_text="강남 치질 수술 회복 기간은?",
                    is_active=True,
                    created_at=now,
                )
            ],
        ),
    )


def _content_item(*, hospital_id, content_id=None, target_id=None, action_id=None, status="DRAFT"):
    return SimpleNamespace(
        id=content_id or uuid.uuid4(),
        hospital_id=hospital_id,
        content_type="FAQ",
        sequence_no=1,
        total_count=8,
        scheduled_date=date(2026, 5, 7),
        status=status,
        title=None,
        query_target_id=target_id,
        exposure_action_id=action_id,
        content_brief=None,
        brief_status=None,
        brief_approved_at=None,
        brief_approved_by=None,
    )


class _MutatingDB:
    committed = False
    refreshed = []

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        self.refreshed.append(item)


def test_builds_measurement_action_when_target_has_no_successful_measurements():
    target = _target(priority="HIGH", target_month="2026-05")

    recommendations = build_exposure_recommendations([target], [], today=date(2026, 5, 3))

    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation.gap_type == "NO_SUCCESSFUL_MEASUREMENT"
    assert recommendation.action_type == "MEASUREMENT"
    assert recommendation.severity == "HIGH"
    assert recommendation.due_month == "2026-05"
    assert recommendation.evidence["successful_measurements"] == 0


def test_builds_content_webblog_and_source_actions_from_missing_mentions():
    target = _target(priority="HIGH")
    records = [
        _record(
            target.id,
            is_mentioned=False,
            competitors=[{"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1}],
            source_urls=[],
        ),
        _record(
            target.id,
            is_mentioned=False,
            competitors=[],
            source_urls=None,
        ),
    ]

    recommendations = build_exposure_recommendations([target], records, today=date(2026, 5, 3))

    action_types = [recommendation.action_type for recommendation in recommendations]
    assert action_types == ["CONTENT", "WEBBLOG_IA", "SOURCE"]
    assert recommendations[0].gap_type == "MISSING_MENTION"
    assert recommendations[0].evidence["mention_rate"] == 0.0
    assert recommendations[1].gap_type == "COMPETITOR_VISIBILITY"
    assert recommendations[2].gap_type == "SOURCE_SIGNAL_GAP"


async def test_exposure_actions_endpoint_shape(monkeypatch):
    hospital_id = uuid.uuid4()
    target_id = uuid.uuid4()
    gap_id = uuid.uuid4()
    action_id = uuid.uuid4()
    timestamp = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    async def fake_ensure(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id

    async def fake_list(db, requested_hospital_id, *, limit):
        assert requested_hospital_id == hospital_id
        assert limit == 3
        return [
            SimpleNamespace(
                id=action_id,
                hospital_id=hospital_id,
                query_target_id=target_id,
                gap_id=gap_id,
                action_type="CONTENT",
                title="타깃 질의와 연결된 근거 콘텐츠 보강",
                description="최근 성공 측정에서 병원 언급이 없습니다.",
                owner="MotionLabs Ops",
                due_month="2026-05",
                status="OPEN",
                linked_content_id=None,
                linked_report_id=None,
                completed_at=None,
                created_at=timestamp,
                updated_at=timestamp,
                gap=SimpleNamespace(
                    id=gap_id,
                    gap_type="MISSING_MENTION",
                    severity="HIGH",
                    evidence={"mention_rate": 0.0},
                ),
                query_target=SimpleNamespace(
                    id=target_id,
                    name="강남 치질 수술 추천",
                    target_intent="추천형",
                    priority="HIGH",
                    status="ACTIVE",
                    target_month="2026-05",
                ),
            )
        ]

    monkeypatch.setattr(exposure_actions_api, "ensure_hospital_exposure_actions", fake_ensure)
    monkeypatch.setattr(exposure_actions_api, "list_top_exposure_actions", fake_list)

    response = await exposure_actions_api.get_exposure_actions(
        hospital_id,
        db=_FakeDB(SimpleNamespace(id=hospital_id)),
        limit=3,
    )

    assert response == [
        {
            "id": str(action_id),
            "hospital_id": str(hospital_id),
            "query_target_id": str(target_id),
            "gap_id": str(gap_id),
            "gap_type": "MISSING_MENTION",
            "severity": "HIGH",
            "evidence": {"mention_rate": 0.0},
            "action_type": "CONTENT",
            "title": "타깃 질의와 연결된 근거 콘텐츠 보강",
            "description": "최근 성공 측정에서 병원 언급이 없습니다.",
            "owner": "MotionLabs Ops",
            "due_month": "2026-05",
            "status": "OPEN",
            "linked_content_id": None,
            "linked_content": None,
            "linked_report_id": None,
            "completed_at": None,
            "created_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
            "query_target": {
                "id": str(target_id),
                "name": "강남 치질 수술 추천",
                "target_intent": "추천형",
                "priority": "HIGH",
                "status": "ACTIVE",
                "target_month": "2026-05",
            },
        }
    ]


async def test_get_exposure_action_detail(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id)

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _hospital(hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)

    response = await exposure_actions_api.get_exposure_action(
        hospital_id,
        action_id,
        db=_MutatingDB(),
    )

    assert response["id"] == str(action_id)
    assert response["gap_type"] == "MISSING_MENTION"
    assert response["query_target"]["name"] == "강남 치질 수술 추천"


async def test_patch_exposure_action_updates_work_queue_fields(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    target_id = uuid.uuid4()
    content_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id, target_id=target_id)
    item = _content_item(hospital_id=hospital_id, content_id=content_id)

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _hospital(hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_get_content(db, requested_hospital_id, requested_content_id):
        assert requested_hospital_id == hospital_id
        assert requested_content_id == content_id
        return item

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_get_content_item_or_404", fake_get_content)

    db = _MutatingDB()
    response = await exposure_actions_api.update_exposure_action(
        hospital_id,
        action_id,
        exposure_actions_api.ExposureActionPatch(
            status="IN_PROGRESS",
            owner="Ops Lead",
            due_month="2026-06",
            linked_content_id=content_id,
        ),
        db=db,
    )

    assert db.committed is True
    assert action.status == "IN_PROGRESS"
    assert action.owner == "Ops Lead"
    assert action.due_month == "2026-06"
    assert action.linked_content_id == content_id
    assert item.exposure_action_id == action_id
    assert item.query_target_id == target_id
    assert response["linked_content_id"] == str(content_id)


async def test_patch_linked_content_aligns_existing_query_target(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    target_id = uuid.uuid4()
    previous_target_id = uuid.uuid4()
    content_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id, target_id=target_id)
    item = _content_item(
        hospital_id=hospital_id,
        content_id=content_id,
        target_id=previous_target_id,
    )

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _hospital(hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_get_content(db, requested_hospital_id, requested_content_id):
        assert requested_hospital_id == hospital_id
        assert requested_content_id == content_id
        return item

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_get_content_item_or_404", fake_get_content)

    await exposure_actions_api.update_exposure_action(
        hospital_id,
        action_id,
        exposure_actions_api.ExposureActionPatch(linked_content_id=content_id),
        db=_MutatingDB(),
    )

    assert item.query_target_id == target_id
    assert item.query_target_id != previous_target_id


async def test_patch_linked_content_blocks_measurement_actions(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    content_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id, action_type="MEASUREMENT")
    item = _content_item(hospital_id=hospital_id, content_id=content_id)
    content_was_loaded = False

    async def fake_get_hospital(db, requested_hospital_id):
        return _hospital(requested_hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_get_content(db, requested_hospital_id, requested_content_id):
        nonlocal content_was_loaded
        content_was_loaded = True
        return item

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_get_content_item_or_404", fake_get_content)

    db = _MutatingDB()
    try:
        await exposure_actions_api.update_exposure_action(
            hospital_id,
            action_id,
            exposure_actions_api.ExposureActionPatch(linked_content_id=content_id),
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "content-producing exposure actions" in exc.detail
    else:
        raise AssertionError("Expected PATCH linked_content_id to reject MEASUREMENT actions")

    assert content_was_loaded is False
    assert db.committed is False
    assert action.linked_content_id is None
    assert item.exposure_action_id is None


async def test_patch_linked_content_blocks_published_content(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    content_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id, action_type="CONTENT")
    item = _content_item(hospital_id=hospital_id, content_id=content_id, status="PUBLISHED")

    async def fake_get_hospital(db, requested_hospital_id):
        return _hospital(requested_hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_get_content(db, requested_hospital_id, requested_content_id):
        assert requested_hospital_id == hospital_id
        assert requested_content_id == content_id
        return item

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_get_content_item_or_404", fake_get_content)

    db = _MutatingDB()
    try:
        await exposure_actions_api.update_exposure_action(
            hospital_id,
            action_id,
            exposure_actions_api.ExposureActionPatch(linked_content_id=content_id),
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "published content" in exc.detail
    else:
        raise AssertionError("Expected PATCH linked_content_id to reject published content")

    assert db.committed is False
    assert action.linked_content_id is None
    assert item.exposure_action_id is None


async def test_patch_due_month_rejects_impossible_month(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id)

    async def fake_get_hospital(db, requested_hospital_id):
        return _hospital(requested_hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)

    db = _MutatingDB()
    try:
        await exposure_actions_api.update_exposure_action(
            hospital_id,
            action_id,
            exposure_actions_api.ExposureActionPatch(due_month="2026-13"),
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "due_month" in exc.detail
    else:
        raise AssertionError("Expected invalid due_month to be rejected")

    assert db.committed is False
    assert action.due_month == "2026-05"


async def test_create_brief_without_philosophy_links_draft_and_reports_review_gate(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    target_id = uuid.uuid4()
    hospital = _hospital(hospital_id)
    action = _action(hospital_id=hospital_id, action_id=action_id, target_id=target_id)
    item = _content_item(hospital_id=hospital_id)

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return hospital

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_resolve_slot(db, requested_hospital_id, requested_action, body):
        assert requested_hospital_id == hospital_id
        assert requested_action is action
        assert body.content_type.value == "FAQ"
        return item

    async def fake_get_philosophy(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return None

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_resolve_content_slot_for_brief", fake_resolve_slot)
    monkeypatch.setattr(exposure_actions_api, "_get_approved_philosophy", fake_get_philosophy)

    db = _MutatingDB()
    response = await exposure_actions_api.create_exposure_action_brief(
        hospital_id,
        action_id,
        body=exposure_actions_api.CreateBriefBody(),
        db=db,
    )

    assert db.committed is True
    assert action.linked_content_id == item.id
    assert item.query_target_id == target_id
    assert item.exposure_action_id == action_id
    assert item.brief_status == "DRAFT"
    assert item.brief_approved_at is None
    assert item.brief_approved_by is None
    assert item.content_brief["target_query"] == "강남 치질 수술 회복 기간은?"
    assert item.content_brief["philosophy_reference"] is None
    assert response["content_item"]["brief_status"] == "DRAFT"
    assert response["philosophy_gate"]["has_approved_philosophy"] is False
    assert "review" in response["philosophy_gate"]["message"]


async def test_resolve_brief_slot_reuses_existing_linked_content(monkeypatch):
    hospital_id = uuid.uuid4()
    existing_item_id = uuid.uuid4()
    replacement_item_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id)
    action.linked_content_id = existing_item_id
    existing_item = _content_item(hospital_id=hospital_id, content_id=existing_item_id)
    replacement_item = _content_item(hospital_id=hospital_id, content_id=replacement_item_id)
    looked_up_content_ids = []

    async def fake_get_content(db, requested_hospital_id, requested_content_id):
        assert requested_hospital_id == hospital_id
        looked_up_content_ids.append(requested_content_id)
        if requested_content_id == existing_item_id:
            return existing_item
        return replacement_item

    async def fake_find_slot(db, requested_hospital_id, period_start, period_end):
        raise AssertionError("Existing linked content should be reused before selecting another slot")

    monkeypatch.setattr(exposure_actions_api, "_get_content_item_or_404", fake_get_content)
    monkeypatch.setattr(exposure_actions_api, "_find_available_content_slot", fake_find_slot)

    resolved = await exposure_actions_api._resolve_content_slot_for_brief(
        _MutatingDB(),
        hospital_id,
        action,
        exposure_actions_api.CreateBriefBody(),
    )

    assert resolved is existing_item
    assert looked_up_content_ids == [existing_item_id]


async def test_complete_action_status_sets_and_clears_completed_at(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    completed_at = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    action = _action(hospital_id=hospital_id, action_id=action_id, status="IN_PROGRESS")

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _hospital(hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_utcnow", lambda: completed_at)

    db = _MutatingDB()
    response = await exposure_actions_api.update_exposure_action(
        hospital_id,
        action_id,
        exposure_actions_api.ExposureActionPatch(status="COMPLETED"),
        db=db,
    )

    assert action.status == "COMPLETED"
    assert action.completed_at == completed_at
    assert response["completed_at"] == completed_at.isoformat()

    await exposure_actions_api.update_exposure_action(
        hospital_id,
        action_id,
        exposure_actions_api.ExposureActionPatch(status="OPEN"),
        db=db,
    )

    assert action.status == "OPEN"
    assert action.completed_at is None


async def test_create_brief_returns_409_when_no_content_slot_can_be_resolved(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    action = _action(hospital_id=hospital_id, action_id=action_id)

    async def fake_get_hospital(db, requested_hospital_id):
        return _hospital(requested_hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_resolve_slot(db, requested_hospital_id, requested_action, body):
        return None

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_resolve_content_slot_for_brief", fake_resolve_slot)

    try:
        await exposure_actions_api.create_exposure_action_brief(
            hospital_id,
            action_id,
            body=exposure_actions_api.CreateBriefBody(),
            db=_MutatingDB(),
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "No available non-published content slot" in exc.detail
    else:
        raise AssertionError("Expected create_brief to return 409 when no slot is available")


async def test_create_brief_blocks_measurement_action_before_creating_slot(monkeypatch):
    hospital_id = uuid.uuid4()
    action_id = uuid.uuid4()
    action = _action(
        hospital_id=hospital_id,
        action_id=action_id,
        action_type="MEASUREMENT",
    )
    slot_was_resolved = False

    async def fake_get_hospital(db, requested_hospital_id):
        assert requested_hospital_id == hospital_id
        return _hospital(hospital_id)

    async def fake_get_action(db, requested_hospital_id, requested_action_id):
        assert requested_hospital_id == hospital_id
        assert requested_action_id == action_id
        return action

    async def fake_resolve_slot(db, requested_hospital_id, requested_action, body):
        nonlocal slot_was_resolved
        slot_was_resolved = True
        return _content_item(hospital_id=hospital_id)

    monkeypatch.setattr(exposure_actions_api, "_get_hospital_or_404", fake_get_hospital)
    monkeypatch.setattr(exposure_actions_api, "_get_action_or_404", fake_get_action)
    monkeypatch.setattr(exposure_actions_api, "_resolve_content_slot_for_brief", fake_resolve_slot)

    db = _MutatingDB()
    try:
        await exposure_actions_api.create_exposure_action_brief(
            hospital_id,
            action_id,
            body=exposure_actions_api.CreateBriefBody(),
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "content-producing exposure actions" in exc.detail
        assert "baseline measurement" in exc.detail
    else:
        raise AssertionError("Expected create_brief to reject MEASUREMENT actions")

    assert slot_was_resolved is False
    assert db.committed is False
    assert action.linked_content_id is None
