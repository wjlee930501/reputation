"""발행기(08:00~23:00)는 살아 있는 생성 claim의 행을 쓰지 않는다 — 실제 Postgres로 검증한다.

발행기 태스크 전체(후보 조회·`_auto_publish_one`·요약 조립·outbox)를 돈다. 행은 둘이다 — 07:46류의
워커가 잡은 빈 슬롯과, claim이 없었다면 발행될 완성된 글. 대조군으로 claim 없는 완성된 글을 둔다.

- 마지막 발행기가 아닌 시각(12시): 두 claim 행 모두 인시던트·감사·run·요약 줄 없이 DB에서 다시
  읽어도 그대로다. 대조군은 발행된다.
- 예정일의 마지막 발행기(23시): 빈 슬롯은 claim 없는 슬롯과 같은 인시던트·요약 줄 하나를 내되 행은
  그대로다. 완성된 글은 공개되지 않는다.
- 다음 날(claim 만료): 종전처럼 판정·기록하지만 요약 중복 키가 같아 같은 줄을 다시 내지 않는다.

완성된 글의 판정은 이 테스트의 관심사가 아니라(이미지 인증·AI 검수 등) 실제 판정 뒤에 "발행 가능"
으로 바꿔 둔다. 참고자료는 신선한 통과 기록이 있어 GET하지 않는다. 인시던트는 별도 async 세션이라
대개 호출만 잡는다 — 행 모양별 바이트 비교 테스트는 실제 `open_generation_incident`를 같은 테스트
트랜잭션 위에서 돌린다(`real_incidents`, #185 리뷰 A2와 같은 방식).
"""

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.audit import AdminAuditLog
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.operations import OperationRun
from app.services.reference_verification import item_topic_fingerprint, reference_check_record
from app.workers import tasks
from tests.integration import test_operator_decides_digest_postgres as digest
from tests.integration import test_reference_claim_last_run_postgres as last_run
from tests.integration.test_operator_decides_digest_postgres import (
    COST_TITLE,
    DEAD_URL,
    MEDICAL_TITLE,
    TODAY,
    _digests_for,
    _hospital,
    _written,
)
from tests.integration.test_reference_claim_last_run_postgres import (
    _item_incidents,
    _kst,
    _operator_lines,
    _publisher_run,
    _row_json,
)

pg_session = digest.pg_session
gate_db = digest.gate_db
incidents = digest.incidents
real_incidents = last_run.real_incidents  # 실제 `open_generation_incident`(같은 테스트 트랜잭션)

FRESH_URL = "https://health.kdca.go.kr/healthinfo/example"


def _empty_slot(db, hospital, schedule, *, sequence_no):
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=sequence_no,
        total_count=12,
        scheduled_date=TODAY,
        status=ContentStatus.DRAFT,
        essence_check_summary={"automatic_remediation_attempts": 0},
    )
    db.add(item)
    db.flush()
    return item


def _complete_row(db, hospital, schedule, *, sequence_no):
    item = ContentItem(
        hospital_id=hospital.id,
        schedule_id=schedule.id,
        content_type=ContentType.DISEASE,
        sequence_no=sequence_no,
        total_count=12,
        title=MEDICAL_TITLE,
        body="## 안내\n진료 기준과 내원 시점을 안내합니다.",
        scheduled_date=TODAY,
        status=ContentStatus.DRAFT,
        references_list=[{"title": "질병관리청", "url": FRESH_URL}],
    )
    db.add(item)
    db.flush()
    checked = datetime.now(timezone.utc)  # 참고자료 신선도는 실제 시계로 판정한다
    item.reference_checks = [
        {
            **reference_check_record(
                FRESH_URL,
                verdict="pass",
                reason="page_verified",
                checked_at=checked,
                curated=False,
                status=200,
                final_url=FRESH_URL,
                page_title="치핵 | 국가건강정보포털 | 질병관리청",
                text_len=900,
                verified_at=checked,
            ),
            "topic_fingerprint": item_topic_fingerprint(item),
        }
    ]
    return item


def _claim(db, items, at):
    for item in items:
        item.generation_claim_token = uuid.uuid4()
        item.generation_claimed_at = at.to("UTC").datetime
    db.commit()


_STORED_COLUMNS = (
    "content_revision",
    "status",
    "essence_status",
    "essence_check_summary",
    "content_philosophy_id",
    "references_list",
    "reference_checks",
    "published_at",
    "generation_claim_token",
    "generation_claimed_at",
)


def _stored(db, item):
    db.expire_all()
    row = db.get(ContentItem, item.id)
    return {name: getattr(row, name) for name in _STORED_COLUMNS}


def _audits(db, item) -> list[AdminAuditLog]:
    return list(
        db.execute(select(AdminAuditLog).where(AdminAuditLog.target_id == str(item.id))).scalars()
    )


def _runs(db, item) -> list[OperationRun]:
    return [
        run
        for run in db.execute(select(OperationRun)).scalars()
        if ((run.request_payload or {}).get("_dispatch") or {}).get("target_id") == str(item.id)
    ]


def _publishable(monkeypatch, *items):
    real = tasks.assess_content_publication
    ids = {item.id for item in items}

    def assess(item, philosophy):
        assessment = real(item, philosophy)
        if item.id in ids:
            return dataclasses.replace(assessment, publishable=True, code=None, message=None)
        return assessment

    async def recovered(*_args, **_kwargs):
        return None

    async def revalidated(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tasks, "assess_content_publication", assess)
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(tasks, "recover_generation_incidents", recovered)
    monkeypatch.setattr(tasks, "trigger_content_site_revalidate_safe", revalidated)


def test_live_claimed_rows_are_left_alone_until_the_last_run_reports_the_empty_slot_once(
    gate_db, incidents, monkeypatch
):
    db = gate_db
    hospital, schedule = _hospital(db)
    slot = _empty_slot(db, hospital, schedule, sequence_no=1)
    complete = _complete_row(db, hospital, schedule, sequence_no=2)
    control = _complete_row(db, hospital, schedule, sequence_no=3)  # claim 없음
    _publishable(monkeypatch, complete, control)
    claimed = (slot, complete)

    # ── 12시(마지막 발행기가 아님) — 워커가 11:10에 잡았다 ──
    _claim(db, claimed, _kst(TODAY, 11, 10))
    before = {item.id: _stored(db, item) for item in claimed}

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 12))

    assert fetcher.calls == [] and incidents == []
    assert _digests_for(db, hospital) == []
    for item in claimed:
        assert _stored(db, item) == before[item.id]
        assert _audits(db, item) == [] and _runs(db, item) == []
    db.refresh(control)
    assert control.status is ContentStatus.PUBLISHED  # 대조군 — 같은 판정의 claim 없는 글

    # ── 23시(예정일의 마지막 발행기) — 워커가 22:10에 다시 잡았다 ──
    _claim(db, claimed, _kst(TODAY, 22, 10))
    before = {item.id: _stored(db, item) for item in claimed}

    _publisher_run(db, monkeypatch, _kst(TODAY, 23))

    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (slot.id, "CONTENT_NOT_GENERATED")
    ]
    assert len(_digests_for(db, hospital)) == 1
    for item in claimed:
        assert _stored(db, item) == before[item.id]  # 판정·시도 기록·판·상태 모두 그대로
    assert before[complete.id]["status"] is ContentStatus.DRAFT
    assert [audit.detail.get("generation_claim_active") for audit in _audits(db, slot)] == [True]
    assert _audits(db, complete) == [] and _runs(db, complete) == []

    # ── 다음 날 08:00 — claim은 만료됐다(워커가 죽었다). 종전처럼 판정·기록하되 같은 줄은 한 번뿐 ──
    incidents.clear()
    _publisher_run(db, monkeypatch, _kst(TODAY + timedelta(days=1), 8))

    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (slot.id, "CONTENT_NOT_GENERATED")
    ]
    assert len(_digests_for(db, hospital)) == 1  # 요약 중복 키가 같다(시도 지문 포함)
    db.refresh(slot)
    db.refresh(complete)
    assert slot.essence_check_summary["generation_attempt"]["reason"] == "CONTENT_NOT_GENERATED"
    assert complete.status is ContentStatus.PUBLISHED  # claim이 풀린 뒤에는 종전처럼 발행한다


def test_an_unclaimed_empty_slot_gets_the_same_line_and_no_duplicate_the_next_day(
    gate_db, incidents, monkeypatch
):
    """대조군 — claim 없는 빈 슬롯의 23시·다음 날. 위 테스트의 claim 행과 같은 보고 수다."""

    db = gate_db
    hospital, schedule = _hospital(db)
    slot = _empty_slot(db, hospital, schedule, sequence_no=1)
    db.commit()

    _publisher_run(db, monkeypatch, _kst(TODAY, 23))
    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (slot.id, "CONTENT_NOT_GENERATED")
    ]
    assert len(_digests_for(db, hospital)) == 1

    incidents.clear()
    _publisher_run(db, monkeypatch, _kst(TODAY + timedelta(days=1), 8))
    assert [(call["item_id"], call["code"]) for call in incidents] == [
        (slot.id, "CONTENT_NOT_GENERATED")
    ]
    assert len(_digests_for(db, hospital)) == 1


# ── 행 모양별 바이트 비교 — 실제 인시던트 경로까지 claim 행을 쓰지 않는다 ──────────────────


def _three_shapes(db, monkeypatch):
    """빈 슬롯·확인이 끝난 참고자료의 발행 가능한 글·미확정 참고자료(목록 밖 죽은 주소)의 진료비 글."""

    hospital, schedule = _hospital(db)
    slot = _empty_slot(db, hospital, schedule, sequence_no=1)
    settled = _complete_row(db, hospital, schedule, sequence_no=2)
    unsettled = _written(
        db, hospital, schedule, title=COST_TITLE, scheduled_date=TODAY, sequence_no=3
    )
    _publishable(monkeypatch, settled)
    return hospital, (slot, settled, unsettled)


def test_every_live_claimed_shape_is_byte_identical_after_the_last_run_with_real_incidents(
    real_incidents, monkeypatch
):
    db = real_incidents
    hospital, rows = _three_shapes(db, monkeypatch)
    slot, settled, unsettled = rows
    _claim(db, rows, _kst(TODAY, 22, 10))
    before = {row.id: _row_json(db, row) for row in rows}

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 23))

    assert {row.id: _row_json(db, row) for row in rows} == before  # row_to_json 바이트 그대로
    assert fetcher.calls == [DEAD_URL]  # 미확정 참고자료는 종전 23시처럼 잠금 전에 GET한다
    assert [incident.safe_error_code for incident in _item_incidents(db, slot)] == [
        "CONTENT_NOT_GENERATED"
    ]
    assert [incident.safe_error_code for incident in _item_incidents(db, unsettled)] == [
        "MISSING_REFERENCES"
    ]
    assert _item_incidents(db, settled) == []
    assert len(_digests_for(db, hospital)) == 1
    assert _operator_lines(db, hospital) == 1  # 예정일 당일 진료비 글의 운영자 판단 줄(#181)
    db.expire_all()
    assert all(db.get(ContentItem, row.id).status is ContentStatus.DRAFT for row in rows)
    assert _audits(db, settled) == [] and _runs(db, settled) == []


def test_every_live_claimed_shape_is_byte_identical_at_a_non_last_hour_with_real_incidents(
    real_incidents, monkeypatch
):
    db = real_incidents
    hospital, rows = _three_shapes(db, monkeypatch)
    _claim(db, rows, _kst(TODAY, 9, 10))
    before = {row.id: _row_json(db, row) for row in rows}

    fetcher = _publisher_run(db, monkeypatch, _kst(TODAY, 10))

    assert {row.id: _row_json(db, row) for row in rows} == before
    assert fetcher.calls == []
    assert _digests_for(db, hospital) == []
    for row in rows:
        assert _item_incidents(db, row) == []
        assert _audits(db, row) == [] and _runs(db, row) == []
