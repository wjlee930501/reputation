"""실제 PostgreSQL에서 레거시 예산 1회 교체 CLI가 운영 23행 모양의 슬롯을 다시 스윕 대상으로 만든다.

운영(2026-10-13): 10/9 이전 빈 슬롯의 시도 기록은 `LEGACY_SPEND_UNKNOWN`(또는 게이트가 덮은
`CONTENT_NOT_GENERATED` + 열린 LEGACY 사고), `OPERATOR_REQUIRED`, 계수 0이었다. 교체 뒤에는 감사된
REPLACED 예산·감사 로그가 남고 억제가 풀려 다음 스윕의 로더가 집는다. 두 번째 실행은 0건이고,
술어 밖의 행(공개 이력·본문·10/9 이후 생성)은 그대로다.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import Incident, IncidentSeverity
from app.utils import legacy_budget_reset as cli
from app.workers import tasks
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    fresh_generation_attempt,
    read_generation_budget,
)

_LEGACY_CREATED = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)


@pytest.fixture
def rollback_factory(pg_engine) -> Iterator[sessionmaker[Session]]:
    connection = pg_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


def _record(reason: str, context: str) -> dict:
    return {
        "reason": reason,
        "context": context,
        "observed_at": "2026-10-11T22:45:01+00:00",
        "retry_class": "OPERATOR_REQUIRED",
        "attempt_count": 0,
        "attempt_period": "2026-10-12",
        "exhausted_days": 0,
        "first_observed_at": "2026-10-11T22:45:01+00:00",
        "guard_deferral_count": 0,
        "provider_attempt_count": 0,
    }


def _seed(factory) -> dict[str, uuid.UUID]:
    hospital_id = uuid.uuid4()
    ids = {name: uuid.uuid4() for name in ("legacy", "gate", "published", "written", "new")}
    with factory() as db:
        hospital = Hospital(
            id=hospital_id,
            name="레거시 예산 의원",
            slug=f"legacy-budget-{uuid.uuid4().hex[:10]}",
            status=HospitalStatus.ACTIVE,
            region=["서울"],
            specialties=["내과"],
            keywords=["건강검진"],
            competitors=[],
            treatments=[],
        )
        schedule = ContentSchedule(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            plan="PLAN_12",
            publish_days=[0],
            active_from=date(2026, 10, 1),
        )
        db.add_all([hospital, schedule])
        db.flush()

        def item(name: str, **overrides) -> ContentItem:
            row = ContentItem(
                id=ids[name],
                hospital_id=hospital_id,
                schedule_id=schedule.id,
                content_type=ContentType.HEALTH,
                sequence_no=list(ids).index(name) + 1,
                total_count=12,
                scheduled_date=date(2026, 10, 14),
                status=ContentStatus.DRAFT,
                references_list=[],
                created_at=_LEGACY_CREATED,
            )
            for key, value in overrides.items():
                setattr(row, key, value)
            return row

        rows = [
            item("legacy"),
            item("gate"),
            item("published", first_published_at=datetime(2026, 10, 1, tzinfo=UTC)),
            item("written", title="제목", body="이미 쓴 본문"),
            item("new", created_at=datetime(2026, 10, 10, tzinfo=UTC)),
        ]
        db.add_all(rows)
        db.flush()
        context = tasks._generation_attempt_context(rows[0], None)
        for row in rows:
            reason = "CONTENT_NOT_GENERATED" if row.id == ids["gate"] else "LEGACY_SPEND_UNKNOWN"
            row.essence_check_summary = {GENERATION_ATTEMPT_KEY: _record(reason, context)}
        for name in ("gate", "published"):
            db.add(
                Incident(
                    hospital_id=hospital_id,
                    dedupe_key=f"qa:{uuid.uuid4()}",
                    incident_type="CONTENT_GENERATION_FAILED",
                    state="OPEN",
                    severity=IncidentSeverity.HIGH,
                    customer_impact="콘텐츠 생성이 멈췄습니다.",
                    source_type="content_item",
                    source_id=str(ids[name]),
                    safe_error_code="LEGACY_SPEND_UNKNOWN",
                    safe_error_message="이전 생성 비용 기록을 확인할 수 없습니다.",
                    next_action="레거시 예산 교체 후 다시 시도",
                    admin_path=f"/hospitals/{hospital_id}/content",
                )
            )
        db.commit()
    return ids


def _candidate_ids(factory, *, apply: bool = False, reason: str | None = None) -> list[str]:
    out = io.StringIO()
    with factory() as db:
        cli.run(db, apply=apply, reason=reason, out=out)
    return [
        json.loads(line.split(" ", 1)[1])["content_id"]
        for line in out.getvalue().splitlines()
        if line.startswith("CANDIDATE ")
    ]


def _eligible(factory, item_id) -> bool:
    with factory() as db:
        item = db.get(ContentItem, item_id)
        return tasks._generation_retry_is_eligible(db)(item)


def test_cli_replaces_the_stuck_legacy_slots_once_and_the_sweep_takes_them(
    rollback_factory,
) -> None:
    ids = _seed(rollback_factory)
    ours = {str(value) for value in ids.values()}
    stuck = {str(ids["legacy"]), str(ids["gate"])}

    dry = [cid for cid in _candidate_ids(rollback_factory) if cid in ours]
    assert set(dry) == stuck
    for name in ("legacy", "gate"):
        assert _eligible(rollback_factory, ids[name]) is False

    applied = [
        cid for cid in _candidate_ids(rollback_factory, apply=True, reason="D22") if cid in ours
    ]
    assert set(applied) == stuck

    with rollback_factory() as db:
        for name in ("legacy", "gate"):
            item = db.get(ContentItem, ids[name])
            attempt = item.essence_check_summary[GENERATION_ATTEMPT_KEY]
            assert "reason" not in attempt and "retry_class" not in attempt
            assert read_generation_budget(attempt).legacy_state == "REPLACED"
            record = attempt["budget"]["reset_record"]
            assert record["actor"] == cli.ACTOR
            assert record["reason"] == "D22"
            assert record["idempotency_key"] == f"legacy-budget-reset-20261013:{ids[name]}"
            assert record["previous_snapshot"]["retry_class"] == "OPERATOR_REQUIRED"
        audits = db.scalars(
            select(AdminAuditLog).where(AdminAuditLog.target_id.in_(sorted(stuck)))
        ).all()
        assert sorted(row.action for row in audits) == ["LEGACY_BUDGET_REPLACED"] * 2
        assert {row.actor for row in audits} == {cli.ACTOR}
        # 술어 밖의 행은 그대로다.
        for name in ("published", "written", "new"):
            attempt = db.get(ContentItem, ids[name]).essence_check_summary[GENERATION_ATTEMPT_KEY]
            assert attempt["reason"] == "LEGACY_SPEND_UNKNOWN"
            assert "budget" not in attempt

    for name in ("legacy", "gate"):
        assert _eligible(rollback_factory, ids[name]) is True

    assert [cid for cid in _candidate_ids(rollback_factory) if cid in ours] == []
    assert [
        cid for cid in _candidate_ids(rollback_factory, apply=True, reason="again") if cid in ours
    ] == []


def test_cli_never_touches_a_fresh_budget_slot(rollback_factory) -> None:
    ids = _seed(rollback_factory)
    with rollback_factory() as db:
        item = db.get(ContentItem, ids["legacy"])
        fresh = fresh_generation_attempt()
        item.essence_check_summary = {
            GENERATION_ATTEMPT_KEY: {**fresh, "reason": "LEGACY_SPEND_UNKNOWN"}
        }
        db.commit()

    assert str(ids["legacy"]) not in _candidate_ids(rollback_factory)
