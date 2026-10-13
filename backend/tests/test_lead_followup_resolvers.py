"""리드 부수효과(접수 알림·안내 문자) 실패 사고는 사람이 그 리드를 맡으면 닫힌다(Phase 1-9c)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.incident_types import IncidentAudience, incident_policy
from app.services.notification_copy import incident_copy
from app.workers.incident_backlog import RESOLVERS

NOW = datetime(2026, 10, 11, tzinfo=UTC)
KINDS = ("LEAD_INTAKE_NOTIFICATION_FAILED", "INQUIRY_SMS_FAILED")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "lead,expected",
    [
        (SimpleNamespace(status="NEW", purged_at=None), None),
        (SimpleNamespace(status="CONTACTED", purged_at=None), "lead_followed_up"),
        (SimpleNamespace(status="NEW", purged_at=NOW), "lead_purged"),
        (None, "lead_purged"),
    ],
)
def test_lead_side_effect_incidents_close_once_a_person_took_the_lead(kind, lead, expected):
    db = SimpleNamespace(get=lambda _model, _id: lead)
    incident = SimpleNamespace(source_id=str(uuid.uuid4()), hospital_id=None)

    assert RESOLVERS[kind](db, incident, NOW) == expected


@pytest.mark.parametrize("kind", KINDS)
def test_malformed_source_is_left_alone(kind):
    db = SimpleNamespace(get=lambda *_a: pytest.fail("no lookup"))
    assert RESOLVERS[kind](db, SimpleNamespace(source_id="garbage"), NOW) is None


@pytest.mark.parametrize("kind", KINDS)
def test_lead_side_effect_incidents_are_operator_work_with_their_own_copy(kind):
    policy = incident_policy(kind)
    assert policy.audience is IncidentAudience.OPERATOR
    assert policy.quiet is False and policy.sweep_owned is False
    assert "상담 요청" in incident_copy(kind).button
