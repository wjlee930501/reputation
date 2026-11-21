"""측정 키워드 30개 — 실제 Postgres에서 세트 → 측정 spec → 측정표 → 슬롯 → 동시 답변 → 판정 → 집계.

과제 셸(`run_sov_for_hospital`의 실행 기록·디스패치 인증)만 빼고 12/1 새벽 창이 지나는 실제 함수를 차례로
부른다. 공급자 호출과 비용 가드는 가짜다. 확인하는 것:
- 30개(병원명 5 · 고정 15 · 교체 10) × 반복 2 = 60칸이 측정표·슬롯으로 얼고,
- 답변은 슬롯마다 한 번만 사고(동시 선구매 뒤 차례 실행이 다시 사지 않는다), 예약은 하나에 한 번 정산되고,
- 측정표의 `query_sets`가 집계 payload까지 살아남고 대표 노출률에서 병원명 질문이 빠진다.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import MonthlyMeasurementManifest
from app.models.sov import AIQueryTarget, AIQueryVariant, MeasurementRun
from app.services import sov_engine
from app.services.measurement_slots import ensure_monthly_slots
from app.services.monthly_manifest import freeze_dispatch_manifest
from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_repository import load_monthly_sov_manifest
from app.services.sov_tracking_set import (
    tracking_set_basis_size,
    tracking_set_fingerprint,
    tracking_set_members,
)
from app.workers import tasks
from tests.db_env import require_db_url

HOSPITAL_NAME = "새벽측정검증내과의원"
REPEATS = 2


def _target(hospital_id: uuid.UUID, text: str, group: str, role: str) -> AIQueryTarget:
    target = AIQueryTarget(
        hospital_id=hospital_id,
        name=text,
        target_intent="추천 탐색",
        region_terms=[],
        decision_criteria=[],
        platforms=["CHATGPT"],
        competitor_names=[],
        priority="NORMAL",
        status="ACTIVE",
        in_tracking_set=True,
        question_group=group,
        set_role=role,
    )
    target.variants = [AIQueryVariant(query_text=text, platform="CHATGPT", language="ko", is_active=True)]
    return target


def test_set30_dawn_path_buys_each_answer_once_and_freezes_groups(monkeypatch) -> None:
    engine = create_engine(require_db_url("TASK22_DATABASE_URL"), pool_pre_ping=True)
    hospital_id = uuid.uuid4()
    with Session(engine, expire_on_commit=False) as db:
        hospital = Hospital(
            id=hospital_id,
            name=HOSPITAL_NAME,
            slug=f"dawn-{hospital_id.hex[:12]}",
            region=["가상구"],
            address="가상시 가상구 새벽로 12",
        )
        db.add(hospital)
        texts = (
            [(f"{HOSPITAL_NAME} 질문 {i}", "A", "FIXED") for i in range(5)]
            + [(f"가상구 내과 고정 질문 {i}", "B", "FIXED") for i in range(15)]
            + [(f"가상구 야간 내과 교체 질문 {i}", "D", "ROTATING") for i in range(10)]
        )
        db.add_all([_target(hospital_id, text, group, role) for text, group, role in texts])
        db.commit()

        monkeypatch.setattr(tasks.settings, "OPENROUTER_API_KEY", "")
        targets = db.scalars(select(AIQueryTarget).where(AIQueryTarget.hospital_id == hospital_id)).all()
        members = tracking_set_members(targets)
        assert len(members) == 30
        specs, _ = tasks._build_measurement_specs(
            db=db,
            hospital=hospital,
            query_targets=members,
            fallback_queries=[],
            high_priority_cap=-1,
            total_spec_cap=-1,
            measurement_mode="monthly",
        )
        assert len(specs) == 30
        protocol_kwargs = {
            "measurement_window": "month_start_dawn",
            "tracking_set_fingerprint": tracking_set_fingerprint(members),
            "tracking_set_size": tracking_set_basis_size(members),
        }
        manifest = freeze_dispatch_manifest(
            db,
            hospital_id,
            2099,
            11,
            specs,
            gemini_configured=False,
            measurement_protocol_kwargs=protocol_kwargs,
            judgment_identity=tasks._judgment_identity(hospital, hospital.name, None).material(),
        )
        protocol = sov_engine.measurement_protocol(**protocol_kwargs)
        run = MeasurementRun(
            hospital_id=hospital_id,
            run_label="dawn-test",
            measurement_method="TEST",
            status="RUNNING",
            query_count=0,
            success_count=0,
            failure_count=0,
            config={"measurement_protocol": protocol},
        )
        db.add(run)
        db.flush()
        cells = {cell.query_key: cell for cell in manifest.cells}
        slots_by_cell = {
            cell.id: ensure_monthly_slots(
                db,
                cell=cell,
                hospital_id=hospital_id,
                measurement_run_id=run.id,
                repeat_count=REPEATS,
                protocol=protocol,
            )
            for cell in cells.values()
        }
        db.commit()
        assert sum(len(slots) for slots in slots_by_cell.values()) == 60
        assert manifest.platform_provenance["measurement_protocol"]["tracking_set_size"] == 20

        fetched: Counter[str] = Counter()
        reserved: Counter[str] = Counter()
        settled: Counter[str] = Counter()

        async def fetch_answer(query_text, platform, **kwargs):
            fetched[str(kwargs["item_id"])] += 1
            await asyncio.sleep(0)
            mentioned = HOSPITAL_NAME in query_text or query_text.endswith("0")
            body = f"{HOSPITAL_NAME}는 새벽로 12에 있습니다." if mentioned else "가상구에는 여러 내과가 있습니다."
            return {
                "measurement_status": "SUCCESS",
                "raw_response": body,
                "answer_model": "fake-luna",
                "measurement_method": "TEST",
                "provider_calls": 1,
            }

        async def reserve(category, *, count, reservation_id):
            reserved[reservation_id] += 1
            return SimpleNamespace(allowed=True, receipt=reservation_id)

        async def settle(receipt, *, consumed_units):
            settled[receipt] += 1

        monkeypatch.setattr(tasks, "fetch_answer", fetch_answer)
        monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
        monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)

        received = tasks._prefetch_monthly_answers(
            db,
            slots_by_cell=slots_by_cell,
            query_text_by_cell={cell.id: cell.query_text for cell in cells.values()},
            hospital=hospital,
            protocol=protocol,
            deadline=None,
            concurrency=6,
        )
        assert received == 60

        identity = tasks._frozen_manifest_identity(manifest, hospital)
        for cell in cells.values():
            for slot in slots_by_cell[cell.id]:
                result = tasks._execute_paid_observation_slot(
                    db,
                    slot=slot,
                    hospital=hospital,
                    identity=identity,
                    query_text=cell.query_text,
                    competitors=[],
                    protocol=protocol,
                    target_id=cell.query_target_id,
                    variant_id=cell.query_variant_id,
                    monthly_cell=cell,
                    deadline=None,
                )
                assert result.get("measurement_status") == "SUCCESS", result
        db.commit()

        # 슬롯마다 답변은 한 번만 샀고, 예약은 하나에 한 번 정산됐다.
        assert len(fetched) == 60 and set(fetched.values()) == {1}
        answer_reservations = [rid for rid in reserved if ":answer:" in rid]
        assert len(answer_reservations) == 60 and set(reserved.values()) == {1}
        assert all(settled[rid] == 1 for rid in reserved)

        db.expire_all()
        manifest = db.get(MonthlyMeasurementManifest, manifest.id)
        loaded = load_monthly_sov_manifest(db, manifest)
        summary = build_monthly_sov(loaded.cells, tuple(manifest.configured_platforms))
        payload = summary.to_payload()
        queries = payload["queries"]
        groups = Counter(query.get("question_group") for query in queries)
        assert groups == {"A": 5, "B": 15, "D": 10}
        assert Counter(query["query_intent"] for query in queries) == {"BRAND": 5, "LOCAL": 25}
        assert sum(query.get("set_role") == "ROTATING" for query in queries) == 10
        # 대표 노출률: LOCAL 25문항 × 2반복 = 50답변 중 '…0'으로 끝나는 B 2개(0·10)·D 1개 × 2 = 6건.
        # 병원명 질문 10답변(전부 언급)은 들어가지 않는다.
        assert summary.attempts_used == 50
        assert summary.mentioned_attempts == 6
        assert summary.sov_pct == 12.0

    engine.dispose()
