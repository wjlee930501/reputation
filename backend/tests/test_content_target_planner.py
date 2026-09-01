import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.content_target_planner import (
    AFFINITY_EXACT,
    AFFINITY_MISMATCH,
    _content_type_affinity,
    prepare_automatic_content_brief_sync,
)


def _target(**overrides):
    base = {
        "id": uuid.uuid4(),
        "name": "강남역 정형외과 병원 추천해줘",
        "target_intent": "추천 탐색",
        "region_terms": ["강남역"],
        "condition_or_symptom": None,
        "treatment": None,
        "priority": "HIGH",
        "target_month": "2026-10",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── 유형 친화도: 타깃 간 변별력이 생겼는가 ────────────────────────────────────


def test_affinity_discriminates_between_targets():
    """예전 구현은 모든 타깃에서 같은 값을 돌려줘 정렬에 기여하지 못했다."""
    disease_target = _target(condition_or_symptom="허리디스크", region_terms=[])
    treatment_target = _target(treatment="대장내시경", region_terms=[])
    local_target = _target(region_terms=["강남역"])

    affinities = {
        "disease": _content_type_affinity(disease_target, ContentType.DISEASE),
        "treatment": _content_type_affinity(treatment_target, ContentType.DISEASE),
        "local": _content_type_affinity(local_target, ContentType.DISEASE),
    }

    assert affinities["disease"] == AFFINITY_EXACT
    assert affinities["disease"] < affinities["treatment"] < affinities["local"]


@pytest.mark.parametrize(
    "content_type,target,expected",
    [
        (ContentType.LOCAL, _target(region_terms=["강남역"]), AFFINITY_EXACT),
        (ContentType.LOCAL, _target(region_terms=[]), AFFINITY_MISMATCH),
        (ContentType.TREATMENT, _target(treatment="대장내시경"), AFFINITY_EXACT),
        (ContentType.TREATMENT, _target(), AFFINITY_MISMATCH),
        (ContentType.DISEASE, _target(condition_or_symptom="치질"), AFFINITY_EXACT),
        (ContentType.DISEASE, _target(), AFFINITY_MISMATCH),
        (ContentType.FAQ, _target(name="강남역 정형외과 병원 추천해줘"), AFFINITY_EXACT),
        (ContentType.HEALTH, _target(target_intent="비용 확인"), AFFINITY_EXACT),
        (ContentType.COLUMN, _target(target_intent="정보 탐색"), AFFINITY_EXACT),
        (ContentType.NOTICE, _target(condition_or_symptom="치질"), AFFINITY_MISMATCH),
    ],
)
def test_affinity_per_type(content_type, target, expected):
    assert _content_type_affinity(target, content_type) == expected


def test_faq_prefers_question_form_targets():
    question = _target(name="강남역 정형외과 병원 추천해줘")
    keyword_only = _target(name="강남역 정형외과")

    assert _content_type_affinity(question, ContentType.FAQ) < _content_type_affinity(
        keyword_only, ContentType.FAQ
    )


# ── 타깃 선택 순서: 격차 → 우선순위 → 유형 적합도 → 라운드로빈 ────────────────


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
    """_choose_target이 순서대로 던지는 4개 쿼리를 모사한다.

    (1) ACTIVE 타깃  (2) 이번 달 이미 연결된 타깃 id  (3) 열린 액션의 타깃 id
    (4) 열린 노출 격차 (target_id, gap_type)
    """

    def __init__(self, *, targets, used_ids=(), action_ids=(), gaps=()):
        self._results = [
            _Result(targets),
            _Result(list(used_ids)),
            _Result(list(action_ids)),
            _Result(list(gaps)),
        ]
        self._call = 0

    def execute(self, _stmt):
        result = self._results[self._call]
        self._call += 1
        return result


def _choose(db, content_type, hospital_id):
    from app.services.content_target_planner import _choose_target

    item = SimpleNamespace(
        id=uuid.uuid4(),
        content_type=content_type,
        scheduled_date=date(2026, 10, 6),
        query_target_id=None,
    )
    return _choose_target(db, item=item, hospital_id=hospital_id)


def test_missing_mention_targets_are_answered_before_mentioned_ones():
    hospital_id = uuid.uuid4()
    mentioned = _target(name="역삼동 오십견 진료 가능한 병원", condition_or_symptom="오십견")
    missing = _target(
        name="강남역에서 허리디스크 치료하는 병원 알려줘", condition_or_symptom="허리디스크"
    )

    chosen = _choose(
        _PlannerDB(
            targets=[mentioned, missing],
            gaps=[(missing.id, "MISSING_MENTION")],
        ),
        ContentType.DISEASE,
        hospital_id,
    )

    assert chosen is missing


def test_low_mention_share_ranks_behind_missing_mention():
    hospital_id = uuid.uuid4()
    low = _target(name="역삼동 오십견 진료 가능한 병원", condition_or_symptom="오십견")
    missing = _target(
        name="강남역에서 허리디스크 치료하는 병원 알려줘", condition_or_symptom="허리디스크"
    )

    chosen = _choose(
        _PlannerDB(
            targets=[low, missing],
            gaps=[(low.id, "LOW_MENTION_SHARE"), (missing.id, "MISSING_MENTION")],
        ),
        ContentType.DISEASE,
        hospital_id,
    )

    assert chosen is missing


def test_type_affinity_outranks_round_robin_usage():
    """같은 격차·우선순위면 슬롯 유형이 답할 수 있는 질문을 먼저 고른다."""
    hospital_id = uuid.uuid4()
    fits = _target(name="역삼동 대장내시경 가능한 병원 추천해줘", treatment="대장내시경")
    does_not_fit = _target(name="강남역 정형외과 병원 추천해줘")

    chosen = _choose(
        _PlannerDB(
            targets=[does_not_fit, fits],
            # 이미 한 번 쓴 타깃이라도 유형이 맞으면 먼저 고른다.
            used_ids=[str(fits.id)],
            gaps=[
                (fits.id, "MISSING_MENTION"),
                (does_not_fit.id, "MISSING_MENTION"),
            ],
        ),
        ContentType.TREATMENT,
        hospital_id,
    )

    assert chosen is fits


def test_round_robin_still_breaks_ties_between_equally_fitting_targets():
    hospital_id = uuid.uuid4()
    used = _target(name="역삼동 오십견 진료 가능한 병원", condition_or_symptom="오십견")
    fresh = _target(name="위례동 치질 진료 가능한 병원", condition_or_symptom="치질")

    chosen = _choose(
        _PlannerDB(
            targets=[used, fresh],
            used_ids=[str(used.id)],
            gaps=[(used.id, "MISSING_MENTION"), (fresh.id, "MISSING_MENTION")],
        ),
        ContentType.DISEASE,
        hospital_id,
    )

    assert chosen is fresh


def test_legacy_targets_are_backfilled_before_ranking():
    """구조 필드가 비어 있던 레거시 타깃도 선택 시점에 문장으로 복원된다."""
    hospital_id = uuid.uuid4()
    legacy = _target(
        name="역삼동 대장내시경 가능한 병원 추천해줘",
        region_terms=[],
        condition_or_symptom=None,
        treatment=None,
        target_intent="증상 탐색",
    )

    chosen = _choose(
        _PlannerDB(targets=[legacy], gaps=[(legacy.id, "MISSING_MENTION")]),
        ContentType.TREATMENT,
        hospital_id,
    )

    assert chosen is legacy
    assert legacy.treatment == "대장내시경"
    assert legacy.region_terms == ["역삼동"]


def test_existing_approved_brief_receives_current_planned_publish_date() -> None:
    item = SimpleNamespace(
        brief_status=BRIEF_STATUS_APPROVED,
        content_brief={"target_query": "수원 변비 검사"},
        scheduled_date=date(2026, 7, 31),
    )

    result = prepare_automatic_content_brief_sync(
        None,
        item=item,
        hospital=SimpleNamespace(),
        philosophy=SimpleNamespace(),
    )

    assert result["planned_publish_date"] == "2026-07-31"
    assert "planned_publish_date" not in item.content_brief
