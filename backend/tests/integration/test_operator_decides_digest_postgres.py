"""아침 요약의 진료비·병원 선택 글 참고자료 보류 줄 — 실제 Postgres로 검증한다.

mock으로는 확인할 수 없는 것만 본다: 실제 due 조회(당일·catch-up)가 잡은 행 중 **오늘 예정인**
사람의 결정 보류만 요약에 한 줄로 오르고, JSONB 시도 기록 왕복 뒤 같은 날 07:45가 다시 돌거나
08:00 발행기가 돌아도 `notification_outbox`의 중복 키가 두 번째 행을 막는가. 인시던트는 별도
async 세션을 쓰므로 호출만 잡는다. 참고자료 GET은 가짜 fetcher(모두 404)로 막는다.
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import arrow
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import NotificationOutbox
from app.services import content_publish_notifications
from app.services.reference_verification import override_reference_fetcher
from app.workers import tasks
from app.workers.generation_incident_control import (
    PREPUBLISH_MORNING_BATCH,
    PUBLISH_MORNING_BATCH,
    REFERENCES_OPERATOR_DECIDES_CAUSE,
)
from app.workers.generation_retry_policy import OPERATOR_DECIDES_KEY, GenerationRetryClass
from tests.reference_fetch_doubles import PageFetcher

# 다른 테스트의 행과 due 창이 겹치지 않는 먼 날짜.
TODAY = date(2031, 3, 12)
SEVEN_FORTY_FIVE = arrow.get(2031, 3, 12, 7, 45, tzinfo="Asia/Seoul")
EIGHT = arrow.get(2031, 3, 12, 8, 0, tzinfo="Asia/Seoul")
COST_TITLE = "치질 수술 비용 — 보험 적용과 본인부담"
MEDICAL_TITLE = "치질 수술 후 회복 기간과 통증 관리"
DEAD_URL = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn=2480"
)
LINE_TITLE = "참고 자료 운영자 판단"


class _SessionProxy:
    """`with SyncSessionLocal() as db:`를 테스트 세션에 그대로 붙인다."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


@pytest.fixture
def pg_session(pg_conn):
    session = Session(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def gate_db(pg_session, monkeypatch):
    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "")

    def approved_philosophy(db, hospital_id):
        # `_hospital`이 심은 실제 APPROVED 행 — 게이트가 그 id를 content_items의 FK로 쓴다.
        # 실제 readiness 판정(자료 스냅샷 hash 등)은 이 테스트의 범위가 아니다.
        return db.execute(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.hospital_id == hospital_id,
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
            )
        ).scalar_one()

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", approved_philosophy)
    return pg_session


@pytest.fixture
def incidents(monkeypatch):
    calls: list[dict] = []

    async def capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(tasks, "open_generation_incident", capture)
    return calls


def _hospital(db) -> tuple[Hospital, ContentSchedule]:
    hospital = Hospital(
        name=f"보류요약의원 {uuid.uuid4().hex[:6]}",
        slug=f"operator-digest-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
        site_live=True,
        profile_complete=True,
        site_built=True,
        schedule_set=True,
    )
    db.add(hospital)
    db.flush()
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        positioning_statement="근거 중심으로 충분히 설명합니다.",
        patient_promise="확인된 정보만 환자에게 안내합니다.",
        avoid_messages=[],
        approved_at=datetime(2031, 3, 1, tzinfo=timezone.utc),
    )
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1, 3], active_from=date(2031, 3, 1)
    )
    db.add_all([philosophy, schedule])
    db.flush()
    return hospital, schedule


def _written(db, hospital, schedule, *, title, scheduled_date, sequence_no) -> ContentItem:
    """발행 전 검증에서 참고자료가 모두 빠지는 작성된 글(죽은 주소 하나)."""

    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=sequence_no,
        total_count=12,
        title=title,
        body="## 안내\n진료 기준과 내원 시점을 안내합니다.",
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
        references_list=[{"title": "추측 주소", "url": DEAD_URL}],
    )
    db.add(item)
    db.flush()
    return item


def _unwritten(db, hospital, schedule, *, scheduled_date, sequence_no) -> ContentItem:
    """작가 회차 뒤 참고자료가 없어 생성이 사람의 결정으로 남긴, 쓰이지 않은 슬롯."""

    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=sequence_no,
        total_count=12,
        scheduled_date=scheduled_date,
        status=ContentStatus.DRAFT,
        essence_check_summary={
            "generation_attempt": {
                "context": "od-pg-context",
                "reason": "MISSING_REFERENCES",
                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
                "next_retry_at": None,
                OPERATOR_DECIDES_KEY: True,
            }
        },
    )
    db.add(item)
    db.flush()
    return item


def _run(db) -> int:
    with override_reference_fetcher(PageFetcher()):
        return tasks._page_morning_stored_publication_gates(db, now_kst=SEVEN_FORTY_FIVE)


def _run_eight(monkeypatch) -> None:
    """08:00 발행기 태스크 전체 — 후보 조회·`_auto_publish_one`·요약 조립·outbox가 실제 경로다."""

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: EIGHT)
    with override_reference_fetcher(PageFetcher()):
        tasks.morning_content_auto_publish.run()


def _digests_for(db, hospital) -> list[NotificationOutbox]:
    rows = db.execute(
        select(NotificationOutbox).where(
            NotificationOutbox.notification_type == "GENERATION_BLOCKED_DIGEST"
        )
    ).scalars().all()
    return [
        row for row in rows if hospital.name in json.dumps(row.payload, ensure_ascii=False)
    ]


def _section_text(row) -> str:
    return "\n".join(
        block["text"]["text"] for block in row.payload["blocks"] if block.get("type") == "section"
    )


def test_seven_forty_five_names_only_the_same_day_operator_hold_once(gate_db, incidents):
    db = gate_db
    hospital, schedule = _hospital(db)
    held_today = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1
    )
    ordinary_today = _written(
        db, hospital, schedule, title=MEDICAL_TITLE, scheduled_date=TODAY, sequence_no=2
    )
    held_catchup = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY - timedelta(days=2),
        sequence_no=3,
    )
    db.commit()
    ids = {held_today.id, ordinary_today.id, held_catchup.id}

    assert _run(db) == 3

    # 세 글 모두 보류되고 인시던트는 종전처럼 MISSING_REFERENCES다.
    assert {call["item_id"] for call in incidents} == ids
    assert {call["code"] for call in incidents} == {"MISSING_REFERENCES"}
    for item in (held_today, ordinary_today, held_catchup):
        db.refresh(item)
        assert item.references_list == []
    [row] = _digests_for(db, hospital)
    text = _section_text(row)
    assert "발행 보류 1편" in text  # 오늘 예정인 사람의 결정 보류 한 건만
    assert text.count(LINE_TITLE) == 1
    assert f"{LINE_TITLE} 1편" in text
    assert "본문·근거 확인 필요" not in text  # 평범한 참고자료 보류는 주간 요약 몫이다
    attempts = {
        item.id: dict(item.essence_check_summary["generation_attempt"])
        for item in (held_today, ordinary_today, held_catchup)
    }
    assert attempts[held_today.id][OPERATOR_DECIDES_KEY] is True
    assert attempts[held_catchup.id][OPERATOR_DECIDES_KEY] is True
    assert OPERATOR_DECIDES_KEY not in attempts[ordinary_today.id]

    # 같은 날 07:45가 다시 돌아도 outbox 행은 하나다(같은 식별자 → 같은 중복 키).
    incidents.clear()
    assert _run(db) == 3
    assert len(incidents) == 3
    [again] = _digests_for(db, hospital)
    assert again.id == row.id
    assert again.dedupe_key == row.dedupe_key
    for item in (held_today, ordinary_today, held_catchup):
        db.refresh(item)
        assert item.essence_check_summary["generation_attempt"] == attempts[item.id]


def test_seven_forty_five_names_a_same_day_unwritten_operator_slot_once(gate_db, incidents):
    db = gate_db
    hospital, schedule = _hospital(db)
    slot = _unwritten(db, hospital, schedule, scheduled_date=TODAY, sequence_no=1)
    past_slot = _unwritten(
        db, hospital, schedule, scheduled_date=TODAY - timedelta(days=1), sequence_no=2
    )
    db.commit()
    stored = dict(slot.essence_check_summary["generation_attempt"])

    assert _run(db) == 2
    assert _run(db) == 2

    assert [(call["code"], call["message"]) for call in incidents] == [
        ("MISSING_REFERENCES", REFERENCES_OPERATOR_DECIDES_CAUSE)
    ] * 4
    db.refresh(slot)
    db.refresh(past_slot)
    assert slot.essence_check_summary["generation_attempt"] == stored
    [row] = _digests_for(db, hospital)
    text = _section_text(row)
    assert "발행 보류 1편" in text
    assert text.count(LINE_TITLE) == 1


def test_seven_forty_five_then_eight_oclock_leave_one_digest_with_one_operator_line(
    gate_db, incidents, monkeypatch
):
    """평소 아침: 07:45 {A,B} == 08:00 {A,B} → 같은 중복 키 → outbox 행 하나, 보류 줄 하나.

    A는 오늘 예정인 진료비 글의 사람의 결정 보류, B는 두 요약이 모두 싣는 원고 미생성 슬롯,
    C는 지난 예정일(catch-up)의 같은 보류라 어느 요약에도 오르지 않는다.
    """

    db = gate_db
    hospital, schedule = _hospital(db)
    held = _written(db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1)
    missing = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=2,
        total_count=12,
        scheduled_date=TODAY,
        status=ContentStatus.DRAFT,
    )
    db.add(missing)
    held_catchup = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY - timedelta(days=2),
        sequence_no=3,
    )
    db.commit()
    items = (held, missing, held_catchup)
    keys: list[tuple[str, str]] = []
    build = content_publish_notifications.build_generation_blocked_digest_intent

    def recording_build(cycle_date, batch, *args, **kwargs):
        intent = build(cycle_date, batch, *args, **kwargs)
        keys.append((batch, intent.dedupe_key))
        return intent

    monkeypatch.setattr(
        content_publish_notifications, "build_generation_blocked_digest_intent", recording_build
    )

    assert _run(db) == 3
    [row] = _digests_for(db, hospital)
    attempts = {}
    for item in items:
        db.refresh(item)
        attempts[item.id] = dict(item.essence_check_summary["generation_attempt"])

    incidents.clear()
    _run_eight(monkeypatch)

    # 08:00도 세 글을 보류로 판정했고 요약을 조립했다(A를 실었다) — 07:45와 같은 키라 합쳐졌다.
    assert {call["item_id"] for call in incidents} == {item.id for item in items}
    assert [batch for batch, _key in keys] == [PREPUBLISH_MORNING_BATCH, PUBLISH_MORNING_BATCH]
    assert keys[0][1] == keys[1][1] == row.dedupe_key
    [again] = _digests_for(db, hospital)
    assert again.id == row.id
    text = _section_text(again)
    assert "발행 보류 2편" in text
    assert text.count(LINE_TITLE) == 1
    assert f"{LINE_TITLE} 1편" in text
    # 08:00 게이트는 시도 기록(요약 식별자의 지문)을 바꾸지 않았다. 아무 글도 공개되지 않았다.
    for item in items:
        db.refresh(item)
        assert item.essence_check_summary["generation_attempt"] == attempts[item.id]
        assert item.status is ContentStatus.DRAFT
