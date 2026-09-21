"""Report labels must describe the actual production estimator and lineage."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.services.monthly_sov import build_monthly_sov
from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.report_narrative import build_monthly_narrative
from app.services.report_typography import keep_korean_words


def _cell(number: int, outcomes: tuple[bool, ...]) -> ManifestCellInput:
    return ManifestCellInput(
        query_key=f"q-{number}", query_text=f"가상동 진료 질문 {number}",
        platform="chatgpt", query_intent="LOCAL", state="SUCCESS",
        query_matrix_id=None, query_target_id=None, query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=tuple(CellAttempt(
            record_id=UUID(int=number * 100 + index),
            measured_at=datetime(2026, 9, 1, tzinfo=UTC),
            succeeded=True, is_mentioned=mentioned, answer_model="fixture",
        ) for index, mentioned in enumerate(outcomes)),
    )


def _narrative(cells):
    summary = build_monthly_sov(cells, ("chatgpt",))
    return build_monthly_narrative(
        kind="MONTHLY", coverage=summary.to_payload(), attribution=None,
        citations=None, works=(), current=summary.sov_pct, previous=None,
        comparison_reason=summary.comparison.reason, shortfall=0,
    )


def test_unequal_repeats_use_pooled_headline_not_cell_average():
    narrative = _narrative((_cell(1, (True,)), _cell(2, (False,) * 5)))
    assert narrative.current == 16.67
    assert "확정 반복 6회 중 언급 1회" in narrative.denominator
    assert "합산한 언급 비율" in narrative.denominator
    assert "조합별 언급 비율 평균" not in narrative.denominator
    assert "질문별 평균 50.0%" in narrative.platform_details[0]


@pytest.mark.parametrize("mixed", [False, True])
def test_missing_slot_lineage_does_not_become_zero_or_a_total(mixed):
    first = _cell(1, (True,))
    if mixed:
        first = replace(first, slot_lineage="SLOTTED", planned_repeat_count=1,
                        received_answer_count=1, confirmed_slot_count=1)
    narrative = _narrative((first, _cell(2, (False,) * 5)))
    assert "반복 관측 슬롯: 계획 0회" not in " ".join(narrative.methods)
    assert "일부만 확인 가능" in narrative.methods[-1] if mixed else "기록 미확인" in narrative.methods[-1]


def test_limited_slot_status_is_explained_as_partial():
    cell = replace(_cell(1, (True,)), slot_lineage="SLOTTED",
                   planned_repeat_count=3, received_answer_count=1,
                   confirmed_slot_count=1, pending_slot_count=2)
    narrative = _narrative((cell,))
    assert "상태 일부 확정" in narrative.methods[-1]
    assert "계획 3회 / 확정 1회" in narrative.methods[-1]


def test_table_clinic_names_keep_wrap_opportunities():
    word = "서울한마음가정의학과의원"
    html = keep_korean_words(f"<p>{word}</p><table><tr><td>{word}</td></tr></table>")
    before, table = html.split("<table>", 1)
    assert "white-space:nowrap" in before
    assert "white-space:nowrap" not in table
    assert word in table
