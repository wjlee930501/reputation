from datetime import date
from types import SimpleNamespace

from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.content_target_planner import prepare_automatic_content_brief_sync


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
