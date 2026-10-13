"""예약 자동 발행 보류를 켜 두고 잊지 않게 준비 점검과 일일 요약에 보인다(Phase 1-10)."""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.services.fleet_heartbeat import FleetFacts, build_fleet_heartbeat
from app.utils import production_readiness
from tests.test_notification_labels import _ADMIN, _NOW, _watchdog_report


def test_readiness_reports_the_hold_as_a_fact(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", f"{uuid.uuid4()},{uuid.uuid4()}")

    facts = production_readiness._auto_publish_hold_facts()

    assert facts == {"active": True, "all_hospitals": False, "hospital_count": 2}


def test_heartbeat_names_an_active_hold(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "*")

    message = build_fleet_heartbeat(
        _watchdog_report(publish_due_remaining=0, publish_missing=False),
        FleetFacts(2, 0, 0, 0, 2, 2),
        now=_NOW,
        admin_base_url=_ADMIN,
    ).message

    assert "예약 자동 발행 보류가 켜져 있습니다(전체 병원)" in message.fallback_text


def test_heartbeat_is_silent_when_no_hold(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "")

    message = build_fleet_heartbeat(
        _watchdog_report(publish_due_remaining=0, publish_missing=False),
        FleetFacts(2, 0, 0, 0, 2, 2),
        now=_NOW,
        admin_base_url=_ADMIN,
    ).message

    assert "자동 발행 보류" not in message.fallback_text
