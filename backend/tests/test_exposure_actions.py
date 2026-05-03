import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

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
