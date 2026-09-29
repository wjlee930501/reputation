"""내과 병원에 영상의학과 주제가 배정되지 않는다 — 규칙과 세 배정 지점(달력·생성 시점·주제 교체).

근거 사례: 신기한속내과연합의원(1fb77e67)의 V0 자동 시드 질문 61810ef4 "대구 동구 영상의학과
병원 어디가 좋은지 비교해줘"(specialty "동구 영상의학과")가 3일 소진 뒤 주제 교체로 d7a5603e
"대구 동구 영상의학과 병원 추천해줘"를 받았다. 병원 진료과 목록에는 영상의학과도 있다.
DB 조회는 fake로 흉내 내고, 실제 SQL 경로는 PG 테스트(monthly_slots·topic swap)가 본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services import content_target_planner as planner
from app.services.gap_driven_slots import build_gap_targets
from app.services.specialty_compatibility import (
    INCOMPATIBLE_TOPIC_DEPARTMENTS,
    forbidden_topic_departments,
    hospital_primary_departments,
    target_conflicts_with_hospital,
    target_fits_hospital,
)
from app.workers import topic_swap_fallback
from tests.test_content_target_planner import _PlannerDB
from tests.test_topic_swap_fallback import _FakeDB, _item

SINGIHAN = SimpleNamespace(
    id=uuid.UUID("1fb77e67-2b5f-4b10-be52-84e868d391f6"),
    name="신기한속내과연합의원",
    specialties=["내과", "소화기내과", "영상의학과", "건강검진"],
)


def _target(name: str, specialty: str | None, **overrides):
    values = {
        "id": uuid.uuid4(),
        "name": name,
        "specialty": specialty,
        "target_intent": "추천 탐색",
        "region_terms": ["대구"],
        "condition_or_symptom": None,
        "treatment": None,
        "priority": "HIGH",
        "target_month": "2026-09",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _61810ef4():
    return _target(
        "대구 동구 영상의학과 병원 어디가 좋은지 비교해줘",
        "동구 영상의학과",
        id=uuid.UUID("61810ef4-81c8-4a4d-ac03-8f438a03716d"),
        target_intent="비교 검토",
    )


def _d7a5603e():
    return _target(
        "대구 동구 영상의학과 병원 추천해줘",
        "동구 영상의학과",
        id=uuid.UUID("d7a5603e-e66c-4cab-8b18-495f3b68fc72"),
    )


def _internal_medicine_target():
    return _target("대구 동구 내과 병원 추천해줘", "동구 내과", priority="NORMAL")


# ── 규칙 ─────────────────────────────────────────────────────────────────────


def test_the_rule_is_a_narrow_explicit_table():
    assert INCOMPATIBLE_TOPIC_DEPARTMENTS == {"내과": frozenset({"영상의학과"})}


def test_internal_medicine_hospital_rejects_both_radiology_seeds_despite_listing_radiology():
    assert "영상의학과" in SINGIHAN.specialties
    assert hospital_primary_departments(SINGIHAN) == {"내과"}
    assert forbidden_topic_departments(SINGIHAN) == {"영상의학과"}

    assert target_conflicts_with_hospital(_61810ef4(), SINGIHAN)
    assert target_conflicts_with_hospital(_d7a5603e(), SINGIHAN)
    # 질문 문장만 영상의학과를 말해도(진료과 칸이 비어도) 막는다.
    assert target_conflicts_with_hospital(
        _target("율하동에서 영상의학과 진료 받을 수 있는 병원 알려줘", None), SINGIHAN
    )
    assert target_conflicts_with_hospital(
        {"name": "대구 동구 영상의학 전문의 추천", "specialty": None}, SINGIHAN
    )


def test_structured_specialty_wins_over_a_question_that_only_mentions_radiology():
    """질문 문장이 영상의학과를 비교로만 말해도, 구조화된 진료과가 내과면 막지 않는다."""
    comparison = _target("대구 동구 내과와 영상의학과 중 건강검진은 어디로 가야 해?", "동구 내과")

    assert target_fits_hospital(comparison, SINGIHAN)
    assert target_fits_hospital(
        {"name": "내과와 영상의학과 차이 알려줘", "specialty": "내과"}, SINGIHAN
    )
    # 진료과 칸이 영상의학과면 문장과 무관하게 막는다(61810ef4·d7a5603e 모양).
    assert target_conflicts_with_hospital(_target("대구 동구 병원 추천해줘", "동구 영상의학과"), SINGIHAN)


@pytest.mark.parametrize(
    "target",
    [
        _internal_medicine_target(),
        _target("율하동 CT 검사 가능한 병원 추천해줘", None, treatment="CT 검사"),
        _target("복부초음파 진료를 받으려는데 대구 동구 어느 병원으로 가야 해?", None),
        _target("대구 동구 소화기내과 병원 추천해줘", "동구 소화기내과"),
    ],
)
def test_internal_medicine_hospital_keeps_its_own_topics(target):
    assert target_fits_hospital(target, SINGIHAN)


@pytest.mark.parametrize(
    "hospital",
    [
        SimpleNamespace(name="밝은영상의학과의원", specialties=["영상의학과", "내과"]),
        SimpleNamespace(name="OO내과영상의학과의원", specialties=["내과", "영상의학과"]),
        SimpleNamespace(name="행복드림의원", specialties=["일반의원", "내과 진료"]),
        SimpleNamespace(name="장편한외과의원", specialties=["외과", "대장항문과"]),
        None,
    ],
)
def test_other_hospitals_are_not_filtered(hospital):
    assert target_fits_hospital(_d7a5603e(), hospital)


def test_first_listed_specialty_marks_an_internal_medicine_hospital_without_the_word_in_its_name():
    hospital = SimpleNamespace(name="서울W의원 위례점", specialties=["내과", "영상의학과"])

    assert target_conflicts_with_hospital(_d7a5603e(), hospital)


# ── (ii-a) 달력 슬롯 배정: 격차 기반 타깃 ──────────────────────────────────────


def test_calendar_gap_targets_exclude_radiology_for_an_internal_medicine_hospital():
    radiology = _61810ef4()
    internal = _internal_medicine_target()

    targets = build_gap_targets(
        [(radiology, "MISSING_MENTION"), (internal, "LOW_MENTION_SHARE")], hospital=SINGIHAN
    )

    assert [target.id for target in targets] == [internal.id]


# ── (ii-b) 생성 시점 슬롯 배정: _choose_target ────────────────────────────────


def _choose(db, hospital):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        content_type=ContentType.FAQ,
        scheduled_date=date(2026, 9, 29),
        query_target_id=None,
    )
    return planner._choose_target(db, item=item, hospital_id=SINGIHAN.id, hospital=hospital)


def test_generation_time_assignment_skips_radiology_even_when_it_ranks_first():
    radiology = _d7a5603e()  # HIGH + 미언급 격차 — 필터가 없으면 1순위다
    internal = _internal_medicine_target()
    gaps = [(radiology.id, "MISSING_MENTION")]

    unfiltered = _choose(_PlannerDB(targets=[radiology, internal], gaps=gaps), None)
    filtered = _choose(_PlannerDB(targets=[radiology, internal], gaps=gaps), SINGIHAN)

    assert unfiltered is radiology
    assert filtered is internal


def test_brief_preparation_replaces_a_precommitted_radiology_target(monkeypatch):
    radiology = _61810ef4()
    internal = _internal_medicine_target()
    calls: list[dict] = []

    def choose(_db, **kwargs):
        calls.append(kwargs)
        return internal

    monkeypatch.setattr(planner, "_lock_target_planning", lambda *_a: None)
    monkeypatch.setattr(planner, "_load_target", lambda *_a: radiology)
    monkeypatch.setattr(planner, "_choose_target", choose)
    monkeypatch.setattr(planner, "_load_or_choose_action", lambda *_a, **_k: None)
    monkeypatch.setattr(planner, "build_content_brief", lambda **kwargs: {"query_target": kwargs["query_target"]})
    item = SimpleNamespace(
        brief_status=None,
        content_brief=None,
        query_target_id=radiology.id,
        scheduled_date=date(2026, 9, 29),
    )

    brief = planner.prepare_automatic_content_brief_sync(
        None, item=item, hospital=SINGIHAN, philosophy=SimpleNamespace()
    )

    assert item.query_target_id == internal.id
    assert brief["query_target"] is internal
    assert calls and calls[0]["hospital"] is SINGIHAN


# ── (i) 주제 교체 폴백 ────────────────────────────────────────────────────────


@pytest.fixture
def swap_side_effects(monkeypatch):
    """교체 뒤 시도 기록·인시던트 종결은 tasks·async 세션 경로다 — 더블 DB에서는 호출만 잡는다."""

    recorded: list[uuid.UUID] = []

    def record(_db, item, *, now):
        recorded.append(item.id)

    async def recover(_incident_id):
        return True

    monkeypatch.setattr(topic_swap_fallback, "_record_topic_swapped_attempt", record)
    monkeypatch.setattr(topic_swap_fallback, "_recover_incident_async", recover)
    return recorded


def _swap_item():
    return _item(
        hospital_id=SINGIHAN.id,
        hospital=SINGIHAN,
        title=None,
        query_target_id=uuid.UUID("61810ef4-81c8-4a4d-ac03-8f438a03716d"),
        content_brief={},
    )


def _run(db):
    now = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
    return topic_swap_fallback.swap_exhausted_topics(
        db, window_start=date(2026, 9, 22), window_end=date(2026, 10, 1), now=now
    )


def test_topic_swap_never_moves_an_internal_medicine_slot_to_radiology(
    monkeypatch, swap_side_effects
):
    """61810ef4 → d7a5603e 교체를 재현한다: 후보가 영상의학과면 교체하지 않는다."""
    seen: list[dict] = []

    def choose(_db, **kwargs):
        seen.append(kwargs)
        return _d7a5603e()

    monkeypatch.setattr(topic_swap_fallback, "_choose_target", choose)
    db = _FakeDB([_swap_item()])

    report = _run(db)

    assert (report.swapped, report.incompatible_specialty) == (0, 1)
    assert db.updates == []
    # 후보 선택 자체도 병원을 알고 고른다(같은 규칙이 _choose_target 안에서도 걸린다).
    assert seen[0]["hospital"] is SINGIHAN


def test_topic_swap_still_moves_to_a_compatible_topic(monkeypatch, swap_side_effects):
    monkeypatch.setattr(
        topic_swap_fallback, "_choose_target", lambda _db, **_k: _internal_medicine_target()
    )
    db = _FakeDB([_swap_item()])

    report = _run(db)

    assert report.swapped == 1


def test_swap_resets_the_reference_checks_of_the_old_topic(monkeypatch, swap_side_effects):
    monkeypatch.setattr(
        topic_swap_fallback, "_choose_target", lambda _db, **_k: _internal_medicine_target()
    )
    db = _FakeDB([_swap_item()])

    _run(db)

    statement = db.updates[0]
    values = {key.name: value for key, value in statement._values.items()}
    assert "reference_checks" in values

