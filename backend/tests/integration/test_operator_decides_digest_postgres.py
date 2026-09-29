"""07:45 요약의 진료비·병원 선택 글 참고자료 보류 줄 — 실제 Postgres로 검증한다.

mock으로는 확인할 수 없는 것만 본다: 실제 due 조회(당일·catch-up)가 잡은 행 중 **오늘 예정인**
사람의 결정 보류만 요약에 한 줄로 오르고, JSONB 시도 기록 왕복 뒤 같은 날 다시 돌아도
`notification_outbox`의 중복 키가 두 번째 행을 막는가. 인시던트는 별도 async 세션을 쓰므로
호출만 잡는다. 참고자료 GET은 가짜 fetcher(모두 404)로 막는다.
"""

import json
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import arrow
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import NotificationOutbox
from app.services.reference_verification import override_reference_fetcher
from app.workers import tasks
from app.workers.generation_incident_control import REFERENCES_OPERATOR_DECIDES_CAUSE
from app.workers.generation_retry_policy import OPERATOR_DECIDES_KEY, GenerationRetryClass
from tests.reference_fetch_doubles import PageFetcher

# 다른 테스트의 행과 due 창이 겹치지 않는 먼 날짜.
TODAY = date(2031, 3, 12)
SEVEN_FORTY_FIVE = arrow.get(2031, 3, 12, 7, 45, tzinfo="Asia/Seoul")
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
    philosophy = SimpleNamespace(
        id=uuid.uuid4(), version=1, status=PhilosophyStatus.APPROVED, avoid_messages=[]
    )
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: philosophy)
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
    schedule = ContentSchedule(
        hospital_id=hospital.id, plan="PLAN_12", publish_days=[1, 3], active_from=date(2031, 3, 1)
    )
    db.add(schedule)
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
