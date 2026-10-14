"""배포 B 백필 CLI(`essence_autonomy_backfill`, Phase 4 P4-16)를 실제 PostgreSQL에서 확인한다."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import NotificationOutbox
from app.services.essence_engine import synthesize_philosophy_with_mode
from app.services.profile_facts_source import existing_profile_facts_source
from app.utils import essence_autonomy_backfill as backfill


@pytest.fixture
def pg_session(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _seed(db: Session):
    waiting = Hospital(
        id=uuid.uuid4(),
        name="판 없는 병원",
        slug=f"backfill-waiting-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        director_name="김원장",
        director_philosophy="환자분이 이해하실 때까지 설명합니다.",
        treatments=[{"name": "치질 수술"}],
    )
    live = Hospital(
        id=uuid.uuid4(),
        name="운영 중 병원",
        slug=f"backfill-live-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        director_name="이원장",
        specialties=["정형외과"],
    )
    db.add_all([waiting, live])
    db.flush()
    draft = HospitalContentPhilosophy(
        hospital_id=waiting.id,
        version=1,
        status=PhilosophyStatus.DRAFT,
        created_by="SYSTEM_ESSENCE_AI_REVIEW",
        unsupported_gaps=[{"field": "automatic_ai_review", "reason": "근거 부족"}],
    )
    legacy = HospitalContentPhilosophy(
        hospital_id=live.id,
        version=7,
        status=PhilosophyStatus.APPROVED,
        is_base=True,
        approved_at=datetime.now(UTC) - timedelta(days=10),
        created_by="SYSTEM_ESSENCE_AI_REVIEW",
    )
    digest = NotificationOutbox(
        dedupe_key=f"MISSING_APPROVED_ESSENCE_DIGEST:{uuid.uuid4()}",
        notification_type="MISSING_APPROVED_ESSENCE_DIGEST",
        channel="SLACK",
        state="PENDING",
        next_attempt_at=datetime.now(UTC),
        payload={},
        fallback_text="온보딩 생성 요약",
    )
    db.add_all([draft, legacy, digest])
    # CLI는 보고 뒤 rollback한다 — 시드는 커밋해 둔다(바깥 테스트 트랜잭션이 되돌린다).
    db.commit()
    return waiting, live, draft, digest


def _lines(out: io.StringIO) -> list[tuple[str, dict]]:
    rows = []
    for line in out.getvalue().splitlines():
        kind, _, payload = line.partition(" ")
        rows.append((kind, json.loads(payload)))
    return rows


def _deterministic(hospital, sources, notes, operator_note=None):
    return synthesize_philosophy_with_mode(hospital, sources, notes, operator_note, use_llm=False)


def test_dry_run_reports_targets_and_writes_nothing(pg_session) -> None:
    waiting, live, draft, digest = _seed(pg_session)
    out = io.StringIO()

    assert backfill.run(pg_session, apply=False, out=out, ids=[waiting.id, live.id]) == 0

    rows = {payload.get("hospital_id"): (kind, payload) for kind, payload in _lines(out)}
    kind, waiting_row = rows[str(waiting.id)]
    assert kind == "TARGET"
    assert waiting_row["applied_version"] is None
    assert waiting_row["draft_count"] == 1
    assert waiting_row["profile_facts"] == "CREATE"
    assert waiting_row["trigger"] == "T1_NO_ESSENCE"
    summary = dict(_lines(out))["SUMMARY"]
    assert summary["apply"] is False and summary["retired_outbox_open"] >= 1
    assert existing_profile_facts_source(pg_session, waiting.id) is None
    assert pg_session.get(HospitalContentPhilosophy, draft.id).status == PhilosophyStatus.DRAFT
    assert pg_session.get(NotificationOutbox, digest.id).state == "PENDING"


def test_default_targets_are_hospitals_without_an_applied_essence(pg_session) -> None:
    waiting, live, _draft, _digest = _seed(pg_session)
    out = io.StringIO()

    backfill.run(pg_session, apply=False, out=out)

    rows = {payload.get("hospital_id"): kind for kind, payload in _lines(out)}
    assert rows[str(waiting.id)] == "TARGET"
    assert rows[str(live.id)] == "LIVE"
    live_row = next(
        payload for kind, payload in _lines(out) if payload.get("hospital_id") == str(live.id)
    )
    # 옛 승인본은 지금 다시 합성될 이유가 없다 — 판 번호가 그대로다.
    assert (live_row["applied_version"], live_row["applied_mode"], live_row["trigger"]) == (
        7,
        "LEGACY",
        None,
    )


def test_apply_applies_the_automatic_essence_and_leaves_the_legacy_base_alone(pg_session) -> None:
    waiting, live, draft, digest = _seed(pg_session)
    schedules: list[uuid.UUID] = []
    out = io.StringIO()

    backfill.run(
        pg_session,
        apply=True,
        out=out,
        ids=[waiting.id, live.id],
        synthesizer=_deterministic,
        schedule=lambda hospital_id: schedules.append(hospital_id) or "CREATED",
    )

    results = {payload["hospital_id"]: payload for kind, payload in _lines(out) if kind == "RESULT"}
    assert results[str(waiting.id)]["status"] == "APPLIED"
    assert results[str(waiting.id)]["initial_schedule"] == "CREATED"
    assert results[str(waiting.id)]["version"] == 2
    assert results[str(live.id)]["status"] == "UP_TO_DATE"
    assert schedules == [waiting.id]

    versions = pg_session.scalars(
        select(HospitalContentPhilosophy)
        .where(HospitalContentPhilosophy.hospital_id == waiting.id)
        .order_by(HospitalContentPhilosophy.version)
    ).all()
    assert [(row.id == draft.id, row.status) for row in versions] == [
        (True, PhilosophyStatus.ARCHIVED),
        (False, PhilosophyStatus.APPROVED),
    ]
    assert versions[1].synthesis_metadata["mode"] == "AUTOMATIC"
    live_versions = pg_session.scalars(
        select(HospitalContentPhilosophy.version).where(
            HospitalContentPhilosophy.hospital_id == live.id
        )
    ).all()
    assert live_versions == [7]
    # 운영 중 병원도 병원 정보(자동) 자료를 얻지만 판은 그대로다.
    assert existing_profile_facts_source(pg_session, live.id) is not None
    closed = pg_session.get(NotificationOutbox, digest.id)
    assert (closed.state, closed.safe_error_code) == ("FAILED", "RETIRED_NOTIFICATION_TYPE")
    assert dict(_lines(out))["DONE"]["applied"] == 1
