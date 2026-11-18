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


# ── Codex 검토 반영 ───────────────────────────────────────────────────────


def test_medical_words_that_look_like_regions_are_kept():
    """'경산'(출산 경험)·'수정'은 의학 용어다 — 템플릿이 진료 항목 자리에 넣은 모양일 때만 지역명으로 본다."""
    hospital = SimpleNamespace(region=["경산시", "성남 수정구"])
    assert not target_subject_is_region(
        _target(name="초산과 경산의 분만 차이를 알려줘", treatment=None, condition_or_symptom="경산", region_terms=[]),
        hospital,
    )
    assert not target_subject_is_region(
        _target(name="시험관 수정 과정이 궁금해", treatment="수정", region_terms=[]), hospital
    )
    assert target_subject_is_region(
        _target(name="경산에서 경산 치료하는 병원 알려줘", treatment=None, condition_or_symptom="경산"), hospital
    )


def test_legacy_region_question_is_filtered_after_structure_restoration(monkeypatch):
    """구조 필드가 빈 옛 질문도 구조를 채운 뒤 걸러진다 — 처음 고를 때 새지 않는다."""
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    legacy = _target(
        name="하남 진료를 받으려는데 하남시 어느 병원으로 가야 해?",
        treatment=None,
        condition_or_symptom=None,
        region_terms=[],
    )
    assert _choose(_PlannerDB(targets=[legacy]), _hospital()) is None


def test_previous_topic_articles_do_not_fill_the_new_quota(monkeypatch):
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    month_rows = [(uuid.uuid4(), "도수치료") for _ in range(6)] + [(uuid.uuid4(), None) for _ in range(5)]
    chosen = _choose(_PlannerDB(targets=[liver, scope], month_rows=month_rows), _hospital(_FOCUS))
    assert chosen is scope


def test_focus_does_not_decide_between_mismatched_candidates(monkeypatch):
    """NOTICE 슬롯처럼 모든 후보가 유형 불일치면 주력 주제가 승부를 가르지 않는다(주력 주제 없을 때와 같다)."""
    monkeypatch.setattr("app.services.content_target_planner._lock_target_planning", lambda *_: None)
    liver, scope = _liver(), _scope()
    month_rows = [(uuid.uuid4(), None) for _ in range(11)]
    chosen = _choose(
        _PlannerDB(targets=[liver, scope], month_rows=month_rows), _hospital(_FOCUS), content_type=ContentType.NOTICE
    )
    assert chosen is liver


def test_topic_swap_clears_the_old_focus_label():
    from app.workers.topic_swap_fallback import _reset_values

    item = SimpleNamespace(
        query_target_id=uuid.uuid4(),
        topic_swap_history=[],
        content_brief={},
        generation_attempt=None,
        hospital_id=uuid.uuid4(),
        content_focus_topic="내시경",
    )
    values = _reset_values(item, uuid.uuid4(), {"reason": "test"}, attempt={"renewal": True})
    assert values["content_focus_topic"] is None


def test_topic_matches_registered_treatment_written_with_brackets():
    """마포성모탑 'PDRN 주사' — 등록 진료 항목은 '유전자(DNA/PDRN) 주사'다(2026-11-17).

    공백만 떼던 대조는 ')'가 끼어 'pdrn주사'를 찾지 못했다. 기호도 떼고 본다.
    """
    target = _target(
        name="마포역 유전자 주사 잘하는 정형외과 추천해줘",
        treatment="유전자(DNA/PDRN) 주사",
    )
    assert target_matches_topic(target, "PDRN 주사")
    assert target_matches_topic(target, "PDRN")
    assert not target_matches_topic(target, "PRP 주사")


def test_punctuation_in_topic_or_question_does_not_block_a_match():
    target = _target(name="체외충격파, 몇 번 받아야 하나요?", treatment="체외충격파치료")
    assert target_matches_topic(target, "체외 충격파")
    assert target_matches_topic(_target(name="결절종·지방종 제거술 회복 기간", treatment=None), "결절종 지방종")


# ── 측정 질문이 없는 주력 주제 → 등록 진료 항목으로 쓴다(2026-11-18 마포성모탑) ───────────────


_PDRN_FOCUS = {"version": 1, "topics": ["PDRN 주사"], "share": 50, "from_month": "2026-11", "until_month": None}
_MAPO_TREATMENTS = [
    {"name": "PRP 자가혈 재생치료", "description": "환자 본인의 혈액에서 분리한 혈소판 성분을 이용하는 재생 치료"},
    {"name": "유전자(DNA/PDRN) 주사", "description": "PDRN 성분 주사로 손상된 조직의 회복 과정을 돕는 비수술 치료입니다."},
    "도수치료",
]


def _mapo(focus=_PDRN_FOCUS):
    return SimpleNamespace(**{**vars(_hospital(focus)), "treatments": _MAPO_TREATMENTS})


def test_topic_words_match_a_treatment_name_with_extra_words_between():
    """'유전자(PDRN) 주사'는 이어 붙이면 '유전자pdrn주사'라 '유전자dnapdrn주사'에 없다 — 낱말로 본다."""
    from app.services.content_focus import text_matches_topic, treatments_matching_topic

    assert text_matches_topic("유전자(DNA/PDRN) 주사", "유전자(PDRN) 주사")
    assert text_matches_topic("유전자(DNA/PDRN) 주사", "PDRN 주사")
    assert not text_matches_topic("유전자(DNA/PDRN) 주사", "PRP 주사")
    assert not text_matches_topic("PRP 자가혈 재생치료", "PDRN 주사")
    # 일반어 낱말("검사")은 대조에 쓰지 않는다 — '내시경 검사'는 '위내시경'과 맞는다.
    assert text_matches_topic("위내시경", "내시경 검사")
    assert treatments_matching_topic(_mapo(), "유전자(PDRN) 주사") == ["유전자(DNA/PDRN) 주사"]
    assert treatments_matching_topic(_mapo(), "도수") == ["도수치료"]
    assert treatments_matching_topic(_mapo(), "여의사") == []


def _slot(content_type=ContentType.TREATMENT, brief=None, query_target_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        content_type=content_type,
        scheduled_date=date(2026, 11, 20),
        query_target_id=query_target_id,
        content_focus_topic=None,
        content_brief=brief,
    )


class _MonthDB:
    """`_focus_topics_needed`가 읽는 이번 달 글 목록 한 번."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, _stmt):
        return _Result(self.rows)


def test_treatment_focus_is_used_when_no_question_matches():
    from app.services.content_target_planner import _treatment_focus

    month_rows = [(uuid.uuid4(), None) for _ in range(11)]
    focus = _treatment_focus(_MonthDB(month_rows), item=_slot(), hospital=_mapo(), target=_liver())
    assert focus == {"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"}


def test_matching_question_wins_over_the_treatment_fallback():
    from app.services.content_target_planner import _treatment_focus

    pdrn_question = _target(name="마포역 PDRN 주사 잘하는 곳 추천해줘", treatment="PDRN 주사", region_terms=[])
    month_rows = [(uuid.uuid4(), None) for _ in range(11)]
    assert _treatment_focus(_MonthDB(month_rows), item=_slot(), hospital=_mapo(), target=pdrn_question) is None


def test_treatment_focus_respects_quota_type_and_registration():
    from app.services.content_target_planner import _treatment_focus

    full = [(uuid.uuid4(), "PDRN 주사") for _ in range(6)] + [(uuid.uuid4(), None) for _ in range(5)]
    assert _treatment_focus(_MonthDB(full), item=_slot(), hospital=_mapo(), target=None) is None
    open_rows = [(uuid.uuid4(), None) for _ in range(11)]
    # 지역 글·공지는 측정 질문 없이 쓰지 않는다.
    for kind in (ContentType.LOCAL, ContentType.NOTICE):
        assert _treatment_focus(_MonthDB(open_rows), item=_slot(kind), hospital=_mapo(), target=None) is None
    # 진료 항목에도 없으면 종전처럼 고르지 않는다.
    unregistered = SimpleNamespace(**{**vars(_mapo()), "treatments": ["도수치료"]})
    assert _treatment_focus(_MonthDB(open_rows), item=_slot(), hospital=unregistered, target=None) is None


def test_stored_treatment_focus_is_kept_on_retry_until_the_focus_changes():
    from app.services.content_target_planner import _stored_focus_treatment

    stored = {"focus_treatment": {"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"}}
    assert _stored_focus_treatment(_slot(brief=stored), _mapo()) == stored["focus_treatment"]
    # 주력 주제를 바꾸면, 진료 항목이 빠지면, 질문이 배정되면 다시 고른다.
    other = {**_PDRN_FOCUS, "topics": ["체외충격파"]}
    assert _stored_focus_treatment(_slot(brief=stored), _mapo(other)) is None
    dropped = SimpleNamespace(**{**vars(_mapo()), "treatments": ["도수치료"]})
    assert _stored_focus_treatment(_slot(brief=stored), dropped) is None
    assert _stored_focus_treatment(_slot(brief=stored, query_target_id=uuid.uuid4()), _mapo()) is None


def test_record_focus_topic_counts_a_treatment_focus_article():
    from app.services.content_target_planner import _record_focus_topic

    item = _slot()
    _record_focus_topic(item, None, _mapo(), {"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"})
    assert item.content_focus_topic == "PDRN 주사"


def test_brief_for_a_treatment_focus_slot_uses_the_registered_treatment():
    from app.services.content_brief import build_content_brief

    hospital = _mapo()
    item = SimpleNamespace(id=uuid.uuid4(), title=None, content_type=ContentType.TREATMENT)
    brief = build_content_brief(
        hospital=hospital,
        content_item=item,
        focus_treatment={"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"},
    )
    assert brief["target_keyword"] == "PDRN 주사"
    assert brief["target_query"] == "PDRN 주사에 대해 알고 싶어요"
    assert brief["treatment_narrative"]["source"] == "hospital_profile"
    assert brief["treatment_narrative"]["treatment"] == "유전자(DNA/PDRN) 주사"
    assert brief["focus_treatment"] == {"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"}
    assert brief["query_target"] is None
    # 질문이 있는 슬롯에는 진료 항목 주제를 싣지 않는다.
    with_target = build_content_brief(
        hospital=hospital,
        content_item=item,
        query_target=SimpleNamespace(**{**vars(_scope()), "decision_criteria": []}),
        focus_treatment={"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"},
    )
    assert "focus_treatment" not in with_target


def test_planner_writes_the_registered_treatment_when_no_question_matches(monkeypatch):
    """생성 경로 그대로: 질문은 무관한 것만 있고, 주력 주제 'PDRN 주사'는 등록 진료 항목으로 쓴다."""
    from app.services import content_target_planner as planner

    monkeypatch.setattr(planner, "_lock_target_planning", lambda *_: None)
    monkeypatch.setattr(planner, "_choose_target", lambda *args, **kwargs: _liver())
    monkeypatch.setattr(planner, "_focus_topics_needed", lambda *args, **kwargs: ("PDRN 주사",))
    hospital = _mapo()
    philosophy = SimpleNamespace(id=uuid.uuid4(), version=1)
    item = SimpleNamespace(
        id=uuid.uuid4(),
        content_type=ContentType.TREATMENT,
        title=None,
        scheduled_date=date(2026, 11, 20),
        query_target_id=None,
        exposure_action_id=None,
        content_focus_topic=None,
        content_brief=None,
        brief_status=None,
        brief_approved_by=None,
        content_revision=1,
    )
    brief = planner.prepare_automatic_content_brief_sync(None, item=item, hospital=hospital, philosophy=philosophy)
    assert item.query_target_id is None
    assert item.content_focus_topic == "PDRN 주사"
    assert brief["focus_treatment"] == {"topic": "PDRN 주사", "treatment": "유전자(DNA/PDRN) 주사"}
    assert brief["target_keyword"] == "PDRN 주사"
    assert brief["treatment_narrative"]["treatment"] == "유전자(DNA/PDRN) 주사"

    # 재시도: 저장된 진료 항목 주제를 그대로 쓰고 질문을 다시 고르지 않는다.
    monkeypatch.setattr(planner, "_choose_target", lambda *args, **kwargs: pytest.fail("다시 고르면 안 된다"))
    again = planner.prepare_automatic_content_brief_sync(None, item=item, hospital=hospital, philosophy=philosophy)
    assert again["focus_treatment"] == brief["focus_treatment"]
    assert item.query_target_id is None


def test_word_matching_is_not_used_for_long_measurement_questions():
    """낱말 대조는 진료 항목 이름에만 — 측정 질문에는 이어 붙인 키만 본다(Codex 검토 2026-11-18)."""
    question = _target(name="무릎 관절염 상담과 어깨 주사는 어떻게 다른가요?", treatment=None)
    assert not target_matches_topic(question, "무릎 주사")
    assert target_matches_topic(_target(name="무릎 주사 치료 잘하는 곳", treatment=None), "무릎 주사")
