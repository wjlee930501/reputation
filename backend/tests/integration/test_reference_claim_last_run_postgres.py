"""그날 마지막 발행기(23시)의 claim 행 — 실제 Postgres로 검증한다(#185 리뷰 S2).

진료비 글이 예정일 D에 본문을 갖고 있고, 22:10에 생성 워커가 잡아 23시에도 claim이 살아
있다. 23시 발행기 태스크 전체(후보 조회·`_auto_publish_one`·요약 조립·outbox)가 종전처럼 잠금
전 재검증(GET)을 하고, 그 결과를 행이 아닌 분리된 사본에 적용해 인시던트와 #181 운영자 판단
줄을 낸다. 행의 판·참고자료·검증 기록·상태는 DB에서 다시 읽어도 그대로다. 기관 장애로 미뤄진
글은 claim 없는 글과 같은 결과(기관 접속 불가 요약 줄, 보류 없음)다. 같은 행이 12시(마지막
발행기가 아님)에는 GET 없이 건너뛰고, 다음 날 발행기는 운영자 줄을 다시 싣지 않는다. 인시던트는
별도 async 세션이라 대개 호출만 잡는다 — 인시던트 경로가 claim 행을 쓰지 않는지 보는 테스트만
실제 `open_generation_incident`를 같은 테스트 트랜잭션 위에서 돌린다(#185 리뷰 A2). 참고자료 GET은
가짜 fetcher가 받는다(네트워크 없음).
"""

import uuid
from datetime import datetime, timezone

import arrow
import pytest
from sqlalchemy import select, text

from app.models.content import ContentStatus
from app.models.operations import Incident
from app.services import content_publish_notifications
from app.services.reference_publication import REFERENCE_SITE_UNREACHABLE_CODE
from app.services.reference_verification import (
    item_topic_fingerprint,
    override_reference_fetcher,
    reference_check_record,
)
from app.workers import (
    generation_incident_control,
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
)
from app.workers.generation_retry_policy import OPERATOR_DECIDES_KEY, GenerationRetryClass
from tests.integration import test_operator_decides_digest_postgres as digest
from tests.integration.test_operator_decides_digest_postgres import (
    COST_TITLE,
    DEAD_URL,
    LINE_TITLE,
    TODAY,
    _digests_for,
    _hospital,
    _section_text,
    _written,
)
from tests.integration.test_published_image_recertification_postgres import _AsyncSessionFacade
from tests.reference_fetch_doubles import PageFetcher

# #181 요약 테스트의 실제 행·outbox 픽스처를 그대로 쓴다(발행기 세션·승인 운영 기준·인시던트 캡처).
pg_session = digest.pg_session
gate_db = digest.gate_db
incidents = digest.incidents

# 수기 목록 치유 후보가 없는 의료 글 — 참고자료가 비면 종전처럼 MISSING_REFERENCES로 보류된다.
NO_CATALOG_TITLE = "귀지가 막혔을 때 — 집에서 해도 되는 관리"
# 치질(치핵) 수기 목록 문서 — 진료비 글에서는 통과 기록이 있어도 빼야 하는 문서다.
CATALOG_HEMORRHOID = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/"
    "gnrlzHealthInfoView.do?cntnts_sn=5818"
)


def _kst(day, hour, minute=0):
    return arrow.get(day.year, day.month, day.day, hour, minute, tzinfo="Asia/Seoul")


def _set_clock(monkeypatch, moment):
    """발행기·claim 판정·재시도 정책이 같은 '지금'을 보게 한다(리뷰어 S2 probe와 같은 방식)."""

    now = moment.datetime

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    # 인시던트 경로도 같은 '지금'으로 claim을 판정한다(실제 인시던트를 쓰는 테스트).
    for module in (
        tasks,
        generation_retry_policy,
        nightly_generation_batch,
        generation_incident_control,
    ):
        monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: moment)


def _publisher_run(db, monkeypatch, moment, pages=None) -> PageFetcher:
    _set_clock(monkeypatch, moment)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    fetcher = PageFetcher(pages)  # 등록되지 않은 주소는 404
    with override_reference_fetcher(fetcher):
        if (moment.hour, moment.minute) == (7, 45):
            tasks._page_morning_stored_publication_gates(db, now_kst=moment)
        else:
            tasks.morning_content_auto_publish.run()
    return fetcher


def _claimed_cost_post(db, *, claimed_at):
    hospital, schedule = _hospital(db)
    item = _written(db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1)
    checked = datetime.now(timezone.utc)  # 참고자료 신선도는 실제 시계로 판정한다
    item.references_list = [{"title": "치질", "url": CATALOG_HEMORRHOID}]
    item.reference_checks = [
        {
            **reference_check_record(
                CATALOG_HEMORRHOID,
                verdict="pass",
                reason="curated_verified",
                checked_at=checked,
                curated=True,
                status=200,
                final_url=CATALOG_HEMORRHOID,
                page_title="치핵 | 국가건강정보포털 | 질병관리청",
                text_len=800,
                verified_at=checked,
            ),
            "topic_fingerprint": item_topic_fingerprint(item),
        }
    ]
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = claimed_at.to("UTC").datetime
    db.commit()
    return hospital, item


def _claim(db, item, at):
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = at.to("UTC").datetime
    db.commit()


def _stored(db, item):
    db.expire_all()
    db.refresh(item)
    return (
        item.content_revision,
        item.references_list,
        item.reference_checks,
        item.status,
        item.essence_check_summary,
        item.generation_claim_token,
    )


def _operator_lines(db, hospital) -> int:
    return sum(_section_text(row).count(LINE_TITLE) for row in _digests_for(db, hospital))


def test_last_run_neither_holds_nor_touches_a_claimed_cost_post(
    gate_db, incidents, monkeypatch
):
    """D23(2026-10-13): 진료비 글은 목록 문서를 빼 참고자료가 비어도 보류가 아니다 — 23시의 claim
    행은 보고할 것도 없고 행도 그대로다."""

    db = gate_db
    hospital, item = _claimed_cost_post(db, claimed_at=_kst(TODAY, 22, 40))
    before = _stored(db, item)

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 23, 30))

    assert fetcher.calls == [] and incidents == []
    assert _digests_for(db, hospital) == []
    assert _stored(db, item) == before


def test_a_claimed_cost_post_at_noon_is_skipped_with_nothing_reported(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, item = _claimed_cost_post(db, claimed_at=_kst(TODAY, 11, 10))
    before = _stored(db, item)

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 12))

    assert fetcher.calls == [] and incidents == []
    assert _digests_for(db, hospital) == []
    assert _stored(db, item) == before


def test_s2_a_claimed_cost_post_with_a_dead_outside_url_is_not_held(
    gate_db, incidents, monkeypatch
):
    """리뷰어 S2의 글(D23 뒤) — 유일한 참고자료가 죽은 목록 밖 주소여도 진료비 글은 보류가 아니다."""

    db = gate_db
    hospital, schedule = _hospital(db)
    item = _written(db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1)
    _claim(db, item, _kst(TODAY, 22, 40))
    before = _stored(db, item)

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 23, 30))

    assert fetcher.calls == [DEAD_URL]  # 종전 23시처럼 잠금 전 GET
    assert incidents == []
    assert _digests_for(db, hospital) == []
    assert _stored(db, item) == before


def test_a_claimed_row_whose_site_is_down_at_the_last_run_is_deferred_like_an_unclaimed_one(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, schedule = _hospital(db)
    claimed = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1
    )
    unclaimed = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=2
    )
    _claim(db, claimed, _kst(TODAY, 22, 40))
    before = _stored(db, claimed)
    enqueued: list[dict] = []
    enqueue = content_publish_notifications.enqueue_generation_blocked_digest_sync

    def recording_enqueue(digest_db, cycle_date, batch, outcomes, **kwargs):
        enqueued.extend(dict(outcome) for outcome in outcomes)
        return enqueue(digest_db, cycle_date, batch, outcomes, **kwargs)

    monkeypatch.setattr(tasks, "enqueue_generation_blocked_digest_sync", recording_enqueue)

    fetcher = _publisher_run(
        db, monkeypatch, _kst(TODAY, 23, 30), pages={DEAD_URL: TimeoutError("site down")}
    )

    assert fetcher.calls == [DEAD_URL]  # 같은 실행 안의 같은 주소는 캐시가 한 번만 연다
    assert incidents == []  # 보류가 아니다
    by_item = {entry["content_id"]: entry["code"] for entry in enqueued}
    assert by_item == {
        claimed.id: REFERENCE_SITE_UNREACHABLE_CODE,
        unclaimed.id: REFERENCE_SITE_UNREACHABLE_CODE,
    }
    assert _stored(db, claimed) == before
    db.refresh(unclaimed)
    assert unclaimed.reference_checks  # claim 없는 글은 종전처럼 미룸 기록을 행에 남긴다
    assert unclaimed.status is ContentStatus.DRAFT


def test_a_claimed_cost_post_with_a_dead_outside_url_at_noon_is_not_fetched(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, schedule = _hospital(db)
    item = _written(db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1)
    _claim(db, item, _kst(TODAY, 11, 10))
    before = _stored(db, item)

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 12))

    assert fetcher.calls == [] and incidents == []
    assert _digests_for(db, hospital) == []
    assert _stored(db, item) == before


# ── 인시던트 경로(#185 리뷰 A2) ───────────────────────────────────────────


@pytest.fixture
def real_incidents(gate_db, monkeypatch):
    """실제 `open_generation_incident`가 같은 테스트 트랜잭션의 세션으로 돈다(캡처 없음)."""

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: (lambda: _AsyncSessionFacade(gate_db)),
    )
    return gate_db


def _decided_attempt() -> dict:
    """사람의 결정으로 굳은 저장 시도 기록 — 종착이라 인시던트가 `next_retry_at` 키를 지운다."""

    return {
        "context": "a2-pg-context",
        "reason": "MISSING_REFERENCES",
        "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
        "next_retry_at": None,
        OPERATOR_DECIDES_KEY: True,
    }


def _row_json(db, item) -> str:
    db.expire_all()
    return db.execute(
        text("SELECT row_to_json(c)::text FROM content_items c WHERE c.id = :id"),
        {"id": item.id},
    ).scalar_one()


def _item_incidents(db, item) -> list[Incident]:
    return list(
        db.execute(
            select(Incident).where(
                Incident.source_type == "CONTENT_GENERATION",
                Incident.source_id == str(item.id),
            )
        ).scalars()
    )


def test_the_real_incident_does_not_write_a_live_claimed_row_at_the_last_run(
    real_incidents, monkeypatch
):
    """A2 — 23시 사본 판정 뒤의 실제 인시던트도 claim이 살아 있는 행은 쓰지 않는다.

    D36(2026-10-14) 뒤로 빈 참고자료는 어떤 글도 보류하지 않으므로, 결정적 검사(금지 표현)로
    보류되는 글로 본다.
    """

    db = real_incidents
    hospital, schedule = _hospital(db)
    item = _written(
        db, hospital, schedule, title=NO_CATALOG_TITLE, scheduled_date=TODAY, sequence_no=1
    )
    item.meta_description = "완치를 약속드립니다."
    db.commit()
    _claim(db, item, _kst(TODAY, 22, 40))
    before = _row_json(db, item)

    _publisher_run(db, monkeypatch, _kst(TODAY, 23, 30))

    assert _row_json(db, item) == before  # 모든 열이 바이트 그대로
    [incident] = _item_incidents(db, item)
    assert incident.safe_error_code == "FORBIDDEN_EXPRESSION"


def test_the_real_incident_opens_nothing_for_a_cost_post_with_a_pre_d23_operator_record(
    real_incidents, monkeypatch
):
    """D23 이전에 남은 '사람이 정하는 참고자료 보류' 기록이 있어도 진료비 글은 이제 보류가 아니다."""

    db = real_incidents
    hospital, item = _claimed_cost_post(db, claimed_at=_kst(TODAY, 20, 40))
    item.essence_check_summary = {"generation_attempt": _decided_attempt()}
    db.commit()

    _publisher_run(db, monkeypatch, _kst(TODAY, 23, 30))

    assert _item_incidents(db, item) == []
    assert _operator_lines(db, hospital) == 0
