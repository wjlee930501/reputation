"""측정 키워드 30개 세트 계약 (docs/plans/2026-11-21-measurement-30-design.md §2·§3).

- A그룹(병원명 질문)은 BRAND로 얼려 대표 노출률(LOCAL)에서 빠진다.
- 측정 기준 지문은 고정 질문만으로 만든다 — 교체 질문이 바뀌어도 비교가 이어진다.
- 월간·주간 배치의 세트 등록이 사람이 정한 역할을 덮어쓰지 않는다.
- 지난달 비교의 맞춘 칸은 두 달 모두 고정인 칸만이다.
- 새벽 창에 이미 얼린 측정표는 그대로 잰다(고친 세트는 다음 달부터).
- 한 페이지의 답변 호출은 동시에 보내되 칸마다의 claim·예약·체크포인트 계약은 그대로다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from app.services import measurement_set, sov_engine, sov_tracking_set
from app.services.measurement_selection import build_measurement_specs
from app.services.monthly_manifest import (
    ManifestCellSpec,
    _query_sets,
    freeze_monthly_manifest,
    frozen_manifest_specs,
)
from app.services.monthly_sov import _comparable_cell, build_monthly_sov
from app.services.monthly_sov_repository import _intent_snapshot, _query_set_snapshot
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput


def _target(text: str, *, group: str | None, role: str | None, status: str = "ACTIVE"):
    query = SimpleNamespace(id=uuid.uuid4(), query_intent="LOCAL", query_text=text)
    variant = SimpleNamespace(
        id=uuid.uuid4(),
        query_matrix_id=query.id,
        query_matrix=query,
        query_text=text,
        platform="CHATGPT",
        is_active=True,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=text,
        status=status,
        in_tracking_set=False,
        question_group=group,
        set_role=role,
        priority="NORMAL",
        target_month=None,
        variants=[variant],
    )


def _set30(*, fixed_b: int = 15, rotating: int = 10):
    brand = [_target(f"행복의원 질문 {i}", group="A", role="FIXED") for i in range(5)]
    fixed = [_target(f"강남 내과 추천 {i}", group="B", role="FIXED") for i in range(fixed_b)]
    rotating_rows = [
        _target(f"강남 야간 진료 내과 {i}", group="D", role="ROTATING") for i in range(rotating)
    ]
    return brand, fixed, rotating_rows


# ── 그룹·역할 규칙 ────────────────────────────────────────────────


def test_group_a_measures_as_brand_and_b_to_d_as_local():
    brand = _target("행복의원 어때?", group="A", role="FIXED")
    local = _target("가슴이 두근거리는데 초기 증상이 뭔지 알려줘", group="C", role="ROTATING")
    legacy = _target("강남 내과 추천해줘", group=None, role=None)

    assert measurement_set.set_measurement_intent(brand) == sov_engine.QUERY_INTENT_BRAND
    # 옛 정보형 표지가 들어 있어도 30개 세트의 C그룹은 LOCAL이다(문구 분류를 쓰지 않는다).
    assert measurement_set.set_measurement_intent(local) == sov_engine.QUERY_INTENT_LOCAL
    assert measurement_set.set_measurement_intent(legacy) is None
    assert sov_engine.QUERY_INTENT_BRAND not in sov_engine.MENTION_RATE_INTENTS


def test_group_a_is_not_a_content_topic():
    assert not measurement_set.content_eligible(_target("행복의원 어때?", group="A", role="FIXED"))
    assert measurement_set.content_eligible(_target("강남 내과 추천", group="B", role="FIXED"))
    assert measurement_set.content_eligible(_target("강남 내과 추천", group=None, role=None))


def test_fingerprint_uses_fixed_questions_only():
    brand, fixed, rotating = _set30()
    targets = [*brand, *fixed, *rotating]
    swapped = [
        *brand,
        *fixed,
        *[_target(f"주차 되는 강남 내과 {i}", group="D", role="ROTATING") for i in range(10)],
    ]

    fingerprint, size = measurement_set.set_fingerprint(targets)

    assert size == 20
    # 교체 10개를 바꿔도 지난달 비교 기준은 같다.
    assert measurement_set.set_fingerprint(swapped) == (fingerprint, 20)
    # 고정 질문 문구가 바뀌면 다르다.
    fixed[0].variants[0].query_text = "강남 내과 잘하는 곳"
    assert measurement_set.set_fingerprint(targets)[0] != fingerprint
    # 같은 문구를 다른 그룹으로 옮기면(분모가 바뀐다) 다르다.
    fixed[0].variants[0].query_text = "강남 내과 추천 0"
    fixed[0].question_group = "C"
    assert measurement_set.set_fingerprint(targets)[0] != fingerprint


def test_set_problems_bound_the_shape():
    brand, fixed, rotating = _set30()
    assert measurement_set.set_problems([*brand, *fixed, *rotating]) == []
    assert measurement_set.set_problems([*brand, *fixed[:9], *rotating])  # 고정 14개
    assert measurement_set.set_problems([*brand, *fixed, *rotating, *rotating[:1]]) == [
        "rotating questions 11, requires at most 10",
        "set size 31, requires at most 30",
    ]
    only_brand = [_target(f"행복의원 {i}", group="A", role="FIXED") for i in range(15)]
    assert "no fixed B~D question for the headline rate" in measurement_set.set_problems(
        only_brand
    )


# ── 추적 세트 등록 ────────────────────────────────────────────────


class _FlushDB:
    flushes = 0

    def flush(self):
        self.flushes += 1


def test_curated_set_registration_never_reselects_roles(monkeypatch):
    brand, fixed, rotating = _set30()
    outsider = _target("측정 이력 많은 옛 질문", group=None, role=None)
    outsider.in_tracking_set = True
    targets = [*brand, *fixed, *rotating, outsider]
    monkeypatch.setattr(sov_tracking_set, "_load_targets", lambda _db, _hid: targets)
    monkeypatch.setattr(
        sov_tracking_set,
        "propose_tracking_set",
        lambda *_a, **_k: pytest.fail("30개 방식은 자동 제안을 부르지 않는다"),
    )

    result = sov_tracking_set.register_tracking_set(_FlushDB(), uuid.uuid4(), n=15)

    assert result["valid"] is True
    assert result["curated"] is True
    assert result["registered_size"] == 30
    assert all(target.in_tracking_set for target in (*brand, *fixed, *rotating))
    assert outsider.in_tracking_set is False
    assert {t.id for t in sov_tracking_set.tracking_set_members(targets)} == {
        t.id for t in (*brand, *fixed, *rotating)
    }
    assert sov_tracking_set.tracking_set_basis_size(targets) == 20
    assert sov_tracking_set.tracking_set_fingerprint(targets) == (
        measurement_set.set_fingerprint(targets)[0]
    )


def test_invalid_curated_set_reports_a_reason(monkeypatch):
    brand, fixed, _rotating = _set30(fixed_b=5)
    targets = [*brand, *fixed]
    monkeypatch.setattr(sov_tracking_set, "_load_targets", lambda _db, _hid: targets)

    result = sov_tracking_set.register_tracking_set(_FlushDB(), uuid.uuid4())

    assert result["valid"] is False
    assert result["registered_size"] == 0
    assert result["reason"] == "measurement set invalid: fixed questions 10, requires 15..20"


def test_paused_role_rows_do_not_switch_a_hospital_to_the_curated_set():
    legacy = [_target(f"강남 병원 질문 {i}", group=None, role=None) for i in range(12)]
    for target in legacy:
        target.in_tracking_set = True
    paused = _target("행복의원 어때?", group="A", role="FIXED", status="PAUSED")

    assert not measurement_set.uses_curated_set([*legacy, paused])
    assert sov_tracking_set.tracking_set_members([*legacy, paused]) == legacy


# ── 측정 spec·측정표 ─────────────────────────────────────────────


def test_monthly_specs_carry_brand_intent_and_query_set():
    brand = _target("행복의원 어때?", group="A", role="FIXED")
    local = _target("강남 야간 진료 내과", group="D", role="ROTATING")

    specs, _trimmed = build_measurement_specs(
        resolve_variant_query=lambda variant: variant.query_matrix,
        gemini_enabled=False,
        query_targets=[brand, local],
        fallback_queries=[],
        high_priority_cap=-1,
        total_spec_cap=-1,
        measurement_mode="monthly",
    )

    by_text = {spec["query_text"]: spec for spec in specs}
    assert by_text["행복의원 어때?"]["query_intent"] == "BRAND"
    assert by_text["행복의원 어때?"]["query_set"] == {"group": "A", "role": "FIXED"}
    assert by_text["강남 야간 진료 내과"]["query_intent"] == "LOCAL"
    assert by_text["강남 야간 진료 내과"]["query_set"] == {"group": "D", "role": "ROTATING"}


class _ManifestSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def _cell_spec(key: str, intent: str, query_set: dict | None) -> ManifestCellSpec:
    return ManifestCellSpec(
        query_key=key,
        query_text=key,
        platform="chatgpt",
        query_matrix_id=uuid.uuid4(),
        query_target_id=uuid.uuid4(),
        query_variant_id=uuid.uuid4(),
        query_intent=intent,
        query_set=query_set,
    )


def test_manifest_freezes_query_sets_and_rebuilds_specs():
    specs = [
        _cell_spec("variant:a", "BRAND", {"group": "A", "role": "FIXED"}),
        _cell_spec("variant:b", "LOCAL", {"group": "B", "role": "ROTATING"}),
        _cell_spec("variant:legacy", "LOCAL", None),
    ]
    manifest = freeze_monthly_manifest(
        _ManifestSession(),
        uuid.uuid4(),
        2026,
        11,
        specs,
        gemini_configured=False,
        measurement_protocol_kwargs={
            "measurement_window": "month_start_dawn",
            "tracking_set_fingerprint": "f" * 16,
            "tracking_set_size": 20,
        },
    )

    provenance = manifest.platform_provenance
    assert provenance["query_sets"] == {
        "variant:a": {"group": "A", "role": "FIXED"},
        "variant:b": {"group": "B", "role": "ROTATING"},
    }
    assert provenance["query_intents"]["variant:a"] == "BRAND"
    rebuilt = {spec["query_text"]: spec for spec in frozen_manifest_specs(manifest)}
    assert rebuilt["variant:a"]["query_intent"] == "BRAND"
    assert rebuilt["variant:a"]["query_set"] == {"group": "A", "role": "FIXED"}
    assert rebuilt["variant:legacy"]["query_set"] is None
    assert _query_sets(specs[2:]) == {}


def test_repository_reads_brand_and_query_sets_from_the_manifest():
    manifest = SimpleNamespace(
        platform_provenance={
            "query_intents": {"variant:a": "BRAND", "variant:b": "LOCAL"},
            "query_sets": {"variant:a": {"group": "A", "role": "FIXED"}, "variant:x": "bad"},
        }
    )

    assert _intent_snapshot(manifest, "variant:a", "LOCAL") == ("BRAND", "FROZEN")
    assert _query_set_snapshot(manifest, "variant:a") == ("A", "FIXED")
    assert _query_set_snapshot(manifest, "variant:x") == (None, None)
    assert _query_set_snapshot(manifest, "variant:b") == (None, None)
    assert _query_set_snapshot(SimpleNamespace(platform_provenance=None), "variant:a") == (
        None,
        None,
    )


# ── 월간 노출률·비교 ─────────────────────────────────────────────


def _metric_cell(key: str, *, intent: str, role: str | None, group: str | None, mentioned: int):
    attempts = tuple(
        CellAttempt(
            record_id=uuid.uuid4(),
            measured_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
            succeeded=True,
            is_mentioned=index < mentioned,
            answer_model="gpt-luna",
        )
        for index in range(5)
    )
    return ManifestCellInput(
        query_key=key,
        query_text=key,
        platform="chatgpt",
        query_intent=intent,
        state="SUCCESS",
        query_matrix_id=None,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=attempts,
        question_group=group,
        set_role=role,
    )


def test_brand_cells_stay_out_of_the_headline_and_carry_group_payload():
    cells = (
        _metric_cell("variant:a", intent="BRAND", role="FIXED", group="A", mentioned=5),
        _metric_cell("variant:b", intent="LOCAL", role="FIXED", group="B", mentioned=1),
        _metric_cell("variant:d", intent="LOCAL", role="ROTATING", group="D", mentioned=0),
    )

    summary = build_monthly_sov(cells, ("chatgpt",))
    payload = summary.to_payload()

    # A(5/5)는 빠지고 B·D만: 1/10 = 10%.
    assert summary.sov_pct == 10.0
    queries = {query["query_key"]: query for query in payload["queries"]}
    assert queries["variant:a"]["query_intent"] == "BRAND"
    assert queries["variant:a"]["query_intent_label"] == "병원명 질문"
    assert queries["variant:a"]["question_group"] == "A"
    assert queries["variant:d"]["set_role"] == "ROTATING"
    cells_payload = {cell["query_key"]: cell for cell in payload["cells"]}
    assert cells_payload["variant:b"]["question_group"] == "B"


def test_rotating_cells_never_join_the_month_over_month_cohort():
    assert _comparable_cell(
        _metric_cell("variant:b", intent="LOCAL", role="FIXED", group="B", mentioned=1)
    )
    assert _comparable_cell(
        _metric_cell("query:legacy", intent="LOCAL", role=None, group=None, mentioned=1)
    )
    assert not _comparable_cell(
        _metric_cell("variant:d", intent="LOCAL", role="ROTATING", group="D", mentioned=1)
    )
    assert not _comparable_cell(
        _metric_cell("variant:a", intent="BRAND", role="FIXED", group="A", mentioned=1)
    )


def test_month_over_month_compares_fixed_cells_only():
    protocol = {
        **sov_engine.measurement_protocol(
            measurement_window="month_start_dawn",
            tracking_set_fingerprint="f" * 16,
            tracking_set_size=20,
        )
    }
    prior = (
        _metric_cell("variant:b", intent="LOCAL", role="FIXED", group="B", mentioned=1),
        _metric_cell("variant:r", intent="LOCAL", role="ROTATING", group="D", mentioned=5),
    )
    current = (
        _metric_cell("variant:b", intent="LOCAL", role="FIXED", group="B", mentioned=2),
        # 같은 교체 질문이 두 달 이어져도 비교 표본이 아니다.
        _metric_cell("variant:r", intent="LOCAL", role="ROTATING", group="D", mentioned=0),
        _metric_cell("variant:a", intent="BRAND", role="FIXED", group="A", mentioned=5),
    )

    summary = build_monthly_sov(
        current,
        ("chatgpt",),
        prior_cells=prior,
        prior_platforms=("chatgpt",),
        current_protocol=protocol,
        prior_protocol=protocol,
    )

    assert summary.comparison.status == "COMPARABLE"
    assert summary.comparison.matched_cell_count == 1
    assert summary.comparison.current_sov_pct == 40.0
    assert summary.comparison.prior_sov_pct == 20.0


# ── 답변 동시 선구매 ─────────────────────────────────────────────


class _PrefetchDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _slot():
    return SimpleNamespace(
        id=uuid.uuid4(),
        platform="chatgpt",
        scope="MONTHLY",
        measurement_run_id=uuid.uuid4(),
        answer_status="PENDING",
        answer_attempt_count=0,
        judgment_status="PENDING",
        judgment_attempt_count=0,
    )


def _prefetch_shell(monkeypatch, *, allowed=lambda _rid: True, answer=None):
    from app.workers import tasks

    events = SimpleNamespace(claims=[], reserves=[], settled=[], checkpoints=[], released=[])
    in_flight = SimpleNamespace(now=0, peak=0)

    def claim(_db, slot_id, *, stage, deadline):
        events.claims.append(slot_id)
        return SimpleNamespace(
            id=slot_id,
            platform="chatgpt",
            scope="MONTHLY",
            measurement_run_id=uuid.uuid4(),
            answer_attempt_count=1,
        ), uuid.uuid4()

    async def reserve(category, *, count, reservation_id):
        events.reserves.append(reservation_id)
        return SimpleNamespace(allowed=allowed(reservation_id), receipt=reservation_id)

    async def settle(receipt, *, consumed_units):
        events.settled.append((receipt, consumed_units))

    async def fetch(query_text, platform, **_kw):
        import asyncio

        in_flight.now += 1
        in_flight.peak = max(in_flight.peak, in_flight.now)
        await asyncio.sleep(0.01)
        in_flight.now -= 1
        if answer is not None:
            return answer(query_text)
        return {"measurement_status": "SUCCESS", "raw_response": f"{query_text} 답", "provider_calls": 1}

    def checkpoint(_db, slot_id, result, *, lease_token, deadline):
        events.checkpoints.append((slot_id, result.get("measurement_status")))
        return SimpleNamespace(answer_status="RECEIVED" if result.get("raw_response") else "FAILED")

    def release(_db, slot_id, *, stage, lease_token, refund_attempt):
        events.released.append((slot_id, refund_attempt))
        return True

    monkeypatch.setattr(tasks, "claim_slot_stage", claim)
    monkeypatch.setattr(tasks.cost_guard, "reserve", reserve)
    monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(tasks, "fetch_answer", fetch)
    monkeypatch.setattr(tasks, "checkpoint_answer", checkpoint)
    monkeypatch.setattr(tasks, "release_interrupted_stage", release)
    return tasks, events, in_flight


def _run_prefetch(tasks, slots_by_cell, *, concurrency=3, deadline=None):
    return tasks._prefetch_monthly_answers(
        _PrefetchDB(),
        slots_by_cell=slots_by_cell,
        query_text_by_cell={cell_id: f"질문 {cell_id}" for cell_id in slots_by_cell},
        hospital=SimpleNamespace(id=uuid.uuid4()),
        protocol={"openai_model_query": "gpt-luna", "gemini_model": "gemini"},
        deadline=deadline,
        concurrency=concurrency,
    )


def test_prefetch_buys_page_answers_concurrently_with_per_slot_contract(monkeypatch):
    tasks, events, in_flight = _prefetch_shell(monkeypatch)
    cells = {uuid.uuid4(): [_slot(), _slot()] for _ in range(4)}

    received = _run_prefetch(tasks, cells, concurrency=3)

    assert received == 8
    assert len(events.claims) == 8
    assert len(set(events.reserves)) == 8
    assert all(rid.endswith(":answer:1") for rid in events.reserves)
    assert sorted(units for _receipt, units in events.settled) == [1] * 8
    assert len(events.checkpoints) == 8
    assert 1 < in_flight.peak <= 3


def test_prefetch_stops_at_the_cost_guard_and_leaves_the_rest_to_the_serial_loop(monkeypatch):
    seen = []

    def allowed(reservation_id):
        seen.append(reservation_id)
        return len(seen) <= 2

    tasks, events, _ = _prefetch_shell(monkeypatch, allowed=allowed)
    cells = {uuid.uuid4(): [_slot(), _slot(), _slot()]}

    received = _run_prefetch(tasks, cells)

    assert received == 2
    assert len(events.checkpoints) == 2


def test_prefetch_settles_provider_exceptions_as_spent(monkeypatch):
    def answer(_text):
        raise RuntimeError("provider timeout")

    tasks, events, _ = _prefetch_shell(monkeypatch, answer=answer)
    cells = {uuid.uuid4(): [_slot()]}

    received = _run_prefetch(tasks, cells)

    assert received == 0
    assert events.settled[0][1] == 1
    assert events.checkpoints[0][1] == "FAILED"


def test_prefetch_soft_limit_hands_back_every_unsettled_claim(monkeypatch):
    def answer(_text):
        raise SoftTimeLimitExceeded()

    tasks, events, _ = _prefetch_shell(monkeypatch, answer=answer)
    cells = {uuid.uuid4(): [_slot(), _slot()]}

    with pytest.raises(SoftTimeLimitExceeded):
        _run_prefetch(tasks, cells)

    assert len(events.released) == 2
    assert all(refund for _slot_id, refund in events.released)


def test_prefetch_is_off_when_concurrency_is_one(monkeypatch):
    tasks, events, _ = _prefetch_shell(monkeypatch)

    assert _run_prefetch(tasks, {uuid.uuid4(): [_slot()]}, concurrency=1) == 0
    assert events.claims == []


def test_prefetch_rechecks_the_dawn_deadline_right_before_each_call(monkeypatch):
    """대기열에서 차례가 왔을 때 07:00이 지났으면 호출하지 않는다 — 예약 0 정산·시도 환급."""

    from app.workers import tasks as tasks_module

    calls = SimpleNamespace(deadline_checks=0)
    real = tasks_module._deadline_reached

    def deadline_reached(deadline, moment):
        if deadline is None:
            return False
        calls.deadline_checks += 1
        # claim 단계(슬롯 2개)까지는 열려 있고, 호출 직전 확인부터는 지났다.
        return calls.deadline_checks > 2

    tasks, events, _ = _prefetch_shell(monkeypatch)
    monkeypatch.setattr(tasks, "_deadline_reached", deadline_reached)
    cells = {uuid.uuid4(): [_slot(), _slot()]}

    received = _run_prefetch(
        tasks, cells, deadline=datetime(2026, 12, 1, 7, 0, tzinfo=timezone.utc)
    )

    assert received == 0
    assert events.checkpoints == []
    assert sorted(units for _receipt, units in events.settled) == [0, 0]
    assert len(events.released) == 2
    assert all(refund for _slot_id, refund in events.released)
    assert real is not None


def test_prefetch_checkpoints_each_answer_as_it_lands(monkeypatch):
    """느린 답 하나가 먼저 받은 답의 체크포인트를 붙잡지 않는다."""

    import asyncio

    order = []

    async def fetch(query_text, platform, **_kw):
        slow = query_text.endswith("slow")
        await asyncio.sleep(0.05 if slow else 0.0)
        return {"measurement_status": "SUCCESS", "raw_response": "답", "provider_calls": 1}

    tasks, events, _ = _prefetch_shell(monkeypatch)
    monkeypatch.setattr(tasks, "fetch_answer", fetch)
    original = tasks.checkpoint_answer

    def checkpoint(db, slot_id, result, **kwargs):
        order.append(slot_id)
        return original(db, slot_id, result, **kwargs)

    monkeypatch.setattr(tasks, "checkpoint_answer", checkpoint)
    slow_cell, fast_cell = uuid.uuid4(), uuid.uuid4()
    slots = {slow_cell: [_slot()], fast_cell: [_slot()]}

    received = tasks._prefetch_monthly_answers(
        _PrefetchDB(),
        slots_by_cell=slots,
        query_text_by_cell={slow_cell: "질문 slow", fast_cell: "질문 fast"},
        hospital=SimpleNamespace(id=uuid.uuid4()),
        protocol={},
        deadline=None,
        concurrency=2,
    )

    assert received == 2
    # 빠른 답(두 번째 claim)이 먼저 저장된다.
    assert order == [events.claims[1], events.claims[0]]


def test_prefetch_never_refunds_a_settling_claim(monkeypatch):
    """정산을 시작한 칸은 soft limit에도 시도 번호를 돌려주지 않는다(같은 예약 번호 재사용 방지)."""

    tasks, events, _ = _prefetch_shell(monkeypatch)

    async def settle(receipt, *, consumed_units):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks.cost_guard, "settle_reservation", settle)
    cells = {uuid.uuid4(): [_slot()]}

    with pytest.raises(SoftTimeLimitExceeded):
        _run_prefetch(tasks, cells)

    assert events.released == [(events.claims[0], False)]


def test_measured_members_cap_the_set_at_thirty_fixed_first():
    brand, fixed, rotating = _set30()
    extra = _target("강남 주차 되는 내과 추가", group="D", role="ROTATING")
    members = measurement_set.measured_members([*rotating, extra, *fixed, *brand])

    assert len(members) == 30
    assert members[:20] == sorted(
        [*brand, *fixed], key=lambda t: (t.question_group, t.name, str(t.id))
    )
    assert sum(member.set_role == "ROTATING" for member in members) == 10
    # 교체 11개 중 그룹·문구 순으로 뒤에 오는 하나가 빠진다.
    assert members[-1].set_role == "ROTATING"


def test_frozen_dawn_protocol_is_reused_for_continuations():
    from app.workers import tasks

    manifest = SimpleNamespace(
        platform_provenance={
            "measurement_protocol": {
                "measurement_window": "month_start_dawn",
                "tracking_set_fingerprint": "abcd",
                "tracking_set_size": 20,
                "policy_version": "x",
            }
        }
    )

    assert tasks._frozen_protocol_kwargs(manifest) == {
        "measurement_window": "month_start_dawn",
        "tracking_set_fingerprint": "abcd",
        "tracking_set_size": 20,
    }
    assert tasks._frozen_protocol_kwargs(None) is None


def test_prefetch_db_error_rolls_back_at_once_and_leaves_no_task_behind(monkeypatch):
    """한 칸의 저장 오류는 곧바로 되돌리고 올린다 — 재사용 루프에 남은 호출이 없어야 한다."""

    import asyncio

    tasks, events, _ = _prefetch_shell(monkeypatch)
    db = _PrefetchDB()
    calls = SimpleNamespace(n=0)

    def checkpoint(_db, slot_id, result, *, lease_token, deadline):
        calls.n += 1
        if calls.n == 1:
            raise RuntimeError("db down")
        return SimpleNamespace(answer_status="RECEIVED")

    monkeypatch.setattr(tasks, "checkpoint_answer", checkpoint)
    cells = {uuid.uuid4(): [_slot(), _slot(), _slot()]}

    with pytest.raises(RuntimeError, match="db down"):
        tasks._prefetch_monthly_answers(
            db,
            slots_by_cell=cells,
            query_text_by_cell={cell_id: "질문" for cell_id in cells},
            hospital=SimpleNamespace(id=uuid.uuid4()),
            protocol={},
            deadline=None,
            concurrency=1 + 1,
        )

    assert db.rollbacks >= 1
    loop = tasks._thread_event_loop()
    assert [task for task in asyncio.all_tasks(loop) if not task.done()] == []
    # 끝내지 못한 칸은 claim을 돌려받는다.
    assert events.released


def test_prefetch_serializes_gemini_like_the_provider_pool(monkeypatch):
    """Gemini는 프로세스당 1개 — 우리 대기열을 지나면 곧바로 호출이라 07:00 재확인이 공급자 대기열 뒤에 숨지 않는다."""

    import asyncio

    tasks, events, _ = _prefetch_shell(monkeypatch)
    in_flight = SimpleNamespace(gemini=0, peak=0)

    async def fetch(query_text, platform, **_kw):
        if platform == "gemini":
            in_flight.gemini += 1
            in_flight.peak = max(in_flight.peak, in_flight.gemini)
        await asyncio.sleep(0.01)
        if platform == "gemini":
            in_flight.gemini -= 1
        return {"measurement_status": "SUCCESS", "raw_response": "답", "provider_calls": 1}

    def claim(_db, slot_id, *, stage, deadline):
        events.claims.append(slot_id)
        return SimpleNamespace(
            id=slot_id, platform="gemini", scope="MONTHLY", measurement_run_id=uuid.uuid4(),
            answer_attempt_count=1,
        ), uuid.uuid4()

    monkeypatch.setattr(tasks, "fetch_answer", fetch)
    monkeypatch.setattr(tasks, "claim_slot_stage", claim)

    assert _run_prefetch(tasks, {uuid.uuid4(): [_slot(), _slot(), _slot()]}, concurrency=6) == 3
    assert in_flight.peak == 1


def test_new_query_matrix_for_a_brand_target_is_stored_as_brand():
    from app.workers import tasks

    class _DB:
        def __init__(self):
            self.added = []

        def get(self, *_a):
            return None

        def add(self, row):
            self.added.append(row)

        def flush(self):
            self.added[-1].id = uuid.uuid4()

    db = _DB()
    target = _target("행복의원 어때?", group="A", role="FIXED")
    variant = target.variants[0]
    variant.query_matrix_id = None
    variant.query_target = target

    query = tasks._ensure_variant_query_matrix(db, SimpleNamespace(id=uuid.uuid4()), variant)

    assert query.query_intent == "BRAND"
