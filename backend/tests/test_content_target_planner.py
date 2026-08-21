from datetime import date
from types import SimpleNamespace

from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.content_target_planner import (
    _target_matches_focus,
    prepare_automatic_content_brief_sync,
)


def test_existing_approved_brief_receives_current_planned_publish_date() -> None:
    item = SimpleNamespace(
        brief_status=BRIEF_STATUS_APPROVED,
        content_brief={"target_query": "수원 변비 검사"},
        scheduled_date=date(2026, 7, 31),
    )

    result = prepare_automatic_content_brief_sync(
        None,
        item=item,
        hospital=SimpleNamespace(),
        philosophy=SimpleNamespace(),
    )

    assert result["planned_publish_date"] == "2026-07-31"
    assert "planned_publish_date" not in item.content_brief


def test_existing_approved_brief_outside_new_focus_is_replanned() -> None:
    item = SimpleNamespace(
        id="content-1",
        brief_status=BRIEF_STATUS_APPROVED,
        content_brief={"target_query": "감기 수액 치료", "content_focus_topic": "외상"},
        scheduled_date=date(2026, 8, 25),
        content_type=SimpleNamespace(value="HEALTH"),
        title=None,
    )
    hospital = SimpleNamespace(
        id="hospital-1",
        content_focus_topics=["정형외과", "신경외과", "통증의학과", "외상"],
        treatments=[],
    )

    result = prepare_automatic_content_brief_sync(
        None,
        item=item,
        hospital=hospital,
        philosophy=SimpleNamespace(id="philosophy-1"),
    )

    assert result["target_query"] != "감기 수액 치료"
    assert result["content_focus_topic"] in hospital.content_focus_topics
    assert item.brief_status == BRIEF_STATUS_APPROVED


def test_measured_target_must_match_a_director_approved_content_focus() -> None:
    matching = SimpleNamespace(
        name="외상 후 통증 진료",
        treatment="외상치료",
        condition_or_symptom="통증",
        target_intent="내원 전 안내",
    )
    unrelated = SimpleNamespace(
        name="감기 증상",
        treatment="수액 치료",
        condition_or_symptom="발열",
        target_intent="내원 전 안내",
    )

    assert _target_matches_focus(matching, ("정형외과", "통증의학과", "외상")) is True
    assert _target_matches_focus(unrelated, ("정형외과", "통증의학과", "외상")) is False
