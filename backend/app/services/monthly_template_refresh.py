"""월간 리포트 '문구·디자인만' 다시 만들기(TEMPLATE_REFRESH)의 비교 규칙.

새 원장용 템플릿을 배포한 뒤 이미 만든 달의 리포트를 새 버전으로 다시 찍을 때, 숫자는
처음 만든 그대로여야 한다. 일반 재생성(MANUAL_REBUILD)은 지금 시각을 마감으로 삼고
현재 행(발행 상태·노출 행동·자료 처리)을 다시 읽으므로 숫자가 흔들린다. 템플릿 갱신은
대체할 버전에 저장된 요약을 그대로 옮기고, 이 모듈의 비교가 하나라도 어긋나면 만들지 않는다.

여기에는 DB·저장소·공급자 호출이 없다. 계획 생성은 `app.workers.tasks`가, 운영 명령은
`app.utils.monthly_template_refresh`가 맡는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any

# 템플릿 갱신이 저장된 값만으로 원장·AE 리포트를 다시 그리려면 반드시 있어야 하는 칸.
REQUIRED_CONTENT_SUMMARY_PATHS: tuple[tuple[str, ...], ...] = (
    ("published_count",),
    ("operations",),
    ("operations", "plan_quota"),
    ("operations", "supplementary_count"),
    ("contract_timing",),
    ("contract_timing", "early_publication_count"),
    ("contract_timing", "late_recovery_count"),
    ("contract_timing", "published_for_contract_count"),
    ("contract_timing", "observed_at"),
    ("attribution",),
    ("strategy",),
    ("citations",),
    ("talking_points",),
)
REQUIRED_SOV_SUMMARY_PATHS: tuple[tuple[str, ...], ...] = (
    ("sov_pct",),
    ("comparison",),
    ("comparison", "reason"),
)
IN_FLIGHT_OPERATION_TYPES = ("GENERATE_MONTHLY_REPORT", "SCHEDULED_MONTHLY_REPORT", "RUN_SOV")
IN_FLIGHT_STATES = ("REQUESTED", "QUEUED", "RUNNING")

_MISSING = object()


@dataclass(frozen=True, slots=True)
class RefreshFinding:
    """비교 한 줄. kind는 BLOCKER(만들 수 없음)·DIFF(숫자 불일치)·WARN(출력 숫자와 무관)."""

    kind: str
    code: str
    detail: str = ""


@dataclass(slots=True)
class RefreshVerdict:
    findings: list[RefreshFinding] = field(default_factory=list)

    def add(self, kind: str, code: str, detail: str = "") -> None:
        self.findings.append(RefreshFinding(kind, code, detail))

    @property
    def status(self) -> str:
        kinds = {finding.kind for finding in self.findings}
        if "BLOCKER" in kinds:
            return "BLOCKED"
        if "DIFF" in kinds:
            return "DIFF"
        return "PASS"

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def summary(self) -> str:
        return "; ".join(
            f"{finding.kind}:{finding.code}{'(' + finding.detail + ')' if finding.detail else ''}"
            for finding in self.findings
        )


class TemplateRefreshRefused(RuntimeError):
    """숫자를 지킬 수 없어 템플릿 갱신을 만들지 않았다."""

    def __init__(self, verdict: RefreshVerdict) -> None:
        super().__init__(f"template refresh refused: {verdict.status} {verdict.summary()}")
        self.verdict = verdict


def _dig(value: Any, path: Iterable[str]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def missing_stored_paths(
    content_summary: Any, sov_summary: Any
) -> list[str]:
    missing = [
        "content_summary." + ".".join(path)
        for path in REQUIRED_CONTENT_SUMMARY_PATHS
        if _dig(content_summary, path) is _MISSING
    ]
    missing.extend(
        "sov_summary." + ".".join(path)
        for path in REQUIRED_SOV_SUMMARY_PATHS
        if _dig(sov_summary, path) is _MISSING
    )
    return missing


def stored_observed_at(content_summary: Mapping[str, Any]) -> datetime:
    raw = _dig(content_summary, ("contract_timing", "observed_at"))
    if not isinstance(raw, str):
        raise ValueError("stored observed_at is missing")
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("stored observed_at has no timezone")
    return value


def _json_normal(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def numeric_leaves(value: Any, prefix: str = "") -> dict[str, float]:
    """JSON 값 안의 모든 숫자를 경로와 함께 꺼낸다(bool은 숫자가 아니다)."""
    leaves: dict[str, float] = {}
    normal = _json_normal(value)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            leaves[path] = float(node)
        elif isinstance(node, Mapping):
            for key in sorted(node):
                walk(node[key], f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(normal, prefix)
    return leaves


def numeric_diff(expected: Any, actual: Any, *, prefix: str = "") -> list[str]:
    """두 JSON 값의 숫자 칸을 비교한다. 한쪽에만 있는 숫자 칸도 차이로 본다."""
    left = numeric_leaves(expected, prefix)
    right = numeric_leaves(actual, prefix)
    paths = sorted(set(left) | set(right))
    return [
        f"{path}: {left.get(path, 'missing')} → {right.get(path, 'missing')}"
        for path in paths
        if left.get(path) != right.get(path)
    ]


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def number_tokens(lines: Iterable[str]) -> list[str]:
    """문장 속 숫자를 순서대로. 문구가 바뀌어도 숫자는 같아야 하는 문장에 쓴다."""
    return [token for line in lines for token in _NUMBER.findall(str(line))]


# ── 원장 뷰의 숫자 사실(구조 비교) ─────────────────────────────────────────────
# 원장 PDF 본문에서 정규식으로 숫자를 뽑아 옛 PDF와 집합으로 비교하지 않는다. 문구만 바뀌어도
# 사실이 달라 보였다(2026-10-06: '16.7~30.0%'→'16.7%~30.0%', '150번 중 150번'→'150회 전부').
# 대신 템플릿이 그리는 값(원장 뷰)을 키별 '정본 사실'로 뽑아, 대체할 버전의 저장 요약에서 같은
# 방식으로 다시 만든 사실과 키 단위로 대조한다. PDF 본문은 그 사실이 실제로 찍혔는지만 보는
# 이차 확인이며, 옛 PDF 본문은 비교하지 않는다.

# 저장 요약에 근거가 반드시 있어야 하는 뷰 사실 — 근거 없이 뷰에만 생기면 숫자가 새로 생긴 것이다.
# 지난달 참고 값(`rate.reference_previous`)은 저장 요약이 아니라 지난달 보고서가 근거라 호출부가 따로 맞춘다.
REQUIRE_STORED_BACKING = frozenset({"rate.current", "rate.previous", "tile.contract"})
# 현재 템플릿(doctor_report_v3)이 PDF에 그리지 않는 사실 — 뷰·저장값 대조에만 쓴다.
_NOT_IN_PDF = frozenset({"tile.chatgpt", "tile.gemini", "highlight.published_this_month"})
_HIGHLIGHT_NAMES = (
    "measured_questions",
    "mentioned_questions",
    "published_this_month",
    "cumulative_published",
    "cited_questions",
    "cited_answers_measured",
)
_PDF_WINDOW = 40


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return float(value)


def _pct(value: Any) -> str | None:
    number = _number(value)
    return None if number is None else f"{number:.1f}%"


def _comparable(sov_summary: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """지난달과 직접 비교하는 달이면 저장된 comparison. 서술(narrative)의 판정과 같은 규칙이다."""
    comparison = sov_summary.get("comparison")
    if not isinstance(comparison, Mapping):
        return None
    values = (comparison.get("prior_sov_pct"), comparison.get("current_sov_pct"))
    matched = comparison.get("matched_cell_count", 0)
    if (
        comparison.get("status") == "COMPARABLE"
        and comparison.get("reason") == "MATCHED_COHORT"
        and isinstance(matched, (int, float))
        and matched > 0
        and all(_number(value) is not None and 0 <= value <= 100 for value in values)
    ):
        return comparison
    return None


def _highlight_facts(highlights: Mapping[str, Any]) -> dict[str, str]:
    return {
        f"highlight.{name}": str(highlights[name])
        for name in _HIGHLIGHT_NAMES
        if isinstance(highlights.get(name), int) and not isinstance(highlights.get(name), bool)
    }


def _appendix_facts(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    facts: dict[str, str] = {}
    for index, row in enumerate(rows):
        for field_name, short in (("prev_label", "prev"), ("current_label", "current")):
            label = str(row.get(field_name) or "")
            if _NUMBER.search(label):
                facts[f"appendix.{index}.{short}"] = label
    return facts


def doctor_view_facts(view: Mapping[str, Any]) -> dict[str, str]:
    """원장 뷰(템플릿이 그리는 값)의 숫자 사실. 모르는 값(None)은 0과 섞지 않고 뺀다."""
    facts: dict[str, str] = {}
    narrative = view["narrative"]
    for key, value in (
        ("rate.current", narrative.current),
        ("rate.previous", narrative.previous),
        ("rate.reference_previous", narrative.reference_previous),
    ):
        text = _pct(value)
        if text is not None:
            facts[key] = text
    tiles = view["tiles"]
    facts["tile.contract"] = str(tiles[0]["value"])
    for key, tile in zip(("tile.chatgpt", "tile.gemini"), tiles[1:3], strict=False):
        facts[key] = str(tile["value"])
    facts.update(_highlight_facts(view.get("highlights") or {}))
    facts.update(_appendix_facts(view.get("appendix_rows") or []))
    baseline = view.get("v0_baseline")
    if baseline:
        facts["v0.of_hundred"] = f"{baseline['of_hundred']}%"
        facts["v0.current_of_hundred"] = f"{baseline['current_of_hundred']}%"
    return facts


def stored_doctor_facts(
    sov_summary: Mapping[str, Any], content_summary: Mapping[str, Any]
) -> dict[str, str]:
    """저장된 요약에서 원장 뷰와 같은 키로 다시 만든 사실(뷰가 만들 수 있는 사실만).

    뷰 빌더와 같은 함수(`_director_highlights`·`_appendix_rows`)에 저장된 귀속·인용을 먹여
    독립적으로 계산한다. 누적 발행 편수·초기 측정 참고선·지난달 참고 값은 저장 요약에 없으므로
    여기에 없다(뒤의 둘은 호출부가 따로 근거를 댄다).
    """
    from app.services.report_engine import _appendix_rows, _director_highlights

    facts: dict[str, str] = {}
    comparison = _comparable(sov_summary)
    current = comparison["current_sov_pct"] if comparison else sov_summary.get("sov_pct")
    for key, value in (
        ("rate.current", current),
        ("rate.previous", comparison["prior_sov_pct"] if comparison else None),
    ):
        text = _pct(value)
        if text is not None:
            facts[key] = text
    operations = content_summary.get("operations") or {}
    timing = content_summary.get("contract_timing") or {}
    quota = operations.get("plan_quota")
    facts["tile.contract"] = (
        f"{content_summary.get('published_count')}편"
        if quota is None
        else f"{quota}편 중 {timing.get('published_for_contract_count')}편"
    )
    platforms = {
        row.get("platform"): row
        for row in sov_summary.get("platforms") or []
        if isinstance(row, Mapping)
    }
    for platform_id in ("chatgpt", "gemini"):
        row = platforms.get(platform_id, {})
        rate = row.get("mention_rate")
        attempts = int(row.get("attempts_used") or 0)
        facts[f"tile.{platform_id}"] = (
            f"{rate:.1f}%" if rate is not None and attempts else "측정 미완료"
        )
    attribution = content_summary.get("attribution") or {}
    facts.update(
        _highlight_facts(
            _director_highlights(
                attribution=attribution,
                citations=content_summary.get("citations"),
                published_count=content_summary.get("published_count"),
                cumulative_published_count=None,
            )
        )
    )
    facts.update(
        _appendix_facts(
            _appendix_rows(
                attribution.get("question_rows") or [],
                has_prior_month=bool(attribution.get("has_prior_month")),
                competitors={},
                cited_titles={},
            )
        )
    )
    return facts


def stored_only_facts(sov_summary: Mapping[str, Any]) -> dict[str, str]:
    """저장된 측정 요약에만 있고 뷰의 값이 아닌 사실(답변 비율 범위·확인 횟수) — PDF 이차 확인용."""
    facts: dict[str, str] = {}
    for key, name in (("ci.low", "ci95_low"), ("ci.high", "ci95_high")):
        text = _pct(sov_summary.get(name))
        if text is not None:
            facts[key] = text
    adequacy = sov_summary.get("observation_adequacy")
    for key, source in (
        ("adequacy.planned_slots", adequacy),
        ("adequacy.confirmed_slots", adequacy),
        ("coverage.planned_count", sov_summary),
        ("coverage.success_count", sov_summary),
    ):
        name = key.split(".", 1)[1]
        value = source.get(name) if isinstance(source, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool):
            facts[key] = str(value)
    comparison = sov_summary.get("comparison")
    if isinstance(comparison, Mapping) and comparison.get("status") == "COMPARABLE":
        text = _pct(sov_summary.get("sov_pct_all_cells"))
        if text is not None:
            facts["coverage.sov_pct_all_cells"] = text
    return facts


def compare_doctor_facts(
    expected: Mapping[str, str],
    actual: Mapping[str, str],
    *,
    require_backing: frozenset[str] = REQUIRE_STORED_BACKING,
) -> list[str]:
    """저장값에서 다시 만든 사실(expected)과 원장 뷰의 사실(actual)을 키 단위로 대조한다.

    저장 사실이 뷰에서 빠지거나 값이 다르면 차이다. 뷰에만 있는 사실은 `require_backing`에 든
    키만 차이로 본다(누적 발행 편수·초기 측정 참고선은 현재 행에서 읽는 값이다).
    """
    problems = [
        (
            f"원장 뷰에 없음: {key}={expected[key]}"
            if key not in actual
            else f"{key}: 저장 {expected[key]} ≠ 뷰 {actual[key]}"
        )
        for key in sorted(expected)
        if actual.get(key) != expected[key]
    ]
    problems.extend(
        f"저장값 근거 없음: {key}={actual[key]}"
        for key in sorted(set(actual) - set(expected))
        if key in require_backing
    )
    return problems


def _pdf_number_pattern(value: str) -> re.Pattern[str] | None:
    numbers = _NUMBER.findall(value)
    if not numbers:
        return None
    # 숫자는 앞뒤가 숫자·소수점으로 이어지지 않는 온전한 값이어야 하고, 사실에 숫자가 여럿이면
    # ('12편 중 11편') 같은 순서로 가까이 나와야 한다. '%'·단위·조사 같은 문구는 보지 않는다.
    guarded = [rf"(?<![\d.]){re.escape(number)}(?!\d|\.\d)" for number in numbers]
    return re.compile(rf"[\s\S]{{0,{_PDF_WINDOW}}}?".join(guarded))


def pdf_fact_problems(pdf_text: str, facts: Mapping[str, str]) -> list[str]:
    """정본 사실의 값이 새 PDF 본문에 모두 찍혔는가(이차 확인). 서식·문구 변화에는 관대하다."""
    compact = "".join(pdf_text.split())
    problems: list[str] = []
    for key in sorted(facts):
        if key in _NOT_IN_PDF:
            continue
        # 템플릿은 질문 수가 0이거나 모를 때 '확인 못 함'만 적고 숫자를 그리지 않는다.
        if key == "highlight.mentioned_questions" and facts.get(
            "highlight.measured_questions", "0"
        ) == "0":
            continue
        if key == "highlight.cited_answers_measured" and facts[key] == "0":
            continue
        pattern = _pdf_number_pattern(facts[key])
        if pattern is not None and pattern.search(compact) is None:
            problems.append(f"새 PDF에서 찾지 못함: {key}={facts[key]}")
    return problems


def doctor_view_expectations(
    view: Mapping[str, Any],
    *,
    sov_summary: Mapping[str, Any],
    content_summary: Mapping[str, Any],
) -> list[str]:
    """원장 뷰의 숫자 사실을 저장된 요약에서 독립적으로 다시 계산해 키 단위로 맞춰 본다."""
    return compare_doctor_facts(
        stored_doctor_facts(sov_summary, content_summary), doctor_view_facts(view)
    )
