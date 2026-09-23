"""Postgres proof that a soft-limit chunk boundary neither burns nor re-buys slot stages."""

import uuid
from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import MeasurementObservationSlot
from app.models.sov import MeasurementRun, QueryMatrix
from app.services.measurement_slots import ensure_v0_slots, slot_is_terminal
from app.workers import tasks

PROTOCOL = {"judge_model": "judge-test", "judge_prompt_fingerprint": "prompt-v1"}


def _seed(pg_engine, *, repeat_count: int):
    suffix = uuid.uuid4().hex[:12]
    with Session(pg_engine, expire_on_commit=False) as db:
        hospital = Hospital(name=f"청크이어가기-{suffix}", slug=f"chunk-resume-{suffix}")
        db.add(hospital)
        db.flush()
        query = QueryMatrix(
            hospital_id=hospital.id,
            query_text="노원구 정형외과 추천",
            query_intent="LOCAL",
        )
        run = MeasurementRun(
            hospital_id=hospital.id,
            run_label="chunk continuation test",
            status="RUNNING",
            config={"measurement_protocol": PROTOCOL},
        )
        db.add_all([query, run])
        db.flush()
        slot_ids = [
            slot.id
            for slot in ensure_v0_slots(
                db,
                hospital_id=hospital.id,
                measurement_run_id=run.id,
                query_id=query.id,
                platform="chatgpt",
                repeat_count=repeat_count,
                protocol=PROTOCOL,
            )
        ]
        db.commit()
        return hospital.id, query.query_text, slot_ids


def _cleanup(pg_engine, hospital_id):
    with Session(pg_engine) as db:
        db.execute(
            delete(MeasurementObservationSlot).where(
                MeasurementObservationSlot.hospital_id == hospital_id
            )
        )
        hospital = db.get(Hospital, hospital_id)
        if hospital is not None:
            db.delete(hospital)
        db.commit()


class _Provider:
    def __init__(self, hospital_name: str):
        self.hospital_name = hospital_name
        self.fetches: list[tuple[str, str]] = []
        self.judgments: list[tuple[str, str]] = []
        self.reservations: list[str] = []
        self.settlements: list[str] = []
        self.interrupt_answer_for: set[str] = set()
        self.interrupt_judgment_for: set[str] = set()

    async def fetch_answer(self, _query_text, _platform, **kwargs):
        self.fetches.append((kwargs["item_id"], kwargs["attempt_id"]))
        if kwargs["item_id"] in self.interrupt_answer_for:
            self.interrupt_answer_for.discard(kwargs["item_id"])
            raise SoftTimeLimitExceeded()
        return {
            "measurement_status": "SUCCESS",
            "raw_response": f"{self.hospital_name}을 추천합니다.",
            "answer_model": "answer-test",
            "measurement_method": "TEST",
            "provider_calls": 1,
        }

    async def judge_answer(self, _name, _response, **kwargs):
        self.judgments.append((kwargs["item_id"], kwargs["attempt_id"]))
        return {
            "measurement_status": "SUCCESS",
            "verdict": "MATCHED",
            "is_mentioned": True,
            "mention_rank": 1,
            "provider_calls": 1,
        }

    async def reserve(self, _category, count, *, reservation_id):
        self.reservations.append(reservation_id)
        return SimpleNamespace(allowed=True, receipt=reservation_id)

    async def settle(self, receipt, *, consumed_units):
        self.settlements.append(receipt)
        slot_id = receipt.split(":")[1]
        if ":judgment:" in receipt and slot_id in self.interrupt_judgment_for:
            self.interrupt_judgment_for.discard(slot_id)
            raise SoftTimeLimitExceeded()

    def install(self, monkeypatch):
        monkeypatch.setattr(tasks, "fetch_answer", self.fetch_answer)
        monkeypatch.setattr(tasks, "judge_answer", self.judge_answer)
        monkeypatch.setattr(tasks.cost_guard, "reserve", self.reserve)
        monkeypatch.setattr(tasks.cost_guard, "settle_reservation", self.settle)


def _execute(db, hospital, query_text, slot_id):
    slot = db.get(MeasurementObservationSlot, slot_id, populate_existing=True)
    return tasks._execute_paid_observation_slot(
        db,
        slot=slot,
        hospital=hospital,
        query_text=query_text,
        competitors=[],
        protocol=PROTOCOL,
    )


def test_soft_limit_refunds_unsettled_answer_and_resume_skips_completed_slots(
    pg_engine, monkeypatch
):
    hospital_id, query_text, (done_id, cut_id) = _seed(pg_engine, repeat_count=2)
    try:
        with Session(pg_engine, expire_on_commit=False) as db:
            hospital = db.get(Hospital, hospital_id)
            provider = _Provider(hospital.name)
            provider.install(monkeypatch)
            provider.interrupt_answer_for = {str(cut_id)}

            # First chunk: one slot completes, the next is cut by the soft limit.
            _execute(db, hospital, query_text, done_id)
            with pytest.raises(SoftTimeLimitExceeded):
                _execute(db, hospital, query_text, cut_id)

            cut = db.get(MeasurementObservationSlot, cut_id, populate_existing=True)
            assert cut.answer_attempt_count == 0
            assert cut.lease_token is None
            assert cut.lease_expires_at is None
            assert cut.answer_status != "RECEIVED"

            # Resumed chunk walks every slot of the pending cell again.
            for slot_id in (done_id, cut_id):
                result = _execute(db, hospital, query_text, slot_id)
                assert result["verdict"] == "MATCHED"

            slots = [
                db.get(MeasurementObservationSlot, slot_id, populate_existing=True)
                for slot_id in (done_id, cut_id)
            ]
            assert all(slot_is_terminal(slot) for slot in slots)
            assert [slot.answer_attempt_count for slot in slots] == [1, 1]
            assert [slot.judgment_attempt_count for slot in slots] == [1, 1]

        # The completed slot was bought once; the cut slot reused attempt 1.
        assert provider.fetches == [
            (str(done_id), "1"),
            (str(cut_id), "1"),
            (str(cut_id), "1"),
        ]
        assert provider.judgments == [(str(done_id), "1"), (str(cut_id), "1")]
        assert provider.reservations.count(f"measurement:{done_id}:answer:1") == 1
        assert provider.reservations.count(f"measurement:{cut_id}:answer:1") == 2
        assert f"measurement:{cut_id}:answer:2" not in provider.reservations
    finally:
        _cleanup(pg_engine, hospital_id)


def test_soft_limit_after_settlement_keeps_paid_attempt_but_releases_lease(
    pg_engine, monkeypatch
):
    hospital_id, query_text, (slot_id,) = _seed(pg_engine, repeat_count=1)
    try:
        with Session(pg_engine, expire_on_commit=False) as db:
            hospital = db.get(Hospital, hospital_id)
            provider = _Provider(hospital.name)
            provider.install(monkeypatch)
            provider.interrupt_judgment_for = {str(slot_id)}

            with pytest.raises(SoftTimeLimitExceeded):
                _execute(db, hospital, query_text, slot_id)

            slot = db.get(MeasurementObservationSlot, slot_id, populate_existing=True)
            assert slot.answer_status == "RECEIVED"
            assert slot.answer_attempt_count == 1
            assert slot.judgment_attempt_count == 1
            assert slot.judgment_status != "CONFIRMED"
            assert slot.lease_token is None

            result = _execute(db, hospital, query_text, slot_id)
            assert result["verdict"] == "MATCHED"

        assert provider.fetches == [(str(slot_id), "1")]
        assert provider.judgments == [(str(slot_id), "1"), (str(slot_id), "2")]
        assert f"measurement:{slot_id}:judgment:2" in provider.reservations
    finally:
        _cleanup(pg_engine, hospital_id)
