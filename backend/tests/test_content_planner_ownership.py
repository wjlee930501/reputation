from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_target_planner as planner


@pytest.mark.parametrize(
    "wrong_field", ["hospital_id", "query_target_id", "linked_content_id", "action_type"]
)
def test_saved_action_cannot_supply_another_hospitals_or_articles_brief(wrong_field):
    hospital_id, target_id, item_id = uuid4(), uuid4(), uuid4()
    action = SimpleNamespace(
        id=uuid4(),
        hospital_id=hospital_id,
        query_target_id=target_id,
        linked_content_id=item_id,
        action_type="CONTENT",
        status="OPEN",
    )
    setattr(action, wrong_field, "MEASUREMENT" if wrong_field == "action_type" else uuid4())
    before = vars(action).copy()
    db = Mock()
    db.get.return_value = action
    db.execute.return_value.scalars.return_value.all.return_value = []
    item = SimpleNamespace(id=item_id, exposure_action_id=action.id)
    selected = planner._load_or_choose_action(
        db, item=item, target=SimpleNamespace(id=target_id), hospital_id=hospital_id
    )
    assert selected is None
    if wrong_field in {"hospital_id", "linked_content_id"}:
        assert vars(action) == before


def test_saved_own_action_can_be_reused_without_selecting_a_new_one():
    hospital_id, target_id, item_id = uuid4(), uuid4(), uuid4()
    action = SimpleNamespace(
        id=uuid4(),
        hospital_id=hospital_id,
        query_target_id=target_id,
        linked_content_id=item_id,
        action_type="CONTENT",
        status="OPEN",
    )
    db = Mock()
    db.get.return_value = action
    assert (
        planner._load_or_choose_action(
            db,
            item=SimpleNamespace(id=item_id, exposure_action_id=action.id),
            target=SimpleNamespace(id=target_id),
            hospital_id=hospital_id,
        )
        is action
    )
    db.execute.assert_not_called()
