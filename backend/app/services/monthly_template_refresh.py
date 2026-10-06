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


# 원장용 PDF에서 '숫자 사실'로 읽는 토큰. 문구·쪽 번호·연도·예시 숫자는 제외한다.
_PDF_DELTA = re.compile(r"[+\-−]?\d+(?:\.\d+)?%p")
_PDF_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?%"),
    re.compile(r"\d+편중\d+편"),
    re.compile(r"\d+번중\d+번"),
)
# 옛 템플릿의 고정 문구('공통 눈금 0–100%', '95% 구간')와 새 부록의 설명 예시('6번 중 2번').
OLD_PDF_STATIC_TOKENS = frozenset({"100%", "95%"})
NEW_PDF_STATIC_TOKENS = frozenset({"6번중2번"})


def pdf_fact_tokens(text: str, *, ignore: frozenset[str] = frozenset()) -> dict[str, int]:
    compact = "".join(text.split())
    compact = _PDF_DELTA.sub("", compact)
    counts: dict[str, int] = {}
    for pattern in _PDF_PATTERNS:
        for token in pattern.findall(compact):
            if token not in ignore:
                counts[token] = counts.get(token, 0) + 1
    return counts


def stored_pdf_fact_tokens(sov_summary: Mapping[str, Any] | None) -> frozenset[str]:
    """저장된 측정 요약에서 그대로 옮겨 새 템플릿이 'N번 중 M번'으로 처음 적는 사실.

    옛 템플릿은 반복 관측의 계획·확인 횟수를 다른 꼴로 적어 숫자 사실로 읽히지 않았다.
    새 PDF에만 나타나도 저장값과 정확히 같으면 숫자가 바뀐 것이 아니다.

    답변 비율 범위(ci95)의 양 끝도 같다. 2026-10-06 하루 동안 템플릿이 범위를 '8.2~20.0%'로
    찍어 아래쪽 숫자가 사실로 읽히지 않았다 — 그때 만든 버전을 '8.2%~20.0%'로 다시 찍어도
    저장값 그대로면 차이가 아니다.
    """
    summary = sov_summary or {}
    facts: set[str] = set()
    adequacy = summary.get("observation_adequacy")
    if isinstance(adequacy, Mapping):
        planned, confirmed = adequacy.get("planned_slots"), adequacy.get("confirmed_slots")
        if isinstance(planned, int) and isinstance(confirmed, int):
            facts.add(f"{planned}번중{confirmed}번")
    for key in ("ci95_low", "ci95_high"):
        value = summary.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts.add(f"{value:.1f}%")
    return frozenset(facts)


def compare_doctor_pdf_facts(
    old_text: str, new_text: str, *, stored_facts: frozenset[str] = frozenset()
) -> list[str]:
    """옛 원장 PDF에 있던 숫자 사실이 새 PDF에도 있고, 새 PDF에 없던 숫자가 생기지 않았는가.

    `stored_facts`는 저장값에서 그대로 옮긴 사실이라 한쪽 PDF에만 있어도 차이로 보지 않는다.
    """
    # 옛 PDF가 이미 새 템플릿으로 만든 버전이면 새 부록의 설명 예시도 들어 있다 — 고정 문구는
    # 양쪽에서 똑같이 뺀다(2026-10-02: v2→v3 갱신이 '옛 PDF에만 있음: 6번중2번'으로 막혔다).
    old = set(
        pdf_fact_tokens(old_text, ignore=OLD_PDF_STATIC_TOKENS | NEW_PDF_STATIC_TOKENS)
    )
    new = set(pdf_fact_tokens(new_text, ignore=NEW_PDF_STATIC_TOKENS))
    # 저장값에서 옮긴 사실은 문구가 'N번 중 M번' 꼴을 버려도(2026-10: '150회 전부 확인') 숫자가
    # 바뀐 것이 아니다 — 옛 PDF에만 있어도 차이로 보지 않는다.
    problems = [f"옛 PDF에만 있음: {token}" for token in sorted(old - new - stored_facts)]
    problems.extend(
        f"새 PDF에만 있음: {token}" for token in sorted(new - old - stored_facts)
    )
    return problems


def doctor_view_expectations(
    view: Mapping[str, Any],
    *,
    sov_summary: Mapping[str, Any],
    content_summary: Mapping[str, Any],
) -> list[str]:
    """원장 뷰의 핵심 숫자를 저장된 요약에서 독립적으로 다시 계산해 맞춰 본다."""
    problems: list[str] = []
    narrative = view["narrative"]
    comparison = sov_summary.get("comparison") or {}
    stored_current = sov_summary.get("sov_pct")
    comparable = narrative.previous is not None
    expected_current = comparison.get("current_sov_pct") if comparable else stored_current
    if narrative.current != expected_current:
        problems.append(f"narrative.current {narrative.current} ≠ {expected_current}")
    if comparable and narrative.previous != comparison.get("prior_sov_pct"):
        problems.append(
            f"narrative.previous {narrative.previous} ≠ {comparison.get('prior_sov_pct')}"
        )
    operations = content_summary.get("operations") or {}
    timing = content_summary.get("contract_timing") or {}
    quota = operations.get("plan_quota")
    tile_value = view["tiles"][0]["value"]
    expected_tile = (
        f"{content_summary.get('published_count')}편"
        if quota is None
        else f"{quota}편 중 {timing.get('published_for_contract_count')}편"
    )
    if tile_value != expected_tile:
        problems.append(f"tile {tile_value} ≠ {expected_tile}")
    return problems
