"""08:00~23:00 발행기(`_auto_publish_one`)도 살아 있는 생성 claim의 행을 쓰지 않는다.

07:45 게이트(#180)와 같은 규칙이다. 생성 워커가 slot을 잡고 있으면(`generation_claim_is_active`)
마지막 발행기가 아닌 시각에는 행 모양(빈 슬롯·확인이 끝난 참고자료·미확정 참고자료)과 무관하게
통째로 건너뛴다 — GET·재검증 적용·판정 기록(`apply_publication_assessment`)·시도 기록
(`_record_gate_blocker_decision`)·run·감사·인시던트·요약 줄·발행이 모두 없다. 예정일의 마지막
발행기(23시)와 지난 예정일(`reference_outage_alert_due`)에는 조용히 빠지지 않게 분리된 사본으로
판정해, 보류면 claim 없는 글과 같은 인시던트·요약 줄을 내고 행은 그대로 둔다. 보류가 아니면
공개하지 않고 건너뛴다. 만료된 claim은 종전 그대로다.

네트워크·DB는 쓰지 않는다 — 가짜 세션과 가짜 fetcher만 쓴다.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import arrow
import pytest

from app.services.reference_verification import ReferenceVerifier, override_reference_fetcher
from app.workers import generation_retry_policy, nightly_generation_batch, tasks
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from tests.reference_fetch_doubles import PageFetcher
from tests.test_tasks_nightly import (
    _approved_philosophy,
    _arm_external_effect_tripwires,
    _AutoPublishDB,
    _DigestGateDB,
    _publication_hospital,
    _publication_item,
)

SLOT = date(2026, 6, 10)  # `_publication_item`의 예정일


def _kst(hour, minute=0, *, day=SLOT):
    return arrow.get(day.year, day.month, day.day, hour, minute, tzinfo="Asia/Seoul")


def _set_clock(monkeypatch, moment: arrow.Arrow) -> None:
    """발행기·claim 판정·시도 기록이 같은 '지금'을 보게 한다(PG 테스트와 같은 방식)."""

    now = moment.datetime

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: moment)


def _claim(item, at: arrow.Arrow) -> None:
    item.generation_claim_token = uuid.uuid4()
    item.generation_claimed_at = at.to("UTC").datetime


def _row_bytes(item) -> str:
    return json.dumps(vars(item), default=str, sort_keys=True)


def _complete_row(hospital, *, sequence_no=1):
    """본문·인증 이미지·신선한 통과 기록이 모두 있는(claim이 없다면 발행되는) 글."""

    item = _publication_item(hospital, body="진료 전 확인할 점을 안내합니다.")
    item.sequence_no = sequence_no
    item.content_revision = 3
    return item


def _empty_slot(hospital, *, sequence_no=1):
    """아직 쓰이지 않은 슬롯(시도 기록 없음) — 판정은 CONTENT_NOT_GENERATED다."""

    item = _publication_item(hospital, body="임시", title="임시")
    for field in (
        "title",
        "body",
        "meta_description",
        "faq_question",
        "faq_answer_summary",
        "references_list",
        "reference_checks",
        "image_url",
        "image_content_hash",
        "image_subject_hash",
        "image_policy_version",
        "image_policy_verified_at",
    ):
        setattr(item, field, None)
    item.sequence_no = sequence_no
    item.content_revision = 1
    item.essence_check_summary = {"automatic_remediation_attempts": 0}
    return item


def _textual_row_without_image(hospital):
    """본문·확인이 끝난 참고자료는 있고 이미지만 아직 없는 글(워커가 이미지를 만드는 중)."""

    item = _complete_row(hospital)
    for field in (
        "image_url",
        "image_content_hash",
        "image_subject_hash",
        "image_policy_version",
        "image_policy_verified_at",
    ):
        setattr(item, field, None)
    item.essence_check_summary = {"automatic_remediation_attempts": 0}
    return item


def _publisher(monkeypatch, *items):
    """08:00 발행기 태스크 전체의 더블 — 후보 조회·`_auto_publish_one`·요약 조립·outbox 중복 키는
    실제 경로다. 인시던트는 별도 async 세션이라 호출만 잡는다."""

    db = _DigestGateDB(*items)
    incidents: list[dict] = []

    async def capture_incident(**kwargs):
        incidents.append(kwargs)

    philosophy = _approved_philosophy()
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)

    async def recover_incidents(*_args, **_kwargs):
        return None

    monkeypatch.setattr(tasks, "recover_generation_incidents", recover_incidents)
    db.runs = []  # 차단 run — 실제 조회는 이 더블이 받지 못해 생성만 기록한다

    def block_run(_db, *, item, **_kwargs):
        run = SimpleNamespace(id=uuid.uuid4(), item_id=item.id)
        db.runs.append(run)
        return run

    monkeypatch.setattr(tasks, "ensure_publication_block_run", block_run)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", db)
    db.lines = []  # 요약에 실린 (글, 코드) — 조립·중복 키는 실제 경로다
    enqueue = tasks.enqueue_generation_blocked_digest_sync

    def recording_enqueue(digest_db, cycle_date, batch, outcomes, **kwargs):
        db.lines.extend((outcome["content_id"], outcome["code"]) for outcome in outcomes)
        return enqueue(digest_db, cycle_date, batch, outcomes, **kwargs)

    monkeypatch.setattr(tasks, "enqueue_generation_blocked_digest_sync", recording_enqueue)
    effects = _arm_external_effect_tripwires(monkeypatch)
    return db, incidents, effects


def _run_publisher(fetcher: PageFetcher | None = None) -> PageFetcher:
    fetcher = fetcher or PageFetcher()
    with override_reference_fetcher(fetcher):
        tasks.morning_content_auto_publish.run()
    return fetcher


def _digest_lines(db) -> list[tuple[object, str]]:
    return list(db.lines)


def _single_publish(monkeypatch, item, hospital):
    """`_auto_publish_one` 한 건의 더블(발행 경로의 outbox·IndexNow 조회까지 받는다)."""

    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_a: _approved_philosophy()
    )
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    return db, effects


# ── (a)(b) 마지막 발행기가 아닌 시각 — 통째로 건너뛴다 ─────────────────────────────


def test_eight_leaves_a_live_claimed_empty_slot_entirely_alone(monkeypatch):
    """(a) 07:46에 워커가 잡은 빈 슬롯, 08:00. 종전에는 판정 기록·시도 기록(CONTENT_NOT_GENERATED,
    OPERATOR_REQUIRED)을 행에 쓰고 run·감사·인시던트·요약 줄을 냈다 — 워커가 지금 쓰는 슬롯이다."""

    hospital = _publication_hospital()
    slot = _empty_slot(hospital)
    _claim(slot, _kst(7, 46))
    before = _row_bytes(slot)
    db, incidents, effects = _publisher(monkeypatch, slot)
    _set_clock(monkeypatch, _kst(8))

    fetcher = _run_publisher()

    assert _row_bytes(slot) == before  # essence_check_summary·content_revision·status 모두 그대로
    assert GENERATION_ATTEMPT_KEY not in slot.essence_check_summary
    assert (slot.content_revision, slot.status) == (1, tasks.ContentStatus.DRAFT)
    assert incidents == [] and db.outbox() == []
    assert db.added == [] and db.runs == []  # run·감사 기록도 없다
    assert fetcher.calls == [] and effects == {"revalidate": [], "indexnow": []}


def test_ten_does_not_publish_a_live_claimed_row_whose_references_are_settled(monkeypatch):
    """(b) 본문·확인이 끝난 참고자료·인증 이미지 — claim이 없다면 발행되는 글. 10시에 워커가 잡고
    있으면(본문·이미지를 쓰는 중) 공개하지 않는다. 종전에는 claim과 무관하게 공개했다."""

    hospital = _publication_hospital()
    item = _complete_row(hospital)
    _claim(item, _kst(9, 40))
    before = _row_bytes(item)
    db, effects = _single_publish(monkeypatch, item, hospital)
    _set_clock(monkeypatch, _kst(10))
    fetcher = PageFetcher()

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher))

    assert payload is None
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert _row_bytes(item) == before
    assert db.added == [] and fetcher.calls == []
    assert effects == {"revalidate": [], "indexnow": []}


@pytest.mark.parametrize("hour", [8, 12, 22])
def test_every_non_last_hour_skips_every_shape_of_a_live_claimed_row(monkeypatch, hour):
    """빈 슬롯·발행 가능한 글·이미지 없는 글이 한 실행에 함께 있어도 아무것도 쓰지 않는다."""

    hospital = _publication_hospital()
    rows = [
        _empty_slot(hospital, sequence_no=1),
        _complete_row(hospital, sequence_no=2),
        _textual_row_without_image(hospital),
    ]
    rows[2].sequence_no = 3
    for row in rows:
        _claim(row, _kst(hour - 1, 50))
    before = [_row_bytes(row) for row in rows]
    db, incidents, effects = _publisher(monkeypatch, *rows)
    _set_clock(monkeypatch, _kst(hour))

    _run_publisher()

    assert [_row_bytes(row) for row in rows] == before
    assert incidents == [] and db.outbox() == [] and db.added == [] and db.runs == []
    assert effects == {"revalidate": [], "indexnow": []}


# ── (c)(d) 예정일의 마지막 발행기(23시) — 사본으로 판정, 행은 그대로 ────────────────────


def test_last_run_reports_a_live_claimed_empty_slot_like_an_unclaimed_one_without_writing_it(
    monkeypatch,
):
    """(c) 23시에 claim이 살아 있는 빈 슬롯 — 건너뛰면 그날의 보류가 조용히 빠진다. claim 없는 같은
    슬롯과 같은 인시던트 하나·요약 줄 하나를 내되, 행에는 판정·시도 기록을 쓰지 않는다. 다음 날
    (claim 만료) 발행기가 종전처럼 기록을 남겨도 요약의 중복 키가 같아 줄이 다시 나가지 않는다."""

    reports = {}
    for claimed in (False, True):
        hospital = _publication_hospital()
        slot = _empty_slot(hospital)
        if claimed:
            _claim(slot, _kst(22, 20))
        before = _row_bytes(slot)
        db, incidents, _effects = _publisher(monkeypatch, slot)
        _set_clock(monkeypatch, _kst(23))

        _run_publisher()

        assert [(call["item_id"], call["code"]) for call in incidents] == [
            (slot.id, "CONTENT_NOT_GENERATED")
        ]
        assert _digest_lines(db) == [(slot.id, "CONTENT_NOT_GENERATED")]
        assert len(db.outbox()) == 1
        if claimed:
            assert _row_bytes(slot) == before
            assert GENERATION_ATTEMPT_KEY not in slot.essence_check_summary
            assert slot.content_revision == 1 and slot.status is tasks.ContentStatus.DRAFT
            assert [log.detail.get("generation_claim_active") for log in db.added if hasattr(log, "action")] == [True]
        else:
            # 대조군 — claim 없는 슬롯은 종전처럼 정본 시도 기록을 남긴다.
            assert slot.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == (
                "CONTENT_NOT_GENERATED"
            )

        # 다음 날 08:00 — claim은 만료됐다(워커가 죽었다). 종전처럼 판정·기록하고 인시던트를
        # 이어가지만 같은 차단이라 요약 중복 키가 같다(시도 지문 포함).
        incidents.clear()
        _set_clock(monkeypatch, _kst(8, day=SLOT + timedelta(days=1)))
        _run_publisher()
        assert [call["code"] for call in incidents] == ["CONTENT_NOT_GENERATED"]
        assert len(db.outbox()) == 1
        reports[claimed] = [
            (line_id == slot.id, code) for line_id, code in _digest_lines(db)
        ]

    assert reports[True] == reports[False]


def test_last_run_records_the_decision_only_on_the_copy_and_commits_once(monkeypatch):
    """23시 claim 빈 슬롯 — 판정·시도 기록은 분리된 사본에만 남고(요약 지문이 claim 없는 글과 같다),
    발행기 세션은 #185처럼 끝에서 한 번만 커밋한다(행 잠금이 판정 도중 풀리지 않는다)."""

    hospital = _publication_hospital()
    slot = _empty_slot(hospital)
    _claim(slot, _kst(22, 20))
    before = _row_bytes(slot)
    db, _effects = _single_publish(monkeypatch, slot, hospital)
    _set_clock(monkeypatch, _kst(23))
    philosophy = _approved_philosophy()
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: philosophy)

    payload = tasks._auto_publish_one(slot.id, reference_verifier=ReferenceVerifier(PageFetcher()))

    assert payload["kind"] == "blocked" and payload["code"] == "CONTENT_NOT_GENERATED"
    assert _row_bytes(slot) == before
    assert db.commits == 1
    # 사본의 기록 — claim 없는 행이 같은 판정에서 남기는 지문과 같다.
    assert payload["attempt_fingerprint"] == tasks._generation_attempt_context(slot, philosophy)
    assert payload["essence_check_summary"][GENERATION_ATTEMPT_KEY]["reason"] == (
        "CONTENT_NOT_GENERATED"
    )


def test_last_run_never_publishes_a_live_claimed_publishable_row(monkeypatch):
    """(d) 23시, 본문·확인이 끝난 참고자료·인증 이미지가 있는 claim 행 — 사본 판정은 보류가 아니지만
    생성 중인 행이라 공개하지 않는다. 보고할 보류도 없다."""

    hospital = _publication_hospital()
    item = _complete_row(hospital)
    _claim(item, _kst(22, 30))
    before = _row_bytes(item)
    db, effects = _single_publish(monkeypatch, item, hospital)
    _set_clock(monkeypatch, _kst(23))

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher()))

    assert payload is None
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert _row_bytes(item) == before
    assert db.added == []
    assert effects == {"revalidate": [], "indexnow": []}


def test_a_live_claimed_empty_slot_past_its_date_is_judged_like_the_last_run(monkeypatch):
    """지난 예정일(catch-up)은 이미 마지막 발행기를 지났다 — 다음 날 10시에도 사본으로 보고한다."""

    hospital = _publication_hospital()
    slot = _empty_slot(hospital)
    next_day = SLOT + timedelta(days=1)
    _claim(slot, _kst(9, 40, day=next_day))
    before = _row_bytes(slot)
    db, incidents, _effects = _publisher(monkeypatch, slot)
    _set_clock(monkeypatch, _kst(10, day=next_day))

    _run_publisher()

    assert [call["code"] for call in incidents] == ["CONTENT_NOT_GENERATED"]
    assert _digest_lines(db) == [(slot.id, "CONTENT_NOT_GENERATED")]
    assert _row_bytes(slot) == before


# ── (e) 만료된 claim은 종전 그대로 ─────────────────────────────────────────────────


def test_an_expired_claim_is_judged_recorded_and_reported_as_before(monkeypatch):
    hospital = _publication_hospital()
    slot = _empty_slot(hospital)
    _claim(slot, _kst(5, 59))  # 08:00 기준 TTL(2h) 밖 — 죽은 워커의 흔적이다
    db, incidents, _effects = _publisher(monkeypatch, slot)
    _set_clock(monkeypatch, _kst(8))

    _run_publisher()

    assert [call["code"] for call in incidents] == ["CONTENT_NOT_GENERATED"]
    assert _digest_lines(db) == [(slot.id, "CONTENT_NOT_GENERATED")]
    assert slot.essence_check_summary[GENERATION_ATTEMPT_KEY]["reason"] == "CONTENT_NOT_GENERATED"
    assert [log.action for log in db.added if hasattr(log, "action")] == [
        tasks.AUTO_PUBLISH_BLOCKED_ACTION
    ]


def test_an_expired_claim_on_a_publishable_row_is_published_as_before(monkeypatch):
    hospital = _publication_hospital()
    item = _complete_row(hospital)
    _claim(item, _kst(7, 59))  # 10:00 기준 TTL 밖
    _single_publish(monkeypatch, item, hospital)
    _set_clock(monkeypatch, _kst(10))

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher()))

    assert payload is not None and payload["kind"] == "published"
    assert item.status is tasks.ContentStatus.PUBLISHED


def test_the_claim_is_judged_under_the_row_lock_not_at_the_unlocked_read(monkeypatch):
    """잠금 전 읽기 때는 claim이 없었고, 그 뒤(잠금 직전) 워커가 잡았다 — 잠근 행으로 판정한다."""

    hospital = _publication_hospital()
    item = _complete_row(hospital)
    item.generation_claim_token = item.generation_claimed_at = None
    before_claim = _row_bytes(item)
    db, effects = _single_publish(monkeypatch, item, hospital)
    _set_clock(monkeypatch, _kst(10))
    prefetch = tasks._prefetch_publication_references

    def worker_claims_after_the_unlocked_read(*args, **kwargs):
        result = prefetch(*args, **kwargs)
        _claim(item, _kst(10))
        return result

    monkeypatch.setattr(
        tasks, "_prefetch_publication_references", worker_claims_after_the_unlocked_read
    )

    assert tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher())) is None
    assert item.status is tasks.ContentStatus.DRAFT
    assert db.added == [] and effects == {"revalidate": [], "indexnow": []}
    assert before_claim != _row_bytes(item)  # 달라진 것은 워커의 claim 열뿐이다
    item.generation_claim_token = item.generation_claimed_at = None
    assert _row_bytes(item) == before_claim


# ── (f) 워커가 뒤늦게 오래된 JSON을 다시 쓰는 경합 ──────────────────────────────────


class _WorkerSession:
    """워커 세션의 작업 단위 모사 — 커밋은 추적 객체에서 **바뀐 열만** 행에 쓴다(ORM flush와 같다).

    실제 세션은 `expire_on_commit=False`라 워커의 추적 객체는 마지막 refresh 뒤의 값을 들고 있다.
    그 사이 다른 트랜잭션(발행기)이 쓴 값은 추적 객체에 없다.
    """

    def __init__(self, row, tracked):
        self.row = row
        self.tracked = tracked
        self._loaded = copy.deepcopy(vars(tracked))

    def commit(self):
        for name, value in vars(self.tracked).items():
            if name == "hospital":
                continue
            if self._loaded.get(name) != value:
                setattr(self.row, name, copy.deepcopy(value))
        self._loaded = copy.deepcopy(vars(self.tracked))

    def rollback(self):  # pragma: no cover — 이 경로는 예외가 없다
        pass


def test_a_worker_mid_image_is_not_paged_and_its_stale_flush_erases_nothing(monkeypatch):
    """(f) 경합 재현. 워커가 본문을 저장하고 refresh한 뒤 이미지 공급자를 부르는 중(claim 유지)에
    10시 발행기가 돈다. 종전 발행기는 이미지 증상(CONTENT_IMAGE_NOT_READY)으로 판정 기록과 시도
    기록을 행에 쓰고 인시던트·요약 줄("이미지 공급자 크레딧 확인")을 냈다. 이미지 공급자가 URL 없이
    끝나면 워커의 `_remember_image_failure`는 refresh 이전의 추적 객체로 `essence_check_summary`
    전체를 다시 쓰므로, 발행기가 남긴 판정·시도 기록이 조용히 사라지고 인시던트만 남는다(기한을
    빌린 기록이 없는 인시던트). 이제 발행기는 이 행을 쓰지도 알리지도 않는다."""

    hospital = _publication_hospital()
    row = _textual_row_without_image(hospital)
    _claim(row, _kst(9, 52))
    philosophy = _approved_philosophy()
    worker_item = copy.copy(row)  # 본문 write-back 뒤 refresh한 워커의 추적 객체
    for name, value in vars(row).items():
        if name != "hospital":
            setattr(worker_item, name, copy.deepcopy(value))
    worker_db = _WorkerSession(row, worker_item)
    db, incidents, _effects = _publisher(monkeypatch, row)
    _set_clock(monkeypatch, _kst(10))
    before = _row_bytes(row)
    observed: dict[str, object] = {}

    def publisher_runs_during_the_image_phase(_hospital):
        # 이미지 공급자 호출 직전(동기 지점) — 워커의 추적 객체는 이미 refresh가 끝났다.
        _run_publisher()
        observed["row_after_publisher"] = _row_bytes(row)

    async def image_provider_without_a_url(*_args, **_kwargs):
        return None, None  # 공급자가 URL 없이 끝났다

    monkeypatch.setattr(tasks, "hospital_image_direction", publisher_runs_during_the_image_phase)
    monkeypatch.setattr(tasks, "generate_image", image_provider_without_a_url)

    state = tasks._recover_missing_content_image(worker_db, worker_item, hospital, philosophy)

    assert state == tasks.GenerationItemState.PARTIAL
    assert observed["row_after_publisher"] == before  # 발행기는 워커의 행을 쓰지 않았다
    assert incidents == [] and db.outbox() == [] and db.added == [] and db.runs == []
    attempt = row.essence_check_summary[GENERATION_ATTEMPT_KEY]
    assert attempt["reason"] == "IMAGE_GENERATION_FAILED"  # 워커의 기록만 남는다
    assert attempt["provider_attempt_count"] == 1
