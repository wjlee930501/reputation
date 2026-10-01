"""주제 교체 → 로더 → 글 단위 워커를 실제 Postgres에서 한 줄로 잇는다.

기존 PG 테스트는 교체 SQL과 로더의 claim까지만 보고 워커를 실행하지 않았다. 여기서는 실제 교체
SQL이 비운 슬롯을 실제 로더(`_load_nightly_generation_batch`)가 claim하고, 실제 글 단위 태스크
(`generate_claimed_content_item.run` → `_run_generation_item` → 조건부 write-back → 종료 해제)가
그 토큰으로 돈다. 가짜는 작가(`_generate_with_auto_review`)와 이미지 공급자(`generate_image`)뿐이다.
인시던트는 별도 async 세션이라 호출만 잡는다.

- 성공: 새 주제의 본문이 저장되고, claim이 풀리고(#184), 교체 기록이 새 결과로 바뀌며, 07:45
  게이트는 이 슬롯을 CONTENT_NOT_GENERATED로 보고하지 않는다.
- 공급자 시간 초과: 시도 기록이 PROVIDER_TIMEOUT(환경 복구, 다음 시도 시각)으로 남고 claim이
  풀리며, 로더는 그 시각 전에는 집지 않고 그 시각에 다시 claim한다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.workers import (
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
    topic_swap_fallback,
)
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import GenerationRetryClass
from tests.integration.test_topic_swap_fallback_postgres import SLOT, _seed_hospital, _seed_item

KST = ZoneInfo("Asia/Seoul")
SWAPPED_AT = datetime(2026, 9, 16, 7, 0, 2, tzinfo=KST)  # 예정일 당일 07:00 스윕
LOADED_AT = datetime(2026, 9, 16, 7, 0, 5, tzinfo=KST)
WORKED_AT = datetime(2026, 9, 16, 7, 1, tzinfo=KST)
GATE_AT = datetime(2026, 9, 16, 7, 45, tzinfo=KST)
IMAGE_URL = "https://storage.googleapis.com/reputation-images/content/" + "d" * 64 + "-new.png"


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


def _freeze(monkeypatch, moment: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)


@pytest.fixture
def reports(pg_session, monkeypatch):
    """인시던트 열기·종결 호출만 잡는다(자기 async 세션이라 테스트 트랜잭션 밖이다)."""

    calls: dict[str, list] = {"opened": [], "recovered": [], "digested": []}

    async def capture_open(**kwargs):
        calls["opened"].append((kwargs["item_id"], kwargs["code"]))

    async def capture_recover(item_id, *_args, **_kwargs):
        calls["recovered"].append(item_id)

    monkeypatch.setattr(tasks, "SyncSessionLocal", _SessionProxy(pg_session))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_open)
    monkeypatch.setattr(tasks, "recover_generation_incidents", capture_recover)
    monkeypatch.setattr(
        tasks,
        "enqueue_generation_blocked_digest_sync",
        lambda _db, _day, _batch, outcomes: calls["digested"].extend(
            (row["content_id"], row["code"]) for row in outcomes
        ),
    )
    return calls


def _seed_swappable_slot(pg_conn, pg_session) -> uuid.UUID:
    hospital_id = _seed_hospital(pg_conn)
    pg_session.add(
        HospitalContentPhilosophy(
            hospital_id=hospital_id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            positioning_statement="근거 중심으로 충분히 설명합니다.",
            patient_promise="확인된 정보만 환자에게 안내합니다.",
            avoid_messages=[],
            approved_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    pg_session.commit()
    return _seed_item(pg_conn, hospital_id)  # 3일 소진된 GENERATION_REJECTED, 예정일 당일


def _swap_and_load(pg_session, monkeypatch) -> ContentItem:
    report = topic_swap_fallback.swap_exhausted_topics(
        pg_session,
        window_start=SLOT - timedelta(days=7),
        window_end=SLOT + timedelta(days=2),
        now=SWAPPED_AT.astimezone(UTC),
    )
    assert report.swapped == 1
    return _load_at(pg_session, monkeypatch, LOADED_AT)


def _load_at(pg_session, monkeypatch, moment: datetime) -> ContentItem | None:
    _freeze(monkeypatch, moment)
    claimed, _truncated, _complete = tasks._load_nightly_generation_batch(
        pg_session,
        moment.date() - timedelta(days=7),
        moment.date() + timedelta(days=2),
        is_eligible=tasks._generation_retry_is_eligible(pg_session),
    )
    assert len(claimed) <= 1
    return claimed[0] if claimed else None


def _target_name(pg_conn, target_id) -> str:
    return pg_conn.execute(
        text("SELECT name FROM ai_query_targets WHERE id=:id"), {"id": target_id}
    ).scalar_one()


def _row(pg_session, item_id) -> ContentItem:
    pg_session.expire_all()
    return pg_session.execute(select(ContentItem).where(ContentItem.id == item_id)).scalar_one()


def test_a_swapped_slot_is_claimed_by_the_loader_and_written_by_the_real_worker(
    pg_conn, pg_session, monkeypatch, reports
):
    item_id = _seed_swappable_slot(pg_conn, pg_session)
    claimed = _swap_and_load(pg_session, monkeypatch)
    assert claimed is not None and claimed.id == item_id
    token = claimed.generation_claim_token
    swapped = _row(pg_session, item_id)
    new_topic = _target_name(pg_conn, swapped.query_target_id)
    assert new_topic != "대장내시경 수면 여부"  # 교체된 주제
    assert swapped.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == (
        topic_swap_fallback.TOPIC_SWAPPED_REASON
    )
    written: list[str] = []

    async def writer(*, hospital, item, existing_titles, philosophy, approved_brief):
        topic = _target_name(pg_conn, item.query_target_id)
        written.append(topic)
        return (
            {
                "title": f"{topic} — 진료 전 확인할 점",
                "body": f"## {topic}\n" + "진료 기준과 내원 시점을 안내합니다. " * 80,
                "meta_description": f"{topic}을 정리했습니다.",
                "references": [],
                "reference_checks": [],
                "faq_question": f"{topic}은 어떻게 준비하나요?",
                "faq_answer_summary": "현재 증상과 복용약을 정리해 의료진에게 알려 주세요.",
            },
            SimpleNamespace(status="ALIGNED", summary={"blocking": False, "findings": []}),
        )

    async def image_provider(*_args, **_kwargs):
        return IMAGE_URL, "prompt"

    monkeypatch.setattr(tasks, "_generate_with_auto_review", writer)
    monkeypatch.setattr(tasks, "generate_image", image_provider)
    _freeze(monkeypatch, WORKED_AT)

    tasks.generate_claimed_content_item.run(str(item_id), str(token))

    row = _row(pg_session, item_id)
    assert written == [new_topic]
    assert row.title == f"{new_topic} — 진료 전 확인할 점" and new_topic in row.body
    assert row.status is ContentStatus.DRAFT
    assert row.image_url == IMAGE_URL
    assert (row.generation_claim_token, row.generation_claimed_at) == (None, None)  # #184
    attempt = (row.essence_check_summary or {}).get(GENERATION_ATTEMPT_KEY) or {}
    assert attempt.get("reason") != topic_swap_fallback.TOPIC_SWAPPED_REASON  # 교체 기록은 새 결과로

    _freeze(monkeypatch, GATE_AT)
    tasks._page_morning_stored_publication_gates(pg_session, now_kst=arrow.get(GATE_AT))

    codes = [code for reported, code in reports["opened"] + reports["digested"] if reported == item_id]
    assert "CONTENT_NOT_GENERATED" not in codes


def test_a_provider_timeout_keeps_its_record_releases_the_claim_and_is_claimed_again_when_due(
    pg_conn, pg_session, monkeypatch, reports
):
    item_id = _seed_swappable_slot(pg_conn, pg_session)
    claimed = _swap_and_load(pg_session, monkeypatch)
    assert claimed is not None and claimed.id == item_id
    token = claimed.generation_claim_token

    async def timing_out_writer(**_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(tasks, "_generate_with_auto_review", timing_out_writer)
    _freeze(monkeypatch, WORKED_AT)

    tasks.generate_claimed_content_item.run(str(item_id), str(token))

    row = _row(pg_session, item_id)
    assert row.body is None
    assert (row.generation_claim_token, row.generation_claimed_at) == (None, None)
    attempt = row.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["reason"] == "PROVIDER_TIMEOUT"
    assert attempt["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert attempt["next_retry_at"] is not None
    assert (item_id, "PROVIDER_TIMEOUT") in reports["opened"]
    retry_at = datetime.fromisoformat(attempt["next_retry_at"])
    assert retry_at > WORKED_AT

    # 그 시각 전의 로더는 집지 않는다(같은 입력의 비용 재시도 억제). 그 시각에는 다시 claim한다.
    assert _load_at(pg_session, monkeypatch, retry_at - timedelta(seconds=1)) is None
    assert _row(pg_session, item_id).essence_check_summary[GENERATION_ATTEMPT_KEY] == attempt
    again = _load_at(pg_session, monkeypatch, retry_at)
    assert again is not None and again.id == item_id
    assert again.generation_claim_token not in (None, token)
