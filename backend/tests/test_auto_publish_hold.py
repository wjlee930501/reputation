"""`AUTO_PUBLISH_HOLD_HOSPITALS` — 예약 자동 발행 보류의 해석과 공용 due 조건.

보류는 `auto_publish_due_predicate` 한 곳에서만 거른다. 꺼져 있을 때(기본 "") 조건은
이전과 **똑같아야** 한다 — 배포만으로 발행 동작이 바뀌면 안 된다.
"""

import logging
import uuid
from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.dialects import postgresql

from app.core.config import Settings, settings
from app.models.content import ContentItem
from app.models.hospital import Hospital
from app.services import post_publish_review_policy as policy
from app.workers import tasks

_TODAY = date(2026, 9, 29)
_A = uuid.UUID("0a000000-0000-0000-0000-00000000000a")
_B = uuid.UUID("0b000000-0000-0000-0000-00000000000b")


def _sql(clause) -> str:
    return str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _pre_hotfix_predicate(today):
    """5862e557의 조건 그대로 — 보류가 꺼져 있을 때 이것과 같은 SQL이어야 한다."""
    return and_(
        ContentItem.scheduled_date <= today,
        ContentItem.scheduled_date >= policy.auto_publish_catchup_start(today),
        ContentItem.status.in_(policy.AUTO_PUBLISHABLE_STATUSES),
    )


def test_default_setting_is_off():
    assert Settings.model_fields["AUTO_PUBLISH_HOLD_HOSPITALS"].default == ""
    hold = policy.parse_auto_publish_hold("")
    assert not hold.active
    assert not hold.holds(_A)


def test_parse_star_holds_every_hospital():
    hold = policy.parse_auto_publish_hold(" * ")
    assert hold.all_hospitals and hold.holds(_A) and hold.holds(uuid.uuid4())


def test_parse_list_tolerates_whitespace_empty_items_and_case():
    hold = policy.parse_auto_publish_hold(f" {_A} ,, {str(_B).upper()} ,")
    assert hold.hospital_ids == frozenset({_A, _B})
    assert not hold.all_hospitals
    assert not hold.holds(uuid.uuid4())


def test_parse_ignores_invalid_ids_with_a_warning(caplog):
    policy.parse_auto_publish_hold.cache_clear()
    with caplog.at_level(logging.WARNING, logger=policy.logger.name):
        hold = policy.parse_auto_publish_hold(f"not-a-uuid, {_A}")
    assert hold.hospital_ids == frozenset({_A})
    assert any("not-a-uuid" in record.getMessage() for record in caplog.records)


def test_only_invalid_ids_behaves_like_off(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "typo")
    assert _sql(policy.auto_publish_due_predicate(_TODAY)) == _sql(
        _pre_hotfix_predicate(_TODAY)
    )


def test_hold_off_keeps_the_due_predicate_identical(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "")
    assert _sql(policy.auto_publish_due_predicate(_TODAY)) == _sql(
        _pre_hotfix_predicate(_TODAY)
    )
    # 발행기가 실제로 쓰는 문장도 5862e557과 같은 SQL이다.
    pre_hotfix_stmt = (
        select(ContentItem.id)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            _pre_hotfix_predicate(_TODAY),
            policy.publicly_operational_hospital_predicate(),
        )
        .order_by(ContentItem.scheduled_date, ContentItem.sequence_no)
    )
    assert _sql(tasks._auto_publish_due_stmt(_TODAY)) == _sql(pre_hotfix_stmt)


def test_hold_star_makes_the_predicate_unsatisfiable(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "*")
    sql = _sql(policy.auto_publish_due_predicate(_TODAY))
    # and_(…, false())는 SQLAlchemy가 상수 false로 접는다 — 어떤 행도 due가 아니다.
    assert sql == "false"


def test_hold_list_excludes_only_those_hospitals(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", f"{_B},{_A}")
    sql = _sql(policy.auto_publish_due_predicate(_TODAY))
    assert sql.startswith(_sql(_pre_hotfix_predicate(_TODAY)))
    assert sql.endswith(f"AND (content_items.hospital_id NOT IN ('{_A}', '{_B}'))")


def test_hold_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "")
    assert not policy.auto_publish_hold().active
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", str(_A))
    assert policy.auto_publish_hold().holds(_A)
