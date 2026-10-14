"""D35(2026-10-14): 공개 글의 사후 검수 지적 — Critical은 "마케터 검토 필요" 글당 평생 한 번, 아니면 조용히 닫는다.

글은 어느 경우에도 자동으로 내리지 않는다. 알림 의도는 Critical 판정을 저장하는 같은 쓰기에 남고(판정
스냅샷), 이후의 FLAG·PASS·교정과 무관하게 전달·종결된다. 배포 전부터 남은 Critical은 요약 한 통으로
알린다. D37로 검수 장애 때 먼저 발행된 글은 사후 검수 첫 단계다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.content import ContentStatus
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReviewStatus,
    candidate_sha256,
)
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
PHILOSOPHY = "본원은 환자 한 분 한 분의 이야기를 끝까지 듣는 것을 진료 원칙으로 삼습니다."
KEY = sweep.POST_PUBLISH_CRITICAL_ALERT_KEY


def _item(**extra):
    item = _published()
    item.id = uuid.uuid4()
    item.body = f"## 안내\n{PHILOSOPHY}\n증상이 이어지면 진료를 받으세요."
    item.hospital = SimpleNamespace(id=uuid.uuid4(), name="강남병원")
    for name, value in extra.items():
        setattr(item, name, value)
    return item


def _finding(
    severity, kind, message="승인 자료에 없는 진료 원칙 문장", quote=PHILOSOPHY, critical=False
):
    return {
        "severity": severity, "kind": kind, "message": message, "quote": quote, "critical": critical
    }


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
    summary = dict(item.essence_check_summary or {})
    summary[sweep.POST_PUBLISH_FLAG_KEY] = marker
    item.essence_check_summary = summary
    return item


def _critical_review(item, message="흑색변을 소화불량으로 안내했습니다"):
    return _review(
        item,
        status=ContentAiReviewStatus.REVISE,
        findings=[
            ContentAiFinding(
                ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, message, quote="q"
            )
        ],
    )


class _DB:
    def __init__(self):
        self.commits = 0

    def rollback(self):
        pass

    def commit(self):
        self.commits += 1


@pytest.fixture
def calls(monkeypatch):
    recorded = {"critical": [], "recovered": 0, "alerts": []}
    monkeypatch.setattr(
        sweep, "_enqueue_notification", lambda _db, intent, _now: recorded["alerts"].append(intent)
    )
    monkeypatch.setattr(
        sweep,
        "_open_critical_incident",
        lambda item, hospital, run_async, **k: recorded["critical"].append(
            (item.id, (k.get("snapshot") or {}).get("title"))
        ),
    )
    monkeypatch.setattr(
        sweep,
        "_recover_flag_incident",
        lambda *_a, **_k: recorded.__setitem__("recovered", recorded["recovered"] + 1),
    )
    return recorded


def _lock_to(monkeypatch, item):
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)


# ── 끝 처리: Critical은 알리고, 아니면 조용히 닫는다 ─────────────────────────────────


def test_critical_flag_asks_the_marketer_and_keeps_the_post_public(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "MEDICAL_SAFETY", "위험한 오정보"))
    _lock_to(monkeypatch, item)

    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert [item_id for item_id, _title in calls["critical"]] == [item.id]
    [alert] = calls["alerts"]
    assert alert.notification_type == sweep.POST_PUBLISH_CRITICAL_ALERT_NOTIFICATION_TYPE
    assert alert.dedupe_key == f"post-publish-critical:{item.id}" and alert.incident_id is None
    assert item.essence_check_summary[KEY]["enqueued_at"] == NOW.isoformat()
    assert item.status == ContentStatus.PUBLISHED and item.post_publish_reviewed_at is None
    assert calls["recovered"] == 1  # 옛 조용한 사고는 더 쌓지 않는다


def test_noncritical_flag_closes_quietly_once_correction_is_finished(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT"))
    _lock_to(monkeypatch, item)
    db = _DB()

    sweep._settle_flag(db, item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert calls["critical"] == []
    assert item.post_publish_reviewed_at == NOW
    assert item.post_publish_reviewed_by == sweep.POST_PUBLISH_NONCRITICAL_CLOSER
    assert item.essence_check_summary[sweep.POST_PUBLISH_FLAG_KEY]["closed"]["reason"] == "NONCRITICAL"


def test_noncritical_flag_waits_while_correction_can_still_run(monkeypatch, calls):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT"), finished=False)
    _lock_to(monkeypatch, item)

    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, now=NOW)

    assert calls["critical"] == [] and item.post_publish_reviewed_at is None


def test_uncertain_only_flag_waits_for_the_senior_rereview(monkeypatch, calls):
    item = _flag(_item(), _finding("UNCERTAIN", "HOSPITAL_FACT"))
    _lock_to(monkeypatch, item)

    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)
    assert item.post_publish_reviewed_at is None

    _flag(item, _finding("UNCERTAIN", "HOSPITAL_FACT"), senior=True)
    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert item.post_publish_reviewed_at == NOW and calls["critical"] == []


@pytest.mark.parametrize(
    "finding",
    [
        # Codex 2차 재현 — 영어·혼합 언어로 쓴 의료 지적은 종류와 무관하게 Critical이다.
        _finding("HARD", "STYLE", "Missing emergency care instructions for chest pain"),
        _finding("HARD", "HOSPITAL_FACT", "진료 원칙: contraindication for pregnancy is omitted"),
        _finding("UNCERTAIN", "MEDICAL_SAFETY", "확신 부족"),
        _finding("HARD", "HOSPITAL_FACT", "제목 지적", quote="치질 진료 전"),
        _finding("HARD", "HOSPITAL_FACT", "인용 없음", quote=""),
    ],
)
def test_medical_or_unverifiable_flags_are_never_closed_quietly(monkeypatch, calls, finding):
    item = _flag(_item(), finding)
    _lock_to(monkeypatch, item)

    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert item.post_publish_reviewed_at is None
    assert len(calls["critical"]) == 1


def test_noncritical_close_refuses_a_changed_flag(monkeypatch):
    item = _flag(_item(), _finding("HARD", "HOSPITAL_FACT"))
    _lock_to(monkeypatch, item)

    assert not sweep._close_noncritical_flag(_DB(), item.id, "another-body", NOW)
    assert item.post_publish_reviewed_at is None


# ── 글당 평생 한 번의 알림 의도(Codex 2차 C·D) ──────────────────────────────────────


def test_storing_a_critical_verdict_requests_the_alert_with_a_snapshot() -> None:
    item = _item()

    assert sweep.apply_review_outcome(item, _critical_review(item), now=NOW) == "FLAGGED"

    alert = item.essence_check_summary[KEY]
    assert alert["requested_at"] == NOW.isoformat()
    assert alert["critical_alert_sent_at"] is None and alert["closed_at"] is None
    assert alert["snapshot"]["title"] == item.title
    assert alert["snapshot"]["findings"] == ["흑색변을 소화불량으로 안내했습니다"]


def test_noncritical_verdict_requests_no_alert() -> None:
    item = _item()
    review = _review(
        item,
        status=ContentAiReviewStatus.REVISE,
        findings=[
            ContentAiFinding(
                ContentAiFindingSeverity.HARD,
                ContentAiFindingKind.HOSPITAL_FACT,
                "승인 자료에 없는 진료 원칙 문장",
                quote=PHILOSOPHY,
                critical=False,
            )
        ],
    )

    assert sweep.apply_review_outcome(item, review, now=NOW) == "FLAGGED"
    assert KEY not in item.essence_check_summary


def test_one_alert_per_post_lifetime_even_when_the_body_changes(monkeypatch, calls):
    """대표 결정 "글당 한 번" — 본문이 바뀌어 새 Critical이 나와도 다시 알리지 않는다."""

    item = _item()
    sweep.apply_review_outcome(item, _critical_review(item), now=NOW)
    _lock_to(monkeypatch, item)
    assert sweep.ensure_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "ENQUEUED"

    item.body = item.body + "\n새 문단입니다."
    sweep.apply_review_outcome(item, _critical_review(item, "다른 위험한 오정보"), now=NOW)
    assert sweep.ensure_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "SKIPPED"
    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert len(calls["critical"]) == 1 and len(calls["alerts"]) == 1


def test_delivery_uses_the_snapshot_even_after_the_flag_is_gone(monkeypatch, calls):
    """Codex 2차 재현: 알림 기록이 실패한 뒤 PASS로 바뀌어도 의도는 판정 스냅샷으로 전달된다."""

    item = _item()
    sweep.apply_review_outcome(item, _critical_review(item), now=NOW)
    _lock_to(monkeypatch, item)
    item.essence_check_summary = {
        k: v for k, v in item.essence_check_summary.items() if k != sweep.POST_PUBLISH_FLAG_KEY
    }

    assert sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "ENQUEUED"
    assert calls["critical"] == [(item.id, item.title)]
    assert item.title in calls["alerts"][0].message.payload_json()


def test_enqueue_failure_keeps_the_intent_pending_and_never_closes_it(monkeypatch, calls):
    """outbox에 넣지 못하면 의도는 남는다 — 몇 번을 실패해도 닫지 않고 다음 실행이 다시 넣는다."""

    item = _item()
    sweep.apply_review_outcome(item, _critical_review(item), now=NOW)
    _lock_to(monkeypatch, item)

    def broken(*_a, **_k):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(sweep, "_enqueue_notification", broken)
    for _ in range(10):
        with pytest.raises(RuntimeError):
            sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW)
    alert = item.essence_check_summary[KEY]
    assert alert["enqueued_at"] is None and alert["closed_at"] is None

    recorded = []
    monkeypatch.setattr(sweep, "_enqueue_notification", lambda _db, intent, _now: recorded.append(intent))
    assert sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "ENQUEUED"
    assert len(recorded) == 1 and item.essence_check_summary[KEY]["enqueued_at"] == NOW.isoformat()


def test_incident_failure_does_not_undo_the_enqueued_alert(monkeypatch, calls):
    item = _item()
    sweep.apply_review_outcome(item, _critical_review(item), now=NOW)
    _lock_to(monkeypatch, item)

    def broken(*_a, **_k):
        raise RuntimeError("incident store down")

    monkeypatch.setattr(sweep, "_open_critical_incident", broken)

    assert sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "ENQUEUED"
    assert len(calls["alerts"]) == 1


def test_alert_intent_retries_without_a_cap_and_is_not_tied_to_an_incident(calls) -> None:
    from app.services.notification_contracts import UNBOUNDED_NOTIFICATION_ATTEMPTS
    from app.services.notification_transport import retry_delay

    item = _item()
    intent = sweep.build_critical_alert(item, item.hospital, {"title": "제목", "findings": ["오정보"]})

    assert intent.max_attempts == UNBOUNDED_NOTIFICATION_ATTEMPTS and intent.incident_id is None
    assert intent.hospital_id == item.hospital.id
    assert intent.message.fallback_text.startswith("[Error : 오류 발생] 마케터 검토 필요")
    # 시간당 한 번보다 자주 보내지 않고, 큰 시도 수에서도 간격 계산이 커지지 않는다.
    assert retry_delay(UNBOUNDED_NOTIFICATION_ATTEMPTS - 1, None) == 3600
    assert retry_delay(1, None) == 15


def test_an_unpublished_post_closes_its_alert(monkeypatch, calls):
    item = _item()
    sweep.apply_review_outcome(item, _critical_review(item), now=NOW)
    _lock_to(monkeypatch, item)
    item.status = ContentStatus.WITHHELD

    assert sweep._deliver_critical_alert(_DB(), item.id, asyncio.run, now=NOW) == "CLOSED"
    assert item.essence_check_summary[KEY]["closed_reason"] == "not_public"
    assert calls["critical"] == []


def test_pending_queue_skips_sent_and_closed_intents_oldest_first() -> None:
    sql = str(
        sweep._pending_critical_alert_stmt(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "enqueued_at" in sql and "critical_alert_sent_at" in sql and "closed_at" in sql
    assert "ORDER BY" in sql and "requested_at" in sql.split("ORDER BY")[1]
    # 공개 상태로 거르지 않는다 — 공개 중이 아닌 의도는 전달기가 닫아 자리를 비운다.
    assert "content_items.status" not in sql.split("FROM")[1].split("ORDER BY")[0]


def test_the_critical_alert_survives_gate_rewrites_of_the_summary() -> None:
    from app.services import content_publication

    item = _item()
    item.essence_check_summary = {KEY: {"critical_alert_sent_at": "t"}}
    assessment = content_publication.PublicationAssessment(
        publishable=True,
        code=None,
        message=None,
        violations=(),
        essence_status="ALIGNED",
        essence_summary={},
        philosophy_id=None,
    )

    content_publication.apply_publication_assessment(item, assessment)

    assert item.essence_check_summary[KEY]["critical_alert_sent_at"] == "t"


# ── 배포 전 백로그: 요약 한 통(Codex 2차 E) ──────────────────────────────────────────


class _BacklogDB(_DB):
    """id 커서 쪽 조회를 흉내 낸다 — 호출마다 다음 쪽을 돌려준다."""

    def __init__(self, rows, page):
        super().__init__()
        self.pages = [rows[i : i + page] for i in range(0, len(rows), page)] + [[]]
        self.queries = 0

    def execute(self, _stmt):
        rows = self.pages[min(self.queries, len(self.pages) - 1)]
        self.queries += 1
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def test_backlog_is_announced_in_one_digest_across_every_page(monkeypatch, calls):
    """커서로 전부 훑는다 — 600건(쪽 200)이어도 요약은 한 통, 표시는 30줄 + "외 N건"."""

    critical = [
        _flag(_item(), _finding("HARD", "MEDICAL_SAFETY", "위험한 오정보")) for _ in range(560)
    ]
    legacy = [  # v2 기록(`critical` 없음)은 Critical로 읽는다 — 다시 사지 않는다.
        _flag(_item(), {"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "m", "quote": PHILOSOPHY})
        for _ in range(5)
    ]
    noncritical = [_flag(_item(), _finding("HARD", "HOSPITAL_FACT")) for _ in range(35)]
    db = _BacklogDB([*critical, *legacy, *noncritical], page=200)
    monkeypatch.setattr(sweep, "_BACKLOG_PAGE", 200)

    assert sweep.send_critical_backlog_digest(db, now=NOW) == 565

    [intent] = calls["alerts"]
    assert db.queries == 4 and db.commits == 1  # 3쪽 + 빈 쪽
    assert intent.notification_type == sweep.POST_PUBLISH_CRITICAL_DIGEST_NOTIFICATION_TYPE
    payload = intent.message.payload_json()
    assert "마케터 검토 필요 565건" in payload and "외 535건" in payload
    assert payload.count("「") == 30
    assert intent.message.fallback_text.startswith("[Error : 오류 발생]")
    for item in [*critical, *legacy]:
        assert item.essence_check_summary[KEY]["enqueued_at"] == NOW.isoformat()
        assert item.essence_check_summary[KEY]["via"] == "BACKLOG_DIGEST"
    assert all(KEY not in item.essence_check_summary for item in noncritical)


def test_no_backlog_no_digest(monkeypatch, calls):
    assert sweep.send_critical_backlog_digest(_BacklogDB([], page=200), now=NOW) == 0
    assert calls["alerts"] == []


def test_backlog_scan_excludes_posts_with_any_alert_intent() -> None:
    sql = str(
        sweep._backlog_page_stmt(uuid.UUID(int=1), 5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "post_publish_critical_alert" in sql and "FLAGGED" in sql
    assert "content_items.id >" in sql and "ORDER BY content_items.id" in sql


# ── 등록·문구·근거 ───────────────────────────────────────────────────────────────


def test_critical_incident_type_is_an_operator_slack_with_the_error_label() -> None:
    kind = sweep.POST_PUBLISH_CRITICAL_INCIDENT_TYPE

    assert incident_audience(kind) is IncidentAudience.OPERATOR
    assert not incident_is_quiet(kind)
    assert not incident_policy(kind).sweep_owned
    assert label_for_event("INCIDENT_OPEN") is NotificationLabel.ERROR
    assert label_for_event("POST_PUBLISH_CRITICAL_DIGEST") is NotificationLabel.ERROR
    assert label_for_event("POST_PUBLISH_CRITICAL_ALERT") is NotificationLabel.ERROR
    resolvers = incident_backlog.active_resolvers()
    assert resolvers["POST_PUBLISH_REVIEW_FLAGGED"] is resolvers[kind]


def test_critical_incident_copy_uses_the_snapshot(monkeypatch) -> None:
    item = _item()
    captured = {}

    async def fake_open(**kwargs):
        captured.update(kwargs)
        return uuid.uuid4()

    import app.services.ops_incident_alerts as alerts

    monkeypatch.setattr(alerts, "open_ops_incident", fake_open)

    sweep._open_critical_incident(
        item, item.hospital, asyncio.run, snapshot={"title": "옛 제목", "findings": ["흑색변 오정보"]}
    )

    assert captured["incident_type"] == "POST_PUBLISH_CRITICAL_FINDING"
    assert captured["problem"].startswith("마케터 검토 필요: 공개된 글 「옛 제목」")
    assert "흑색변 오정보" in captured["problem"]
    assert "자동으로 내리지 않았습니다" in captured["customer_impact"]
    # Slack은 전용 알림이 맡는다 — 사고는 알리지 않는다(복구가 알림을 종결할 수 없다).
    assert captured["notify"] is False


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
        (lambda item: _flag(item, _finding("HARD", "HOSPITAL_FACT")), "flag_not_critical"),
        (lambda item: None, None),
    ],
)
def test_resolver_closes_only_when_the_critical_flag_is_settled(mutate, expected) -> None:
    item = _flag(_item(), _finding("HARD", "MEDICAL_SAFETY"))
    mutate(item)
    incident = SimpleNamespace(source_id=str(item.id))

    assert incident_backlog._post_publish_flag_settled(_Session(item), incident, NOW) == expected


# ── D37 선발행 ───────────────────────────────────────────────────────────────────


def test_deferred_posts_query() -> None:
    sql = str(
        sweep._deferred_review_stmt(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "'UNAVAILABLE'" in sql and "post_publish_reviewed_at IS NULL" in sql
    assert "ORDER BY content_items.published_at" in sql


def test_pass_clears_the_deferral_marker() -> None:
    item = _published()
    item.essence_check_summary = {
        "ai_review": {"status": "UNAVAILABLE"},
        AI_REVIEW_DEFERRED_KEY: {"candidate_sha256": "x"},
    }

    assert sweep.apply_review_outcome(item, _review(item), now=NOW) == "REVIEWED"
    assert AI_REVIEW_DEFERRED_KEY not in item.essence_check_summary


@pytest.fixture(autouse=True)
def _tiers_and_alerts_on(monkeypatch):
    """이 파일은 기본 꺼짐인 W4 등급·D35 알림을 켠 동작을 고정한다(꺼진 동작은 main과 같다)."""

    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", True)
    monkeypatch.setattr(_settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", True)


# ── 기본 꺼짐: 알림·등급을 끄면 종전(main)과 같다 ──────────────────────────────────


def test_alerts_switched_off_leave_the_legacy_quiet_incident(monkeypatch, calls) -> None:
    from app.core.config import Settings
    from app.core.config import settings as _settings

    # 2026-10-14 재설계 뒤 기본 켜짐이다. 끄면(롤백 스위치) 종전(main)과 같다.
    assert Settings.model_fields["POST_PUBLISH_CRITICAL_ALERTS_ENABLED"].default is True
    assert Settings.model_fields["CONTENT_FINDING_TIERS_ENABLED"].default is True
    monkeypatch.setattr(_settings, "POST_PUBLISH_CRITICAL_ALERTS_ENABLED", False)
    legacy = []
    monkeypatch.setattr(
        sweep, "_open_flag_incident", lambda item, *_a, **k: legacy.append(k.get("needs_human_reason"))
    )
    item = _item()

    assert sweep.apply_review_outcome(item, _critical_review(item), now=NOW) == "FLAGGED"
    assert KEY not in item.essence_check_summary  # 알림 의도를 만들지 않는다
    sweep._settle_flag(_DB(), item, item.hospital, asyncio.run, needs_human_reason="blocked", now=NOW)

    assert legacy == ["blocked"] and calls["critical"] == [] and calls["recovered"] == 0
    assert item.post_publish_reviewed_at is None
    incident = SimpleNamespace(source_id=str(item.id))
    assert incident_backlog._post_publish_flag_settled(_Session(item), incident, NOW) is None


def test_tiers_off_by_default_keep_every_hard_terminal(monkeypatch) -> None:
    from app.core.config import settings as _settings
    from app.workers.generation_retry_policy import has_model_declared_hard_finding

    monkeypatch.setattr(_settings, "CONTENT_FINDING_TIERS_ENABLED", False)
    review = {"findings": [_finding("HARD", "HOSPITAL_FACT")]}

    assert has_model_declared_hard_finding(review, {"body": PHILOSOPHY})
