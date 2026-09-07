"""Postgres fencing contract for paid answer/judgment observation slots."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import MeasurementObservationSlot
from app.models.sov import MeasurementRun, QueryMatrix, SovRecord
from app.services.measurement_slots import (
    MAX_STAGE_ATTEMPTS,
    STAGE_LEASE_SECONDS,
    checkpoint_answer,
    checkpoint_judgment,
    claim_slot_stage,
    ensure_v0_slots,
)
from app.workers import tasks


def _record(*, hospital_id, query_id, run_id, raw_response):
    return SovRecord(
        hospital_id=hospital_id,
        query_id=query_id,
        measurement_run_id=run_id,
        ai_platform="chatgpt",
        is_mentioned=True,
        mention_verdict="MATCHED",
        raw_response=raw_response,
        measurement_status="SUCCESS",
    )


def test_takeover_discards_late_provider_returns_and_caps_claimed_attempts(pg_engine):
    suffix = uuid.uuid4().hex[:12]
    protocol = {"version": "test-v1", "repeat_count": 2}
    with Session(pg_engine, expire_on_commit=False) as seed:
        hospital = Hospital(name=f"측정슬롯-{suffix}", slug=f"measurement-slot-{suffix}")
        seed.add(hospital)
        seed.flush()
        query = QueryMatrix(
            hospital_id=hospital.id,
            query_text="노원구 정형외과 추천",
            query_intent="LOCAL",
        )
        run = MeasurementRun(
            hospital_id=hospital.id,
            run_label="slot fencing test",
            status="RUNNING",
            config={"measurement_protocol": protocol},
        )
        seed.add_all([query, run])
        seed.flush()
        slots = ensure_v0_slots(
            seed,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            query_id=query.id,
            platform="chatgpt",
            repeat_count=2,
            protocol=protocol,
        )
        hospital_id, query_id, run_id = hospital.id, query.id, run.id
        first_slot_id, capped_slot_id = slots[0].id, slots[1].id
        seed.commit()

    try:
        base = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
        with (
            Session(pg_engine, expire_on_commit=False) as worker_a,
            Session(pg_engine, expire_on_commit=False) as worker_b,
        ):
            # Preload both identities. populate_existing on the claim is what prevents
            # worker B from claiming from this stale ORM object after A commits.
            worker_a.get(MeasurementObservationSlot, first_slot_id)
            worker_b.get(MeasurementObservationSlot, first_slot_id)

            claimed_a = claim_slot_stage(worker_a, first_slot_id, stage="ANSWER", now=base)
            assert claimed_a is not None
            _, answer_token_a = claimed_a
            worker_a.commit()

            assert (
                claim_slot_stage(
                    worker_b,
                    first_slot_id,
                    stage="ANSWER",
                    now=base + timedelta(minutes=1),
                )
                is None
            )
            worker_b.rollback()

            claimed_b = claim_slot_stage(
                worker_b,
                first_slot_id,
                stage="ANSWER",
                now=base + timedelta(seconds=STAGE_LEASE_SECONDS + 1),
            )
            assert claimed_b is not None
            _, answer_token_b = claimed_b
            worker_b.commit()

            assert (
                checkpoint_answer(
                    worker_a,
                    first_slot_id,
                    {"measurement_status": "SUCCESS", "raw_response": "late A"},
                    lease_token=answer_token_a,
                )
                is None
            )
            worker_a.rollback()

            accepted = checkpoint_answer(
                worker_b,
                first_slot_id,
                {
                    "measurement_status": "SUCCESS",
                    "raw_response": "authoritative B",
                    "answer_model": "test-model",
                    "measurement_method": "TEST",
                    "search_calls": 1,
                    "source_urls": ["https://example.test/source"],
                },
                lease_token=answer_token_b,
            )
            assert accepted is not None
            worker_b.commit()

            answer_row = worker_a.get(
                MeasurementObservationSlot,
                first_slot_id,
                populate_existing=True,
            )
            assert answer_row.raw_response == "authoritative B"
            assert answer_row.answer_attempt_count == 2

            judgment_claim_a = claim_slot_stage(
                worker_a, first_slot_id, stage="JUDGMENT", now=base
            )
            assert judgment_claim_a is not None
            _, judgment_token_a = judgment_claim_a
            worker_a.commit()

            judgment_claim_b = claim_slot_stage(
                worker_b,
                first_slot_id,
                stage="JUDGMENT",
                now=base + timedelta(seconds=STAGE_LEASE_SECONDS + 1),
            )
            assert judgment_claim_b is not None
            _, judgment_token_b = judgment_claim_b
            worker_b.commit()

            late_record = _record(
                hospital_id=hospital_id,
                query_id=query_id,
                run_id=run_id,
                raw_response="late judgment A",
            )
            assert (
                checkpoint_judgment(
                    worker_a,
                    first_slot_id,
                    fingerprint="a" * 64,
                    result={"measurement_status": "SUCCESS", "verdict": "MATCHED"},
                    sov_record=late_record,
                    lease_token=judgment_token_a,
                )
                is None
            )
            worker_a.rollback()

            accepted_record = _record(
                hospital_id=hospital_id,
                query_id=query_id,
                run_id=run_id,
                raw_response="authoritative judgment B",
            )
            checkpointed = checkpoint_judgment(
                worker_b,
                first_slot_id,
                fingerprint="b" * 64,
                result={"measurement_status": "SUCCESS", "verdict": "MATCHED"},
                sov_record=accepted_record,
                lease_token=judgment_token_b,
            )
            assert checkpointed is not None
            worker_b.commit()

            assert worker_a.scalar(
                select(func.count())
                .select_from(SovRecord)
                .where(SovRecord.raw_response == "late judgment A")
            ) == 0
            authoritative = worker_a.get(
                MeasurementObservationSlot,
                first_slot_id,
                populate_existing=True,
            )
            assert authoritative.sov_record_id == accepted_record.id
            assert authoritative.judgment_input_fingerprint == "b" * 64

            for attempt in range(1, MAX_STAGE_ATTEMPTS + 1):
                claim = claim_slot_stage(
                    worker_a,
                    capped_slot_id,
                    stage="ANSWER",
                    now=base + timedelta(seconds=attempt),
                )
                assert claim is not None
                _, token = claim
                worker_a.commit()  # crash after this point consumes the durable attempt
                checkpoint_answer(
                    worker_a,
                    capped_slot_id,
                    {"measurement_status": "FAILED", "failure_reason": "timeout"},
                    lease_token=token,
                )
                worker_a.commit()
            assert (
                claim_slot_stage(worker_a, capped_slot_id, stage="ANSWER", now=base)
                is None
            )
    finally:
        with Session(pg_engine) as cleanup:
            hospital = cleanup.get(Hospital, hospital_id)
            if hospital is not None:
                cleanup.delete(hospital)
                cleanup.commit()


def test_judgment_retry_reuses_answer_and_settles_each_stage(pg_engine, monkeypatch):
    suffix = uuid.uuid4().hex[:12]
    protocol = {"judge_model": "judge-test", "judge_prompt_fingerprint": "prompt-v1"}
    with Session(pg_engine, expire_on_commit=False) as db:
        hospital = Hospital(name=f"판정재개병원-{suffix}", slug=f"judge-resume-{suffix}")
        db.add(hospital)
        db.flush()
        query = QueryMatrix(
            hospital_id=hospital.id,
            query_text="노원구 병원 추천",
            query_intent="LOCAL",
        )
        run = MeasurementRun(
            hospital_id=hospital.id,
            run_label="judgment resume test",
            status="RUNNING",
            config={"measurement_protocol": protocol},
        )
        db.add_all([query, run])
        db.flush()
        slot = ensure_v0_slots(
            db,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            query_id=query.id,
            platform="chatgpt",
            repeat_count=1,
            protocol=protocol,
        )[0]
        hospital_id = hospital.id
        db.commit()

        fetches = []
        judgments = []
        reservations = []
        settlements = []

        async def fetch_answer(query_text, platform, **kwargs):
            fetches.append((query_text, platform, kwargs["attempt_id"]))
            return {
                "measurement_status": "SUCCESS",
                "raw_response": f"{hospital.name}을 추천합니다.",
                "answer_model": "answer-test",
                "measurement_method": "TEST",
                "search_calls": 1,
                "source_urls": ["https://example.test/clinic"],
                "provider_calls": 1,
            }

        async def judge_answer(_name, _response, **kwargs):
            judgments.append(kwargs["attempt_id"])
            if len(judgments) == 1:
                return {
                    "measurement_status": "FAILED",
                    "failure_reason": "temporary_judge_failure",
                    "provider_calls": 1,
                }
            return {
                "measurement_status": "SUCCESS",
                "verdict": "MATCHED",
                "is_mentioned": True,
                "mention_rank": 1,
                "provider_calls": 1,
            }

        async def reserve(category, count, *, reservation_id):
            reservations.append((category, count, reservation_id))
            return SimpleNamespace(allowed=True, receipt=reservation_id)

        async def settle(receipt, *, consumed_units):
            settlements.append((receipt, consumed_units))

        monkeypatch.setattr(tasks, "fetch_answer", fetch_answer)
        monkeypatch.setattr(tasks, "judge_answer", judge_answer)
        monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
        monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)

        first = tasks._execute_paid_observation_slot(
            db,
            slot=slot,
            hospital=hospital,
            query_text=query.query_text,
            competitors=[],
            protocol=protocol,
        )
        assert first["failure_reason"] == "temporary_judge_failure"

        current = db.get(MeasurementObservationSlot, slot.id, populate_existing=True)
        second = tasks._execute_paid_observation_slot(
            db,
            slot=current,
            hospital=hospital,
            query_text=query.query_text,
            competitors=[],
            protocol=protocol,
        )

        assert second["verdict"] == "MATCHED"
        assert len(fetches) == 1
        assert len(judgments) == 2
        assert [entry[1] for entry in reservations] == [1, 1, 1]
        assert [entry[1] for entry in settlements] == [1, 1, 1]
        assert reservations[0][2].endswith(":answer:1")
        assert reservations[1][2].endswith(":judgment:1")
        assert reservations[2][2].endswith(":judgment:2")
        final = db.get(MeasurementObservationSlot, slot.id, populate_existing=True)
        assert final.answer_attempt_count == 1
        assert final.judgment_attempt_count == 2
        assert final.judgment_status == "CONFIRMED"

    with Session(pg_engine) as cleanup:
        hospital = cleanup.get(Hospital, hospital_id)
        if hospital is not None:
            cleanup.delete(hospital)
            cleanup.commit()


def test_cost_blocks_do_not_consume_stage_attempts_and_zero_judgment_checks_guard(
    pg_engine, monkeypatch
):
    suffix = uuid.uuid4().hex[:12]
    protocol = {"judge_model": "judge-test", "judge_prompt_fingerprint": "prompt-v1"}
    with Session(pg_engine, expire_on_commit=False) as db:
        hospital = Hospital(name=f"비용차단병원-{suffix}", slug=f"cost-block-{suffix}")
        db.add(hospital)
        db.flush()
        query = QueryMatrix(
            hospital_id=hospital.id,
            query_text="노원구 병원 추천",
            query_intent="LOCAL",
        )
        run = MeasurementRun(
            hospital_id=hospital.id,
            run_label="cost block test",
            status="RUNNING",
            config={"measurement_protocol": protocol},
        )
        db.add_all([query, run])
        db.flush()
        slot = ensure_v0_slots(
            db,
            hospital_id=hospital.id,
            measurement_run_id=run.id,
            query_id=query.id,
            platform="chatgpt",
            repeat_count=1,
            protocol=protocol,
        )[0]
        hospital_id = hospital.id
        db.commit()

        fetches = []
        judgments = []
        reservations = []
        settlements = []
        allow_answer = False
        allow_judgment = False

        async def fetch_answer(query_text, platform, **kwargs):
            fetches.append((query_text, platform, kwargs["attempt_id"]))
            return {
                "measurement_status": "SUCCESS",
                # No hospital or competitor mention makes the exact judgment estimate zero.
                "raw_response": "일반적인 선택 기준을 안내합니다.",
                "answer_model": "answer-test",
                "measurement_method": "TEST",
                "provider_calls": 1,
            }

        async def judge_answer(_name, _response, **kwargs):
            judgments.append(kwargs["attempt_id"])
            return {
                "measurement_status": "SUCCESS",
                "verdict": "NOT_MATCHED",
                "is_mentioned": False,
                "provider_calls": 0,
            }

        async def reserve(category, count, *, reservation_id):
            reservations.append((category, count, reservation_id))
            allowed = allow_answer if ":answer:" in reservation_id else allow_judgment
            return SimpleNamespace(
                allowed=allowed,
                receipt=reservation_id if allowed else None,
            )

        async def settle(receipt, *, consumed_units):
            settlements.append((receipt, consumed_units))

        monkeypatch.setattr(tasks, "fetch_answer", fetch_answer)
        monkeypatch.setattr(tasks, "judge_answer", judge_answer)
        monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
        monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)

        blocked_answer = tasks._execute_paid_observation_slot(
            db,
            slot=slot,
            hospital=hospital,
            query_text=query.query_text,
            competitors=[],
            protocol=protocol,
        )
        assert blocked_answer["failure_reason"] == "cost_guard_blocked"
        after_answer_block = db.get(
            MeasurementObservationSlot, slot.id, populate_existing=True
        )
        assert after_answer_block.answer_attempt_count == 0
        assert after_answer_block.lease_token is None
        assert fetches == []

        allow_answer = True
        blocked_judgment = tasks._execute_paid_observation_slot(
            db,
            slot=after_answer_block,
            hospital=hospital,
            query_text=query.query_text,
            competitors=[],
            protocol=protocol,
        )
        assert blocked_judgment["failure_reason"] == "cost_guard_blocked"
        after_judgment_block = db.get(
            MeasurementObservationSlot, slot.id, populate_existing=True
        )
        assert after_judgment_block.answer_attempt_count == 1
        assert after_judgment_block.answer_status == "RECEIVED"
        assert after_judgment_block.judgment_attempt_count == 0
        assert after_judgment_block.lease_token is None
        assert len(fetches) == 1
        assert judgments == []
        assert [entry[1] for entry in reservations] == [1, 1, 0]
        assert [entry[1] for entry in settlements] == [1]

        allow_judgment = True
        completed = tasks._execute_paid_observation_slot(
            db,
            slot=after_judgment_block,
            hospital=hospital,
            query_text=query.query_text,
            competitors=[],
            protocol=protocol,
        )
        assert completed["verdict"] == "NOT_MATCHED"
        assert len(fetches) == 1
        assert len(judgments) == 1
        assert [entry[1] for entry in reservations] == [1, 1, 0, 0]
        assert [entry[1] for entry in settlements] == [1, 0]

    with Session(pg_engine) as cleanup:
        hospital = cleanup.get(Hospital, hospital_id)
        if hospital is not None:
            cleanup.delete(hospital)
            cleanup.commit()
