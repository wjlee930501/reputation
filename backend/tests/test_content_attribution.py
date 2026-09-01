"""Frozen-cell attribution classification and related-content matching."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.monthly_sov_types import CellAttempt, ManifestCellInput
from app.services.report_attribution import (
    ContentAttributionInput,
    build_content_attribution_summary,
)


def _cell(
    query_key: str,
    *,
    mentioned: bool = False,
    platform: str = "chatgpt",
    state: str = "SUCCESS",
    target_id: uuid.UUID | None = None,
    query_text: str | None = None,
    has_success: bool = True,
    keep_attempt_on_terminal_state: bool = False,
) -> ManifestCellInput:
    attempts = ()
    if has_success and (state == "SUCCESS" or keep_attempt_on_terminal_state):
        attempts = (
            CellAttempt(
                record_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{query_key}:{platform}"),
                measured_at=datetime(2026, 8, 1, tzinfo=UTC),
                succeeded=True,
                is_mentioned=mentioned,
            ),
        )
    return ManifestCellInput(
        query_key=query_key,
        query_text=query_text or f"환자 질문 {query_key}",
        platform=platform,
        query_intent="LOCAL",
        state=state,
        query_matrix_id=None,
        query_target_id=target_id,
        query_variant_id=None,
        query_intent_source="FROZEN",
        attempts=attempts,
    )


def _content(content_type, title, *, target_id=None):
    return SimpleNamespace(content_type=content_type, title=title, query_target_id=target_id)


def _summary(*, current=(), prior=None, contents=()):
    return build_content_attribution_summary(
        ContentAttributionInput(
            published_contents=list(contents),
            prev_published_contents=[],
            current_cells=tuple(current),
            prior_cells=None if prior is None else tuple(prior),
            sov_pct=100.0,
            prev_sov_pct=0.0,
            change_pct=100.0,
        )
    )


# ── 유형별 편수 ───────────────────────────────────────────────
def test_content_type_counts_covers_all_seven_types():
    contents = [
        _content("FAQ", "q1"),
        _content("FAQ", "q2"),
        _content("DISEASE", "d1"),
        _content("LOCAL", "l1"),
    ]
    summary = build_content_attribution_summary(
        ContentAttributionInput(
            published_contents=contents,
            prev_published_contents=[],
            current_cells=(),
            prior_cells=None,
            sov_pct=10.0,
            prev_sov_pct=None,
            change_pct=None,
        )
    )
    counts = summary["content_type_counts"]
    assert counts == {
        "FAQ": 2, "DISEASE": 1, "TREATMENT": 0, "COLUMN": 0,
        "HEALTH": 0, "LOCAL": 1, "NOTICE": 0,
    }
    assert summary["published_count"] == 4


# ── 고정 셀 기준 언급 변화 판정 ─────────────────────────────────
@pytest.mark.parametrize(
    ("prior", "expected_counts"),
    [
        ([_cell("A", mentioned=False)], (1, 0, 0)),
        (None, (0, 1, 0)),
        ([_cell("A", state="FAILED")], (0, 0, 1)),
        ([_cell("A", state="EXCLUDED")], (0, 0, 1)),
        ([_cell("A", state="FAILED", keep_attempt_on_terminal_state=True)], (0, 0, 1)),
        ([_cell("A", state="EXCLUDED", keep_attempt_on_terminal_state=True)], (0, 0, 1)),
        ([_cell("A", state="SUCCESS", has_success=False)], (0, 0, 1)),
        ([_cell("A", mentioned=True)], (0, 0, 0)),
    ],
)
def test_attribution_requires_a_successful_unmentioned_prior_cell(prior, expected_counts):
    summary = _summary(current=[_cell("A", mentioned=True)], prior=prior)

    assert (
        summary["new_mention_count"],
        summary["first_measured_mention_count"],
        summary["non_comparable_count"],
    ) == expected_counts


def test_fixed_cell_identity_keeps_platform_outcomes_separate():
    current = [
        _cell("A", platform="chatgpt", mentioned=True),
        _cell("A", platform="gemini", mentioned=True),
    ]
    prior = [_cell("A", platform="chatgpt", mentioned=False)]

    summary = _summary(current=current, prior=prior)

    assert summary["new_mention_count"] == 1
    assert summary["first_measured_mention_count"] == 1
    assert summary["new_mention_cells"][0]["classification"] == "NEW_MENTION"
    assert (
        summary["first_measured_mention_cells"][0]["classification"]
        == "FIRST_MEASURED_MENTION"
    )
    assert summary["new_mention_cells"][0]["platform_label"] == "ChatGPT"
    assert summary["first_measured_mention_cells"][0]["platform_label"] == "Gemini"


@pytest.mark.parametrize("state", ["FAILED", "EXCLUDED"])
def test_current_terminal_state_wins_over_a_successful_looking_attempt(state):
    current = [_cell(
        "A", mentioned=True, state=state, keep_attempt_on_terminal_state=True
    )]

    summary = _summary(current=current, prior=[_cell("A", mentioned=False)])

    assert summary["new_mention_count"] == 0
    assert summary["first_measured_mention_count"] == 0
    assert summary["non_comparable_count"] == 0


def test_counts_all_cells_before_capping_visible_rows():
    current = [_cell(f"Q{i}", mentioned=True) for i in range(8)]
    prior = [_cell(f"Q{i}", mentioned=False) for i in range(8)]

    summary = _summary(current=current, prior=prior)

    assert summary["new_mention_count"] == 8
    assert len(summary["new_mention_cells"]) == 5


# ── 연관 콘텐츠 연결 ──────────────────────────────────────────
def test_related_content_via_query_target_link():
    target_id = uuid.uuid4()
    current = [_cell("A", mentioned=True, target_id=target_id, query_text="강남 치질")]
    prior = [_cell("A", mentioned=False, target_id=target_id, query_text="강남 치질")]
    contents = [
        _content("FAQ", "치질 자가진단 FAQ", target_id=target_id),
        _content("DISEASE", "무관한 글", target_id=uuid.uuid4()),
    ]
    summary = _summary(current=current, prior=prior, contents=contents)
    assert summary["new_mention_cells"][0]["related_contents"] == ["치질 자가진단 FAQ"]


def test_related_content_keyword_fallback_when_no_link():
    current = [_cell("A", mentioned=True, query_text="탈장 수술 회복")]
    prior = [_cell("A", mentioned=False, query_text="탈장 수술 회복")]
    contents = [_content("TREATMENT", "탈장 수술 후 회복 안내")]
    summary = _summary(current=current, prior=prior, contents=contents)
    assert summary["new_mention_cells"][0]["related_contents"] == ["탈장 수술 후 회복 안내"]


def test_related_content_empty_when_nothing_matches():
    current = [_cell("A", mentioned=True, query_text="갑상선 초음파")]
    prior = [_cell("A", mentioned=False, query_text="갑상선 초음파")]
    contents = [_content("FAQ", "치질 FAQ")]
    summary = _summary(current=current, prior=prior, contents=contents)
    assert summary["new_mention_cells"][0]["related_contents"] == []


def test_new_mention_now_means_appeared_at_least_once_in_the_repeats():
    """반복 측정을 쓰면서 "새로 나온 질문"의 뜻도 명시된다.

    대표 응답은 언급된 시도를 먼저 고르므로, 이번 달 5회 중 1회라도 나왔고
    지난달에는 한 번도 안 나온 셀이 "새로 확인된 질문"이 된다. 예전에는
    무작위로 뽑힌 1건이 그 판정을 좌우했다(같은 달을 두 번 계산하면 결과가 달랐다).
    """
    current = [
        ManifestCellInput(
            query_key="A",
            query_text="강남 치질 병원",
            platform="chatgpt",
            query_intent="LOCAL",
            state="SUCCESS",
            query_matrix_id=None,
            query_target_id=None,
            query_variant_id=None,
            query_intent_source="FROZEN",
            attempts=tuple(
                CellAttempt(
                    record_id=uuid.UUID(int=index + 1),
                    measured_at=datetime(2026, 8, 1, tzinfo=UTC),
                    succeeded=True,
                    is_mentioned=index == 4,
                )
                for index in range(5)
            ),
        )
    ]
    prior = [_cell("A", mentioned=False, query_text="강남 치질 병원")]

    summary = _summary(current=current, prior=prior)

    assert summary["new_mention_count"] == 1
