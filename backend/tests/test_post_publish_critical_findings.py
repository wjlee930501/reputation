"""D35(2026-10-14): 공개 글의 사후 검수 지적 — Critical은 "마케터 검토 필요" 한 번, 아니면 조용히 닫는다.

글은 어느 경우에도 자동으로 내리지 않는다. D37로 검수 장애 때 먼저 발행된 글은 사후 검수 맨 앞이다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.content import ContentStatus
from app.services.content_ai_review import candidate_sha256
from app.services.content_publication import AI_REVIEW_DEFERRED_KEY
from app.services.incident_types import (
    IncidentAudience,
    incident_audience,
    incident_is_quiet,
    incident_policy,
)
from app.services.notification_labels import NotificationLabel, label_for_event
from app.workers import incident_backlog
from app.workers import post_publish_ai_review as sweep
from tests.test_post_publish_ai_review import _published, _review

NOW = datetime(2026, 10, 20, 3, 10, tzinfo=UTC)


def _flag(item, *findings, finished=True, senior=False):
    marker = {
        "status": "FLAGGED",
        "candidate_sha256": candidate_sha256(item),
        "findings": [finding["message"] for finding in findings],
        "structured_findings": list(findings),
        "correction": {"finished": finished, "passes": 2},
    }
    if senior:
        marker[sweep.POST_PUBLISH_SENIOR_REREVIEW_KEY] = {"at": NOW.isoformat()}
    item.essence_check_summary = {sweep.POST_PUBLISH_FLAG_KEY: marker}
    return item


def _finding(severity, kind, message="지적", quote="q"):
    return {"severity": severity, "kind": kind, "message": message, "quote": quote}


class _DB:
    def __init__(self):
        self.commits = 0

    def rollback(self):
        pass

    def commit(self):
        self.commits += 1


@pytest.fixture
def calls(monkeypatch):
    recorded = {"critical": [], "recovered": 0}
    monkeypatch.setattr(
        sweep,
        "_open_critical_incident",
        lambda item, hospital, run_async, **k: recorded["critical"].append(
            (item.id, k.get("needs_human_reason"))
        ),
    )
    monkeypatch.setattr(
        sweep,
        "_recover_flag_incident",
        lambda *_a, **_k: recorded.__setitem__("recovered", recorded["recovered"] + 1),
    )
    return recorded


def _item():
    item = _published()
    item.id = uuid.uuid4()
    return item


def test_critical_flag_asks_the_marketer_and_keeps_the_post_public(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "MEDICAL_SAFETY", "위험한 오정보"))
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)

    sweep._settle_flag(_DB(), item, object(), asyncio.run, needs_human_reason="blocked", now=NOW)

    assert calls["critical"] == [(item.id, "blocked")]
    assert item.status == ContentStatus.PUBLISHED
    assert item.post_publish_reviewed_at is None
    # 옛 조용한 사고는 더 쌓지 않는다.
    assert calls["recovered"] == 1


def test_emergency_guidance_flag_is_critical_even_when_uncertain(monkeypatch, calls):
    item = _flag(
        _item(), _finding("UNCERTAIN", "MEDICAL_SAFETY", "검은 변이면 119·응급실 안내가 필요합니다")
    )
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)

    sweep._settle_flag(_DB(), item, object(), asyncio.run, needs_human_reason="blocked", now=NOW)

    assert len(calls["critical"]) == 1


def test_noncritical_flag_closes_quietly_once_correction_is_finished(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학"))
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)
    db = _DB()

    sweep._settle_flag(db, item, object(), asyncio.run, needs_human_reason="blocked", now=NOW)

    assert calls["critical"] == []
    assert item.post_publish_reviewed_at == NOW
    assert item.post_publish_reviewed_by == sweep.POST_PUBLISH_NONCRITICAL_CLOSER
    assert item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["closed"]["reason"] == "NONCRITICAL"
    assert item.status == ContentStatus.PUBLISHED and db.commits == 1


def test_noncritical_flag_waits_while_correction_can_still_run(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT"), finished=False)
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)

    sweep._settle_flag(_DB(), item, object(), asyncio.run, now=NOW)

    assert calls["critical"] == []
    assert item.post_publish_reviewed_at is None


def test_uncertain_only_flag_waits_for_the_senior_rereview(monkeypatch, calls):
    item = _flag(_item(), _finding("UNCERTAIN", "HOSPITAL_FACT"))
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)

    sweep._settle_flag(_DB(), item, object(), asyncio.run, needs_human_reason="blocked", now=NOW)

    assert item.post_publish_reviewed_at is None

    _flag(item, _finding("UNCERTAIN", "HOSPITAL_FACT"), senior=True)
    sweep._settle_flag(_DB(), item, object(), asyncio.run, needs_human_reason="blocked", now=NOW)

    assert item.post_publish_reviewed_at == NOW
    assert calls["critical"] == []


def test_noncritical_close_refuses_a_changed_flag(monkeypatch):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT"))
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)

    assert not sweep._close_noncritical_flag(_DB(), item.id, "another-body", NOW)
    assert item.post_publish_reviewed_at is None


def test_critical_incident_type_is_an_operator_slack_with_the_error_label() -> None:
    kind = sweep.POST_PUBLISH_CRITICAL_INCIDENT_TYPE

    assert incident_audience(kind) is IncidentAudience.OPERATOR
    assert not incident_is_quiet(kind)
    assert not incident_policy(kind).sweep_owned
    assert label_for_event("INCIDENT_OPEN") is NotificationLabel.ERROR
    assert kind in incident_backlog.RESOLVERS
    assert incident_backlog.RESOLVERS["POST_PUBLISH_REVIEW_FLAGGED"] is incident_backlog.RESOLVERS[kind]


def test_critical_incident_copy_says_marketer_review_and_keeps_the_post(monkeypatch) -> None:
    item = _flag(_item(), _finding("HARD", "MEDICAL_SAFETY", "흑색변을 소화불량으로 안내"))
    captured = {}

    async def fake_open(**kwargs):
        captured.update(kwargs)
        return uuid.uuid4()

    import app.services.ops_incident_alerts as alerts

    monkeypatch.setattr(alerts, "open_ops_incident", fake_open)
    hospital = SimpleNamespace(id=uuid.uuid4(), name="강남병원")

    sweep._open_critical_incident(item, hospital, asyncio.run)

    assert captured["incident_type"] == "POST_PUBLISH_CRITICAL_FINDING"
    assert captured["pipeline"] == sweep.POST_PUBLISH_CRITICAL_INCIDENT_PIPELINE
    assert captured["problem"].startswith("마케터 검토 필요")
    assert "흑색변" in captured["problem"]
    assert "자동으로 내리지 않았습니다" in captured["customer_impact"]
    assert captured.get("notify", True) is True


class _Session:
    def __init__(self, item):
        self.item = item

    def get(self, _model, _id):
        return self.item


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda item: setattr(item, "status", ContentStatus.WITHHELD), "content_not_public"),
        (lambda item: setattr(item, "post_publish_reviewed_at", NOW), "post_publish_reviewed"),
        (lambda item: setattr(item, "essence_check_summary", {}), "flag_cleared"),
        (
            lambda item: _flag(item, _finding("HARD", "HOSPITAL_FACT")),
            "flag_not_critical",
        ),
        (lambda item: None, None),
    ],
)
def test_resolver_closes_only_when_the_critical_flag_is_settled(mutate, expected) -> None:
    item = _flag(_item(), _finding("HARD", "MEDICAL_SAFETY"))
    mutate(item)
    incident = SimpleNamespace(source_id=str(item.id))

    assert incident_backlog._post_publish_flag_settled(_Session(item), incident, NOW) == expected


def test_deferred_posts_lead_the_sweep_queue() -> None:
    sql = str(
        sweep._deferred_review_stmt(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "'UNAVAILABLE'" in sql
    assert "post_publish_reviewed_at IS NULL" in sql
    assert "ORDER BY content_items.published_at" in sql


def test_pass_clears_the_deferral_marker() -> None:
    item = _published()
    item.essence_check_summary = {
        "ai_review": {"status": "UNAVAILABLE"},
        AI_REVIEW_DEFERRED_KEY: {"candidate_sha256": "x"},
    }

    assert sweep.apply_review_outcome(item, _review(item), now=NOW) == "REVIEWED"
    assert AI_REVIEW_DEFERRED_KEY not in item.essence_check_summary
    assert item.essence_check_summary["ai_review"]["status"] == "PASS"
