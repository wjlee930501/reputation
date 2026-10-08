"""운영 채널의 숫자와 알림은 읽는 사람에게 달라진 것을 따른다 — 저장 방식이 아니라.

2026-10-08 `#mkt-reputation`에서 확인된 네 가지 증상의 회귀 방지다.
1. 일일 요약의 '확인 필요 N건'이 인시던트 행 수였다(사후검수 지적 51건 + 개발 몫 포함).
2. 같은 달의 새 보고서 버전(TEMPLATE_REFRESH 포함)마다 '전달 준비 완료'가 다시 나갔다.
3. 자동 복구가 없는 공백에도 "시스템이 자동 재측정·마감을 진행합니다"가 매일 반복됐다.
4. 막힌 글 하나가 더해지면 이미 알린 글 전체가 다시 나갔다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_geo_autonomy_hardening import NOW, AsyncDB, heartbeat, seed
from test_geo_autonomy_hardening import db as db  # noqa: F401 - 같은 SQLite 픽스처를 쓴다

from app.api.admin.operations_center_incident_queries import count_operator_incidents
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.models.report import MonthlyReport
from app.models.sov import SovRecord
from app.services import monthly_report_gap_notifications as gaps
from app.services.content_publish_notifications import enqueue_generation_blocked_digest_sync
from app.services.fleet_heartbeat import FleetFacts, collect_fleet_facts
from app.services.incident_cause_group import cause_group_key
from app.services.notification_labels import ERROR_LABEL, REPORT_LABEL
from app.services.operator_action import is_operator_todo
from app.workers.generation_incident_control import PUBLISH_MORNING_BATCH


@pytest.fixture(autouse=True)
def _tables(db):  # noqa: F811
    for model in (Incident, MonthlyReport, SovRecord):
        model.__table__.create(db.get_bind(), checkfirst=True)


def _incident(
    db, hospital, kind, code, *, state="OPEN", sla=None, source_id=None
) -> Incident:
    row = Incident(
        hospital_id=hospital.id,
        dedupe_key=str(uuid.uuid4()),
        incident_type=kind,
        state=state,
        severity="HIGH",
        customer_impact="영향",
        source_type="CONTENT_GENERATION",
        source_id=source_id or str(uuid.uuid4()),
        safe_error_code=code,
        next_action="조치",
        admin_path="/hospitals/x/contents",
        sla_due_at=sla,
    )
    db.add(row)
    db.commit()
    return row


# ── A. 하나의 '할 일' 술어 ─────────────────────────────────────────────


def test_todo_predicate_excludes_developer_quiet_and_automatic_recovery() -> None:
    past, future = NOW - timedelta(hours=1), NOW + timedelta(hours=1)
    assert is_operator_todo("CONTENT_GENERATION_FAILED", "OPEN", None, NOW)
    assert is_operator_todo("CONTENT_GENERATION_FAILED", "RETRYING", past, NOW)
    assert not is_operator_todo("CONTENT_GENERATION_FAILED", "RETRYING", future, NOW)
    assert not is_operator_todo("BROKER_UNAVAILABLE", "OPEN", None, NOW)  # 개발 담당 몫
    assert not is_operator_todo("POST_PUBLISH_REVIEW_FLAGGED", "OPEN", None, NOW)  # 조용한 종류


def test_heartbeat_counts_cause_groups_of_operator_todo_only(db) -> None:
    hospital, _ = seed(db)
    # 같은 원인 세 건은 한 묶음이다 — 운영센터 카드도 하나다.
    for _ in range(3):
        _incident(db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED")
    _incident(db, hospital, "CONTENT_GENERATION_FAILED", "MISSING_REFERENCES")
    # 사후검수 지적: 사람 몫 상태(OPEN)여도 어느 건수에도 들어가지 않는다.
    for _ in range(5):
        _incident(db, hospital, "POST_PUBLISH_REVIEW_FLAGGED", "POST_PUBLISH_REVIEW_FLAGGED")
    # 개발 몫은 따로 센다.
    _incident(db, hospital, "BROKER_UNAVAILABLE", "BROKER_UNAVAILABLE")
    _incident(db, hospital, "NOTIFICATION_DELIVERY_FAILED", "SLACK_DOWN")
    # 기한이 남은 자동 재시도는 사람의 일이 아니다.
    _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "IMAGE_GENERATION_FAILED",
        state="RETRYING", sla=NOW + timedelta(hours=2),
    )

    facts = collect_fleet_facts(db, now=NOW)

    assert facts.operator_work == 2
    assert facts.developer_work == 2
    assert facts.recovering == 1
    assert sum(group.count for group in facts.action_groups) == 4  # 묶음 안의 인시던트 수


async def test_heartbeat_count_equals_operations_center_group_count(db) -> None:
    hospital, _ = seed(db)
    for _ in range(3):
        _incident(db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED")
    _incident(db, hospital, "CONTENT_GENERATION_FAILED", "MISSING_REFERENCES")
    for _ in range(4):
        _incident(db, hospital, "POST_PUBLISH_REVIEW_FLAGGED", "POST_PUBLISH_REVIEW_FLAGGED")
    _incident(db, hospital, "BROKER_UNAVAILABLE", "BROKER_UNAVAILABLE")

    facts = collect_fleet_facts(db, now=NOW)
    counts = await count_operator_incidents(AsyncDB(db), [hospital.id], now=NOW)

    assert facts.operator_work == counts[hospital.id] == 2


def test_cause_group_key_is_shared_by_both_screens() -> None:
    # 운영센터가 쓰는 묶음 키가 같은 함수여야 두 화면의 숫자가 갈리지 않는다.
    from app.api.admin import operations_center_incident_queries as queries

    assert queries._cause_group_key is cause_group_key


def test_zero_operator_work_reads_as_no_issue_even_with_developer_work() -> None:
    text = heartbeat(facts=FleetFacts(2, 0, 0, 0, 2, 2, developer_work=3)).message.fallback_text

    assert "점검 이상 없음" in text.splitlines()[0]
    assert "담당자 확인 필요" not in text.splitlines()[0]
    assert "확인 필요 이슈 0건" in text
    assert "개발 확인 3건" in text


def test_unresolved_failed_runs_move_to_the_developer_line_only() -> None:
    text = heartbeat(facts=FleetFacts(2, 0, 0, 4, 2, 2, 4)).message.fallback_text

    assert "미완료 작업 있음" not in text.splitlines()[0]
    assert "개발 확인 4건" in text


def test_operator_work_still_turns_the_title_into_a_person_task() -> None:
    text = heartbeat(facts=FleetFacts(2, 0, 2, 0, 2, 2, developer_work=1)).message.fallback_text

    assert "담당자 확인 필요" in text.splitlines()[0]
    assert "확인 필요 이슈 2건" in text


# ── D. 월간 공백: 약속은 사실일 때만 ───────────────────────────────────────

_AUTO_PROMISE = "시스템이 자동 재측정·마감을 진행합니다"


def test_auto_gaps_are_a_report_and_manual_gaps_are_one_error_each() -> None:
    auto = gaps.MonthlyReportGap("코호트의원", "COVERAGE_INCOMPLETE", recoverable=True)
    manual_a = gaps.MonthlyReportGap("가의원", "COVERAGE_INCOMPLETE")
    manual_b = gaps.MonthlyReportGap("나의원", "COVERAGE_INCOMPLETE")

    intents = gaps.monthly_report_gap_intents(
        period_key="2026-09", gaps=[auto, manual_a, manual_b], day=3
    )
    by_type = {}
    for intent in intents:
        by_type.setdefault(intent.notification_type, []).append(intent)

    [auto_intent] = by_type["MONTHLY_REPORT_GAP_AUTO"]
    assert auto_intent.message.fallback_text.startswith(REPORT_LABEL)
    assert _AUTO_PROMISE in auto_intent.message.fallback_text
    assert "코호트의원" in auto_intent.message.payload_json()
    manual_intents = by_type["MONTHLY_REPORT_GAP_SUMMARY"]
    assert len(manual_intents) == 2  # 병원마다 한 건
    for intent in manual_intents:
        assert intent.message.fallback_text.startswith(ERROR_LABEL)
        assert _AUTO_PROMISE not in intent.message.fallback_text  # 거짓 약속 금지


def test_auto_summary_is_not_repeated_daily_but_a_changed_set_is_sent() -> None:
    first = [gaps.MonthlyReportGap("A의원", "MISSING", recoverable=True)]
    day_two = gaps.monthly_report_gap_intents(period_key="2026-09", gaps=first, day=2)
    day_three = gaps.monthly_report_gap_intents(period_key="2026-09", gaps=first, day=3)
    grown = gaps.monthly_report_gap_intents(
        period_key="2026-09",
        gaps=[*first, gaps.MonthlyReportGap("B의원", "COVERAGE_INCOMPLETE", recoverable=True)],
        day=3,
    )

    assert day_two[0].dedupe_key == day_three[0].dedupe_key
    assert grown[0].dedupe_key != day_two[0].dedupe_key


def test_manual_error_is_once_per_hospital_and_period() -> None:
    gap = gaps.MonthlyReportGap("가의원", "COVERAGE_INCOMPLETE")
    keys = {
        gaps.monthly_report_gap_intents(period_key="2026-09", gaps=[gap], day=day)[0].dedupe_key
        for day in (1, 2, 5)
    }
    other_period = gaps.monthly_report_gap_intents(period_key="2026-10", gaps=[gap], day=1)

    assert len(keys) == 1
    assert other_period[0].dedupe_key not in keys


def test_last_recovery_day_closes_still_open_auto_gaps_with_one_error() -> None:
    open_auto = [
        gaps.MonthlyReportGap("A의원", "MISSING", recoverable=True),
        gaps.MonthlyReportGap("B의원", "COVERAGE_INCOMPLETE", recoverable=True),
    ]

    intents = gaps.monthly_report_gap_intents(period_key="2026-09", gaps=open_auto, day=7)

    assert [intent.notification_type for intent in intents] == ["MONTHLY_REPORT_GAP_SUMMARY"]
    [closing] = intents
    assert closing.message.fallback_text.startswith(ERROR_LABEL)
    assert "자동 복구 종료" in closing.message.fallback_text
    assert _AUTO_PROMISE not in closing.message.fallback_text
    assert closing.dedupe_key.endswith(":closed")


# ── E. 막힌 글: 새로 막힌 글만 ─────────────────────────────────────────────


def _blocked(hospital, name: str, *, code="GENERATION_REJECTED", episode=1, **extra):
    return {
        "hospital_id": hospital.id,
        "hospital_name": "알림의원",
        "content_id": uuid.uuid4(),
        "scheduled_date": "2026-10-08",
        "title": name,
        "code": code,
        "cause": "자동 검수 게이트가 통과되지 않았습니다.",
        "episode_seq": episode,
        **extra,
    }


def test_blocked_digest_notifies_only_newly_blocked_items(db) -> None:
    hospital, _ = seed(db)
    first, second = _blocked(hospital, "첫째 글"), _blocked(hospital, "둘째 글")

    day1 = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 7), PUBLISH_MORNING_BATCH, [first, second])
    db.commit()
    # 이튿날: 같은 글은 시도 지문·예정일이 달라져도 다시 나가지 않는다.
    again = {**first, "attempt_fingerprint": "new inputs", "scheduled_date": "2026-10-09"}
    day2 = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 8), PUBLISH_MORNING_BATCH, [again, second])
    db.commit()
    # 새 글 하나가 더해지면 그 글만 나간다.
    third = _blocked(hospital, "셋째 글")
    day3 = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 9), PUBLISH_MORNING_BATCH, [first, second, third])
    db.commit()

    assert day1 is not None and day2 is None and day3 is not None
    payload = day3.payload["blocks"].__repr__() + day3.fallback_text
    assert "글 1건" in payload
    assert db.scalar(select(func.count()).select_from(NotificationOutbox)) == 2


def test_blocked_digest_renotifies_when_the_incident_epoch_advances(db) -> None:
    hospital, _ = seed(db)
    item = _blocked(hospital, "다시 막힌 글", episode=1)

    first = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 7), PUBLISH_MORNING_BATCH, [item])
    db.commit()
    same_epoch = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 8), PUBLISH_MORNING_BATCH, [item])
    db.commit()
    new_epoch = enqueue_generation_blocked_digest_sync(
        db, date(2026, 10, 9), PUBLISH_MORNING_BATCH, [{**item, "episode_seq": 2}]
    )
    db.commit()

    assert first is not None and same_epoch is None and new_epoch is not None
    assert db.scalar(
        select(func.count()).select_from(OperationRun).where(
            OperationRun.operation_type == "GENERATION_BLOCKED_NOTICE"
        )
    ) == 2


def test_blocked_digest_reads_the_epoch_from_the_incident_when_not_given(db) -> None:
    hospital, _ = seed(db)
    item = _blocked(hospital, "사고에서 읽는 글")
    del item["episode_seq"]
    incident = _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED",
        source_id=str(item["content_id"]),
    )
    incident.episode_seq = 3
    db.commit()

    enqueue_generation_blocked_digest_sync(db, date(2026, 10, 7), PUBLISH_MORNING_BATCH, [item])
    db.commit()

    key = db.scalar(
        select(OperationRun.idempotency_key).where(
            OperationRun.operation_type == "GENERATION_BLOCKED_NOTICE"
        )
    )
    assert key == f"{item['content_id']}:GENERATION_REJECTED:3"


def test_blocked_digest_ledger_key_includes_the_block_code(db) -> None:
    hospital, _ = seed(db)
    item = _blocked(hospital, "코드가 바뀌는 글", code="GENERATION_REJECTED")
    del item["episode_seq"]  # 운영 경로: 호출자는 epoch를 넘기지 않는다
    own = _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED",
        source_id=str(item["content_id"]),
    )
    first = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 7), PUBLISH_MORNING_BATCH, [item])
    db.commit()
    # 같은 글이 다른 코드로 막힌다(새 사고, 같은 epoch) — 다른 문제이므로 다시 알린다.
    _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "MISSING_REFERENCES",
        source_id=str(item["content_id"]),
    )
    changed = enqueue_generation_blocked_digest_sync(
        db, date(2026, 10, 8), PUBLISH_MORNING_BATCH, [{**item, "code": "MISSING_REFERENCES"}]
    )
    db.commit()
    again = enqueue_generation_blocked_digest_sync(
        db, date(2026, 10, 9), PUBLISH_MORNING_BATCH, [{**item, "code": "MISSING_REFERENCES"}]
    )
    db.commit()

    assert own.episode_seq == 1
    assert first is not None and changed is not None and again is None


def test_hospital_incident_with_another_code_does_not_renotify_blocked_items(db) -> None:
    hospital, _ = seed(db)
    item = _blocked(hospital, "병원 사고와 무관한 글", code="GENERATION_REJECTED")
    del item["episode_seq"]
    _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED",
        source_id=str(item["content_id"]),
    )
    first = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 7), PUBLISH_MORNING_BATCH, [item])
    db.commit()
    # 같은 병원의 다른 원인 사고가 epoch를 올린다.
    other = _incident(
        db, hospital, "CONTENT_GENERATION_FAILED", "MISSING_APPROVED_ESSENCE",
        source_id=str(hospital.id),
    )
    other.episode_seq = 5
    db.commit()
    second = enqueue_generation_blocked_digest_sync(db, date(2026, 10, 8), PUBLISH_MORNING_BATCH, [item])
    db.commit()

    assert first is not None and second is None


# ── 직렬화된 행 필드와 콘텐츠 탭 링크도 같은 술어를 따른다 ────────────────────────


async def test_queue_rows_carry_the_todo_predicate_not_the_state_only_one() -> None:

    from test_operations_center_incident_pagination import _FakeDB, _group_row
    from test_operations_center_incident_pagination import _incident as _queue_incident

    from app.api.admin.operations_center_incident_queries import load_incidents_queue
    from app.api.admin.operations_center_query_common import OperationsFilters
    from app.models.hospital import Hospital

    operator = _queue_incident(safe_error_code="SITE_BUILD_FAILED")
    developer = _queue_incident(safe_error_code="BROKER_UNAVAILABLE")
    developer.incident_type = "BROKER_UNAVAILABLE"
    # 1차: 묶음 행(운영 몫 먼저). 2차: 사람 몫 페이지, 3차: 맥락(개발 몫) 행.
    db = _FakeDB(
        [_group_row(operator), _group_row(developer)],
        [
            [(operator, Hospital(id=operator.hospital_id, name="A", slug="a"), None, None, None)],
            [(developer, Hospital(id=developer.hospital_id, name="B", slug="b"), None, None, None)],
        ],
    )

    total, rows = await load_incidents_queue(
        db, OperationsFilters(), page=1, page_size=25, overview=False,
        now=datetime(2026, 8, 25, tzinfo=UTC),
    )

    by_cause = {row.cause_code: row for row in rows}
    assert total == 1  # 운영 몫 묶음만 센다
    assert by_cause["SITE_BUILD_FAILED"].requires_operator_action is True
    assert by_cause["BROKER_UNAVAILABLE"].requires_operator_action is False


async def test_content_tab_links_skip_developer_incidents_but_keep_quiet_ones(db) -> None:

    from app.api.admin.content import _blocked_links_for

    hospital, _ = seed(db)
    developer_item, operator_item, quiet_item = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _incident(db, hospital, "BROKER_UNAVAILABLE", "BROKER_UNAVAILABLE", source_id=str(developer_item))
    _incident(db, hospital, "CONTENT_GENERATION_FAILED", "GENERATION_REJECTED", source_id=str(operator_item))
    _incident(
        db, hospital, "POST_PUBLISH_REVIEW_FLAGGED", "POST_PUBLISH_REVIEW_FLAGGED",
        source_id=str(quiet_item),
    )

    links = await _blocked_links_for(
        AsyncDB(db), hospital.id, [developer_item, operator_item, quiet_item]
    )

    assert developer_item not in links
    assert links[operator_item]["kind"] == "incident"
    assert links[quiet_item]["kind"] == "incident"  # 콘텐츠 탭에서만 보이는 계약
