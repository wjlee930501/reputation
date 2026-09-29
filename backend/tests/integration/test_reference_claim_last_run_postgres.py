"""그날 마지막 발행기(23시)의 claim 행 — 실제 Postgres로 검증한다(#185 리뷰 S2).

진료비 글이 예정일 D에 본문을 갖고 있고, 22:10에 생성 워커가 잡아 23시에도 claim이 살아
있다. 23시 발행기 태스크 전체(후보 조회·`_auto_publish_one`·요약 조립·outbox)가 종전처럼 잠금
전 재검증(GET)을 하고, 그 결과를 행이 아닌 분리된 사본에 적용해 인시던트와 #181 운영자 판단
줄을 낸다. 행의 판·참고자료·검증 기록·상태는 DB에서 다시 읽어도 그대로다. 기관 장애로 미뤄진
글은 claim 없는 글과 같은 결과(기관 접속 불가 요약 줄, 보류 없음)다. 같은 행이 12시(마지막
발행기가 아님)에는 GET 없이 건너뛰고, 다음 날 발행기는 운영자 줄을 다시 싣지 않는다. 인시던트는
별도 async 세션이라 호출만 잡는다. 참고자료 GET은 가짜 fetcher가 받는다(네트워크 없음).
"""

import uuid
from datetime import datetime, timedelta, timezone

import arrow

from app.models.content import ContentStatus
from app.services import content_publish_notifications
from app.services.reference_publication import REFERENCE_SITE_UNREACHABLE_CODE
from app.services.reference_verification import (
    item_topic_fingerprint,
    override_reference_fetcher,
    reference_check_record,
)
from app.workers import generation_retry_policy, nightly_generation_batch, tasks
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
from tests.reference_fetch_doubles import PageFetcher

# #181 요약 테스트의 실제 행·outbox 픽스처를 그대로 쓴다(발행기 세션·승인 운영 기준·인시던트 캡처).
pg_session = digest.pg_session
gate_db = digest.gate_db
incidents = digest.incidents

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

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
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


def test_last_run_reports_a_claimed_cost_post_once_and_leaves_the_row(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, item = _claimed_cost_post(db, claimed_at=_kst(TODAY, 22, 10))
    before = _stored(db, item)

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 23))

    assert fetcher.calls == []
    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (item.id, "MISSING_REFERENCES")
    ]
    [row] = _digests_for(db, hospital)
    text = _section_text(row)
    assert text.count(LINE_TITLE) == 1 and f"{LINE_TITLE} 1편" in text
    assert _stored(db, item) == before
    assert before[3] is ContentStatus.DRAFT

    # 다음 날: claim은 만료됐다(TTL 2h). 07:45·08:00이 종전처럼 판정하고 인시던트를 이어가지만
    # 지난 예정일이라 운영자 줄은 다시 싣지 않는다.
    next_day = TODAY + timedelta(days=1)
    for moment in (_kst(next_day, 7, 45), _kst(next_day, 8)):
        incidents.clear()
        _publisher_run(db, monkeypatch, moment)
        assert [call["code"] for call in incidents] == ["MISSING_REFERENCES"]
    assert _operator_lines(db, hospital) == 1
    db.refresh(item)
    assert item.references_list == []  # claim이 풀린 뒤에는 종전처럼 목록 문서를 뺐다


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


def test_s2_a_claimed_cost_post_with_a_dead_outside_url_is_reported_once(
    gate_db, incidents, monkeypatch
):
    """리뷰어 S2 그대로 — 진료비 글의 유일한 참고자료가 신선한 통과 없는 목록 밖 죽은 주소(404)."""

    db = gate_db
    hospital, schedule = _hospital(db)
    item = _written(db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=1)
    _claim(db, item, _kst(TODAY, 22, 10))
    before = _stored(db, item)
    assert before[1] == [{"title": "추측 주소", "url": DEAD_URL}] and before[2] is None

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 23))

    assert fetcher.calls == [DEAD_URL]  # 종전 23시처럼 잠금 전 GET
    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (item.id, "MISSING_REFERENCES")
    ]
    [row] = _digests_for(db, hospital)
    assert _section_text(row).count(LINE_TITLE) == 1
    assert _stored(db, item) == before

    next_day = TODAY + timedelta(days=1)
    for moment in (_kst(next_day, 7, 45), _kst(next_day, 8), _kst(next_day, 9)):
        incidents.clear()
        _publisher_run(db, monkeypatch, moment)
        assert [call["code"] for call in incidents] == ["MISSING_REFERENCES"]
    assert _operator_lines(db, hospital) == 1  # 다음 날 중복 줄 없음


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
    _claim(db, claimed, _kst(TODAY, 22, 10))
    before = _stored(db, claimed)
    enqueued: list[dict] = []
    enqueue = content_publish_notifications.enqueue_generation_blocked_digest_sync

    def recording_enqueue(digest_db, cycle_date, batch, outcomes, **kwargs):
        enqueued.extend(dict(outcome) for outcome in outcomes)
        return enqueue(digest_db, cycle_date, batch, outcomes, **kwargs)

    monkeypatch.setattr(tasks, "enqueue_generation_blocked_digest_sync", recording_enqueue)

    fetcher = _publisher_run(
        db, monkeypatch, _kst(TODAY, 23), pages={DEAD_URL: TimeoutError("site down")}
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
