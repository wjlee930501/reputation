"""서비스 시작 시점(V0) 대비 축 — 언제 그려도 되고 언제 그리면 거짓말인가.

V0는 월간과 **비교 가능하지 않다**: 반복 프로토콜(마이크로 평균 5회)도, 측정 창(7일)도,
집계 방식도 다르다. 그래서 여기서 만드는 것은 비교가 아니라 라벨이 붙은 참고선 하나이고,
질문 세트마저 다르면 그 선은 아무 의미가 없으므로 아예 그리지 않는다.
"""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import sov_engine
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.workers.tasks import (
    V0_BASELINE_MIN_OVERLAP,
    _headline_uses_full_current_cohort,
    _load_v0_baseline,
    build_v0_baseline,
    v0_query_overlap_ratio,
)

TRACKING = [f"질문 {index}" for index in range(10)]


def test_overlap_ratio_is_measured_against_the_v0_question_set():
    """분모는 언제나 V0 쪽이다 — 추적 세트가 커졌다고 겹침이 낮아지면 안 된다."""
    assert v0_query_overlap_ratio(TRACKING[:5], TRACKING) == 1.0
    assert v0_query_overlap_ratio(TRACKING, TRACKING[:5]) == 0.5
    assert v0_query_overlap_ratio([], TRACKING) == 0.0
    assert v0_query_overlap_ratio(TRACKING, []) == 0.0


def test_overlap_ratio_ignores_whitespace_differences_only():
    assert v0_query_overlap_ratio(["  강남  치질   병원 "], ["강남 치질 병원"]) == 1.0
    assert v0_query_overlap_ratio(["강남 치질병원"], ["강남 치질 병원"]) == 0.0


def test_baseline_is_drawn_when_the_question_sets_overlap_enough():
    baseline = build_v0_baseline(
        v0_sov_pct=31.0,
        current_sov_pct=47.0,
        v0_query_texts=TRACKING,
        tracking_query_texts=TRACKING,
    )

    assert baseline == {
        "of_hundred": 31,
        "current_of_hundred": 47,
        "sentence": "서비스 시작 시점(V0) 대비: 31번 → 47번",
    }


def test_baseline_is_omitted_when_the_question_sets_drifted_apart():
    """다른 질문으로 잰 두 수치를 나란히 놓는 순간 그 줄은 거짓말이 된다."""
    drifted = build_v0_baseline(
        v0_sov_pct=31.0,
        current_sov_pct=47.0,
        v0_query_texts=TRACKING,
        # 10개 중 7개만 남았다 → 0.7 < 0.8
        tracking_query_texts=TRACKING[:7],
    )

    assert drifted is None
    assert V0_BASELINE_MIN_OVERLAP == 0.8


def test_the_gate_is_inclusive_at_exactly_the_threshold():
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=47.0,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING[:8],
        )
        is not None
    )


def test_baseline_is_omitted_when_either_month_has_no_number():
    assert (
        build_v0_baseline(
            v0_sov_pct=None,
            current_sov_pct=47.0,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING,
        )
        is None
    )
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=None,
            v0_query_texts=TRACKING,
            tracking_query_texts=TRACKING,
        )
        is None
    )


def test_a_hospital_without_a_v0_report_gets_no_baseline():
    assert (
        build_v0_baseline(
            v0_sov_pct=31.0,
            current_sov_pct=47.0,
            v0_query_texts=[],
            tracking_query_texts=TRACKING,
        )
        is None
    )


def test_legacy_v0_without_exact_measurement_lineage_is_suppressed():
    class _Result:
        def scalars(self):
            return self

        def first(self):
            return SimpleNamespace(sov_summary={"sov_pct": 31.0})

    class _DB:
        def execute(self, _statement):
            return _Result()

    assert _load_v0_baseline(
        _DB(),
        uuid.uuid4(),
        current_sov_pct=47.0,
        tracking_query_texts=["강남 치질 병원"],
        current_platforms=("chatgpt",),
        current_protocol=sov_engine.measurement_protocol(),
        current_cells=(),
    ) is None


def test_v0_loader_requires_exact_run_policy_platform_model_and_query_cohort():
    run_id = uuid.uuid4()
    query_id = uuid.uuid4()
    protocol = sov_engine.measurement_protocol()
    report = SimpleNamespace(sov_summary={
        "sov_pct": 31.0,
        "baseline_basis": {
            "measurement_run_id": str(run_id),
            "measurement_protocol": protocol,
            "platforms": ["chatgpt"],
            "query_snapshot": [{
                "query_id": str(query_id),
                "query_text": "강남 치질 병원",
                "query_intent": "LOCAL",
            }],
        },
    })
    rows = [SimpleNamespace(
        query_id=query_id,
        ai_platform="chatgpt",
        answer_model="answer-model",
        measurement_status="SUCCESS",
        mention_verdict="MATCHED",
        is_mentioned=True,
    )]

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalars(self):
            return self

        def first(self):
            return self.value

        def all(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((_Result(report), _Result(rows)))

        def execute(self, _statement):
            return next(self.results)

    current = ManifestCellInput(
        query_key="q1",
        query_text="강남 치질 병원",
        platform="chatgpt",
        query_intent="LOCAL",
        state="SUCCESS",
        query_matrix_id=None,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=(CellAttempt(
            record_id=uuid.uuid4(),
            measured_at=datetime(2026, 8, 31, tzinfo=UTC),
            succeeded=True,
            is_mentioned=True,
            answer_model="answer-model",
        ),),
    )

    baseline = _load_v0_baseline(
        _DB(),
        uuid.uuid4(),
        current_sov_pct=47.0,
        tracking_query_texts=["강남 치질 병원"],
        current_platforms=("chatgpt",),
        current_protocol=protocol,
        current_cells=(current,),
    )

    assert baseline is not None
    assert baseline["sentence"] == "서비스 시작 시점(V0) 대비: 31번 → 47번"

    duplicated_attempt_rows = [*rows, *rows]
    assert _load_v0_baseline(
        _db_with_results(report, duplicated_attempt_rows),
        uuid.uuid4(),
        current_sov_pct=47.0,
        tracking_query_texts=["강남 치질 병원"],
        current_platforms=("chatgpt",),
        current_protocol=protocol,
        current_cells=(current,),
    ) is None


def test_v0_loader_uses_frozen_text_after_live_query_is_edited():
    run_id = uuid.uuid4()
    query_id = uuid.uuid4()
    protocol = sov_engine.measurement_protocol()
    report = SimpleNamespace(sov_summary={
        "sov_pct": 31.0,
        "baseline_basis": {
            "measurement_run_id": str(run_id),
            "measurement_protocol": protocol,
            "platforms": ["chatgpt"],
            "query_snapshot": [{
                "query_id": str(query_id),
                "query_text": "강남 치질 병원",
                "query_intent": "LOCAL",
            }],
        },
    })
    statements = []
    db = _db_with_results(
        report,
        [SimpleNamespace(
            query_id=query_id,
            ai_platform="chatgpt",
            answer_model="answer-model",
            measurement_status="SUCCESS",
            mention_verdict="MATCHED",
            is_mentioned=True,
        )],
        statements=statements,
    )
    current = ManifestCellInput(
        query_key="q1",
        query_text="강남 치질 병원",
        platform="chatgpt",
        query_intent="LOCAL",
        state="SUCCESS",
        query_matrix_id=query_id,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=(CellAttempt(
            record_id=uuid.uuid4(),
            measured_at=datetime(2026, 8, 31, tzinfo=UTC),
            succeeded=True,
            is_mentioned=True,
            answer_model="answer-model",
        ),),
    )

    baseline = _load_v0_baseline(
        db,
        uuid.uuid4(),
        current_sov_pct=47.0,
        tracking_query_texts=["강남 치질 병원"],
        current_platforms=("chatgpt",),
        current_protocol=protocol,
        current_cells=(current,),
    )

    assert baseline is not None
    response_query_sql = statements[1]
    assert "query_matrix" not in response_query_sql.lower(), (
        "V0 response text must come from its run snapshot, not mutable QueryMatrix rows"
    )


def test_v0_loader_excludes_ambiguous_answers_from_sample_shape():
    run_id = uuid.uuid4()
    query_id = uuid.uuid4()
    protocol = sov_engine.measurement_protocol()
    report = SimpleNamespace(sov_summary={
        "sov_pct": 31.0,
        "baseline_basis": {
            "measurement_run_id": str(run_id),
            "measurement_protocol": protocol,
            "platforms": ["chatgpt"],
            "query_snapshot": [{
                "query_id": str(query_id),
                "query_text": "강남 치질 병원",
                "query_intent": "LOCAL",
            }],
        },
    })
    rows = [
        SimpleNamespace(
            query_id=query_id,
            ai_platform="chatgpt",
            answer_model="answer-model",
            measurement_status="SUCCESS",
            mention_verdict="MATCHED",
            is_mentioned=True,
        ),
        SimpleNamespace(
            query_id=query_id,
            ai_platform="chatgpt",
            answer_model="answer-model",
            measurement_status="SUCCESS",
            mention_verdict="AMBIGUOUS",
            is_mentioned=None,
        ),
    ]
    current = ManifestCellInput(
        query_key="q1",
        query_text="강남 치질 병원",
        platform="chatgpt",
        query_intent="LOCAL",
        state="SUCCESS",
        query_matrix_id=query_id,
        query_target_id=None,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=tuple(
            CellAttempt(
                record_id=uuid.uuid4(),
                measured_at=datetime(2026, 8, 31, tzinfo=UTC),
                succeeded=True,
                is_mentioned=True,
                answer_model="answer-model",
            )
            for _ in range(2)
        ),
    )

    assert _load_v0_baseline(
        _db_with_results(report, rows),
        uuid.uuid4(),
        current_sov_pct=47.0,
        tracking_query_texts=["강남 치질 병원"],
        current_platforms=("chatgpt",),
        current_protocol=protocol,
        current_cells=(current,),
    ) is None


def _db_with_results(report, rows, *, statements=None):
    results = iter((report, rows))

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalars(self):
            return self

        def first(self):
            return self.value

        def all(self):
            return self.value

    def _execute(statement):
        if statements is not None:
            statements.append(str(statement))
        return _Result(next(results))

    return SimpleNamespace(execute=_execute)


def test_v0_reference_requires_the_doctor_headline_to_use_every_current_cell():
    cells = (
        SimpleNamespace(query_key="q1", platform="chatgpt", query_intent="LOCAL", state="SUCCESS"),
        SimpleNamespace(query_key="q2", platform="chatgpt", query_intent="LOCAL", state="SUCCESS"),
    )
    partial = SimpleNamespace(
        comparison=SimpleNamespace(status="COMPARABLE"),
        comparison_cell_keys=frozenset({("q1", "chatgpt")}),
    )
    full = SimpleNamespace(
        comparison=SimpleNamespace(status="COMPARABLE"),
        comparison_cell_keys=frozenset({("q1", "chatgpt"), ("q2", "chatgpt")}),
    )

    assert not _headline_uses_full_current_cohort(partial, cells)
    assert _headline_uses_full_current_cohort(full, cells)
