"""병원별 콘텐츠 주력 주제 — 마케터가 정한 방향으로 다음 글의 주제를 기운다."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services.content_focus import (
    ContentFocusError,
    focus_quota,
    matching_topic,
    parse_content_focus,
    target_matches_topic,
    target_subject_is_region,
    topic_match_key,
    validate_content_focus,
)


def _target(**overrides):
    base = {
        "id": uuid.uuid4(),
        "name": "하남시 위내시경 잘하는 병원 추천해줘",
        "target_intent": "추천 탐색",
        "region_terms": ["하남시"],
        "condition_or_symptom": None,
        "treatment": "위내시경",
        "specialty": None,
        "priority": "HIGH",
        "target_month": "2026-11",
        "variants": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── 저장 모양 ──────────────────────────────────────────────────────────────


def test_legacy_string_list_is_read_as_switched_off():
    """0054가 남긴 문자열 목록은 쓰이지 않던 옛 값이다 — 저장 모양이 바뀌어도 켜지지 않는다."""
    focus = parse_content_focus(["정형외과", "신경외과"])
    assert not focus.enabled
    assert focus.legacy_topics == ("정형외과", "신경외과")
    assert focus.active_topics("2026-11") == ()


def test_round_trip_and_period():
    focus = validate_content_focus(
        topics=["내시경", " 내시경 ", "여의사 진료"], share=70, from_month="2026-11", until_month="2026-12"
    )
    assert focus.topics == ("내시경", "여의사 진료")
    stored = focus.to_storage()
    again = parse_content_focus(stored)
    assert again == focus
    assert again.active_topics("2026-10") == ()
    assert again.active_topics("2026-11") == ("내시경", "여의사 진료")
    assert again.active_topics("2026-12") == ("내시경", "여의사 진료")
    assert again.active_topics("2027-01") == ()


def test_open_ended_period_and_switching_off():
    focus = validate_content_focus(topics=["내시경"], share=50, from_month=None, until_month="")
    assert focus.applies_to("2030-01")
    off = validate_content_focus(topics=[], share=50, from_month=None, until_month=None)
    assert off.to_storage() == []
    assert not parse_content_focus(off.to_storage()).enabled


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"topics": ["내"], "share": 50}, "2~20자"),
        ({"topics": ["가" * 21], "share": 50}, "2~20자"),
        ({"topics": ["내시경<script>"], "share": 50}, "기호"),
        ({"topics": [f"주제{i}" for i in range(6)], "share": 50}, "5개까지"),
        ({"topics": ["내시경"], "share": 40}, "비중"),
        ({"topics": ["내시경"], "share": 50, "from_month": "2026-13"}, "시작 달"),
        ({"topics": ["내시경"], "share": 50, "from_month": "2026-12", "until_month": "2026-11"}, "앞설 수"),
    ],
)
def test_invalid_inputs_are_rejected_with_screen_messages(kwargs, message):
    kwargs.setdefault("from_month", None)
    kwargs.setdefault("until_month", None)
    with pytest.raises(ContentFocusError) as excinfo:
        validate_content_focus(**kwargs)
    assert message in str(excinfo.value)


def test_broken_storage_is_switched_off_not_an_error():
    assert not parse_content_focus({"version": 9, "topics": ["내시경"]}).enabled
    assert not parse_content_focus({"version": 1, "topics": ["내시경"], "from_month": "2026-12", "until_month": "2026-11"}).enabled
    assert parse_content_focus({"version": 1, "topics": ["내시경"], "share": 33}).share == 50
    assert not parse_content_focus(None).enabled


# ── 주제 ↔ 측정 질문 대조 ─────────────────────────────────────────────────


def test_generic_tail_words_are_dropped_before_matching():
    assert topic_match_key("내시경 검사") == "내시경"
    assert topic_match_key("내시경 검사 관련 키워드") == "내시경"
    assert topic_match_key("여의사 진료") == "여의사"
    # 끝말을 떼면 너무 짧아지는 주제는 그대로 둔다.
    assert topic_match_key("검사") == "검사"


def test_topic_matches_question_name_treatment_and_variants():
    upper = _target()
    lower = _target(name="대장 검사 병원", treatment=None, condition_or_symptom="대장용종")
    variant_only = _target(
        name="하남 검진 병원",
        treatment=None,
        variants=[SimpleNamespace(query_text="하남 대장내시경 수면으로 하는 곳", is_active=True)],
    )
    inactive_variant = _target(
        name="하남 검진 병원",
        treatment=None,
        variants=[SimpleNamespace(query_text="하남 대장내시경 수면", is_active=False)],
    )
    assert target_matches_topic(upper, "내시경 검사")
    assert not target_matches_topic(lower, "내시경")
    assert target_matches_topic(variant_only, "내시경")
    assert not target_matches_topic(inactive_variant, "내시경")
    assert matching_topic(upper, ["여의사", "내시경"]) == "내시경"
    assert matching_topic(None, ["내시경"]) is None


@pytest.mark.parametrize(
    "share,total,expected",
    [(50, 12, 6), (30, 12, 4), (70, 12, 9), (100, 12, 12), (50, 7, 4), (50, 0, 0)],
)
def test_focus_quota(share, total, expected):
    assert focus_quota(share, total) == expected


# ── 진료 항목 자리에 지역명이 들어간 엉성한 질문 ─────────────────────────────


def test_region_word_in_subject_slot_is_detected():
    hospital = SimpleNamespace(region=["하남시", "위례", "송파", "성남 수정구"])
    assert target_subject_is_region(
        _target(name="하남 진료를 받으려는데 하남시 어느 병원으로 가야 해?", treatment=None, condition_or_symptom="하남"),
        hospital,
    )
    assert target_subject_is_region(
        _target(name="송파 치료 비용이 얼마나 드는지 알려줘", treatment="송파 치료", region_terms=[]), hospital
    )
    assert target_subject_is_region(
        _target(name="창원 진료를 받으려는데 창원시 마산회원구 어느 병원", treatment=None,
                condition_or_symptom="창원", region_terms=["창원시 마산회원구"]),
        None,
    )


def test_real_subjects_are_not_mistaken_for_regions():
    hospital = SimpleNamespace(region=["하남시", "위례", "송파", "성남 수정구"])
    for subject in ("위내시경", "위장질환", "수정체", "갑상선결절"):
        assert not target_subject_is_region(_target(treatment=subject), hospital), subject
    assert not target_subject_is_region(_target(treatment=None, condition_or_symptom=None), hospital)


# ── 글 주제 선택 ──────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _PlannerDB:
    """`_choose_target`의 쿼리 순서: 타깃 → 이번 달 연결 → 열린 액션 → 격차 → (주력 주제면) 이번 달 글."""

    def __init__(self, *, targets, month_rows=None):
        self._results = [_Result(targets), _Result([]), _Result([]), _Result([])]
        if month_rows is not None:
            self._results.append(_Result(month_rows))
        self.calls = 0

    def execute(self, _stmt):
        result = self._results[self.calls]
        self.calls += 1
        return result


def _hospital(focus=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        region=["하남시"],
        specialties=[],
        treatments=[],
        keywords=[],
        hero_specialties=[],
        content_focus_topics=focus if focus is not None else [],
    )


def _choose(db, hospital, content_type=ContentType.DISEASE, item_id=None):
    from app.services.content_target_planner import _choose_target

    item = SimpleNamespace(
        id=item_id or uuid.uuid4(),
        content_type=content_type,
        scheduled_date=date(2026, 11, 18),
        query_target_id=None,
    )
    return _choose_target(db, item=item, hospital_id=hospital.id, hospital=hospital)


_FOCUS = {"version": 1, "topics": ["내시경"], "share": 50, "from_month": "2026-11", "until_month": "2026-12"}


def _liver():
    return _target(
        name="간질환 진료를 받으려는데 하남시 어느 병원으로 가야 해?", treatment=None, condition_or_symptom="간질환"
    )


def _scope():
    return _target(
        name="위내시경 받으려는데 하남시 어느 병원으로 가야 해?", treatment=None, condition_or_symptom="위내시경"
    )


def test_without_focus_order_is_unchanged(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    db = _PlannerDB(targets=[liver, scope])
    chosen = _choose(db, _hospital())
    # 다른 조건이 같으면 이름순 — 주력 주제 없이는 "간질환"이 먼저다.
    assert chosen is liver
    assert db.calls == 4, "주력 주제가 없으면 이번 달 글을 따로 읽지 않는다"


def test_focus_topic_question_is_chosen_while_month_quota_is_open(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    month_rows = [(uuid.uuid4(), None) for _ in range(11)]  # 이번 글 포함 12편, 주력 주제 글 0편
    chosen = _choose(_PlannerDB(targets=[liver, scope], month_rows=month_rows), _hospital(_FOCUS))
    assert chosen is scope


def test_focus_stops_preferring_once_quota_is_met(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    # 12편 중 6편이 이미 주력 주제 글 — 50% 목표를 채웠다.
    month_rows = [(uuid.uuid4(), "내시경") for _ in range(6)] + [(uuid.uuid4(), None) for _ in range(5)]
    chosen = _choose(_PlannerDB(targets=[liver, scope], month_rows=month_rows), _hospital(_FOCUS))
    assert chosen is liver


def test_focus_outside_period_is_ignored(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    focus = {**_FOCUS, "from_month": "2026-12"}
    db = _PlannerDB(targets=[liver, scope])
    assert _choose(db, _hospital(focus)) is liver
    assert db.calls == 4


def test_focus_never_overrides_a_type_mismatch(monkeypatch):
    """LOCAL 글은 지역이 없는 질문에 붙이지 않는다 — 주력 주제여도 그렇다."""
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    local = _target(name="하남시 간질환 병원 추천해줘", treatment=None, condition_or_symptom="간질환", region_terms=["하남시"])
    scope_no_region = _target(name="위내시경 준비는 어떻게 하나요?", treatment="위내시경", region_terms=[])
    month_rows = [(uuid.uuid4(), None) for _ in range(11)]
    chosen = _choose(
        _PlannerDB(targets=[local, scope_no_region], month_rows=month_rows),
        _hospital(_FOCUS),
        content_type=ContentType.LOCAL,
    )
    assert chosen is local


def test_region_subject_questions_are_never_chosen(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    bad = _target(name="하남 진료를 받으려는데 하남시 어느 병원으로 가야 해?", treatment=None, condition_or_symptom="하남")
    assert _choose(_PlannerDB(targets=[bad]), _hospital()) is None
    liver = _liver()
    assert _choose(_PlannerDB(targets=[bad, liver]), _hospital()) is liver


def test_record_focus_topic_marks_the_item():
    from app.services.content_target_planner import _record_focus_topic

    item = SimpleNamespace(scheduled_date=date(2026, 11, 18), content_focus_topic="옛 값")
    _record_focus_topic(item, _scope(), _hospital(_FOCUS))
    assert item.content_focus_topic == "내시경"
    _record_focus_topic(item, _liver(), _hospital(_FOCUS))
    assert item.content_focus_topic is None
    october = SimpleNamespace(scheduled_date=date(2026, 10, 30), content_focus_topic=None)
    _record_focus_topic(october, _scope(), _hospital(_FOCUS))
    assert october.content_focus_topic is None
