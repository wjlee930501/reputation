"""v4 원장 보고서의 쪽 넘김 고아·과부 정리(2026-11-05).

WeasyPrint 69는 표 행의 `break-after/before:avoid`를 들쭉날쭉 지킨다 — 행이 한 줄이면 앞 행의 avoid를 따라 머리 행과
첫 행들을 다음 쪽으로 보내지만, 두 줄짜리 칸이 있는 행이나 `break-inside:avoid` 행 앞에서는 그 행 바로 앞에서 끊어
고아 행(쪽 경계 한쪽에 1~2행)을 남긴다(scratchpad brkprobe3·5 실측, 10월 운영분 행복드림·강심장·장앤김·장보고).
그래서 CSS avoid는 첫 시도일 뿐이고, 여기서 실제 레이아웃을 읽어 **결정적으로** 끊을 자리를 정한다:

- 흐르는 표(`FLOW_TABLES`)와 대신 언급 원장(`.replaced-row`)의 쪽별 조각이 `MIN_ROWS`(3) 미만이면 —
  첫 조각이면 표가 든 모듈을 새 쪽에서 시작하고, 뒤 조각이면 앞 조각의 끝 행들을 함께 넘겨 3행을 채운다
  (앞 조각이 3행 아래로 내려가면 앞 조각 전체를 넘긴다 → 결국 모듈 전체).
- 표 뒤의 마무리(검정 면·시사점·인용 줄)가 행 하나 없는 쪽에 홀로 서면 길이 0인 조각으로 보고 같은 규칙을 쓴다.

렌더러가 `plan_fixups`가 None을 돌려줄 때까지(최대 `MAX_PASSES`) 다시 그린다. 강제 넘김은 템플릿이 행·모듈에 붙이는
`page-start` 클래스(`break-before:page`)뿐이라 WeasyPrint가 확실히 지키고, 머리 행은 새 쪽에 다시 찍힌다.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

MIN_ROWS = 3
MAX_PASSES = 6
FLOW_TABLES: tuple[str, ...] = (
    "p2-table",
    "p3-heat",
    "p4-table",
    "p6-table",
    "appendix-table",
    "appendix-plan-table",
)
LEDGER = "p4-ledger"
CLOSER_CLASSES = frozenset({"figure-band", "insight-panel", "closing"})


@dataclass(frozen=True)
class PageBreaks:
    """템플릿에 넘기는 강제 넘김 — 표 id → 새 쪽에서 시작할 본문 행 번호, 모듈 전체를 넘길 표 id."""

    before_table: frozenset[str] = frozenset()
    before_row: Mapping[str, frozenset[int]] = field(default_factory=dict)

    def starts_page(self, table_id: str, row_index: int) -> bool:
        return row_index in self.before_row.get(table_id, frozenset())

    def table_starts_page(self, table_id: str) -> bool:
        return table_id in self.before_table

    def is_empty(self) -> bool:
        return not self.before_table and not any(self.before_row.values())


def _walk(box: Any) -> Iterator[Any]:
    yield box
    for child in getattr(box, "children", ()):
        yield from _walk(child)


def _classes(element: Any) -> set[str]:
    return set((element.get("class") or "").split()) if element is not None else set()


def _page_fragments(page: Any) -> tuple[dict[str, list[int]], bool, bool]:
    """쪽 하나의 (표 id → 본문 행 번호들, 마무리 요소 있음, 제목(h1) 있음)."""
    rows: dict[str, list[int]] = {}
    closer = False
    heading = False
    seen_rows: set[tuple[str, int]] = set()
    for box in _walk(page._page_box):
        element = getattr(box, "element", None)
        if element is None:
            continue
        kind = type(box).__name__
        if kind == "TableRowBox" and element.tag == "tr" and element.get("data-table"):
            key = (element.get("data-table"), int(element.get("data-row")))
            if key not in seen_rows:  # 같은 행 상자가 포함 관계로 두 번 걸리지 않게
                seen_rows.add(key)
                rows.setdefault(key[0], []).append(key[1])
        elif kind == "BlockBox" and element.tag == "div" and element.get("data-table") == LEDGER:
            key = (LEDGER, int(element.get("data-row")))
            if key not in seen_rows:
                seen_rows.add(key)
                rows.setdefault(LEDGER, []).append(key[1])
        elif element.tag == "h1":
            heading = True
        elif element.tag == "div" and (_classes(element) & CLOSER_CLASSES):
            closer = True
    return {k: sorted(v) for k, v in rows.items()}, closer, heading


def fragments(document: Any) -> dict[str, list[list[int]]]:
    """표 id → 쪽 순서대로의 조각(행 번호 목록). 마무리 요소가 행 없이 홀로 선 쪽은 직전 표의 빈 조각 `[]`로 붙인다."""
    out: dict[str, list[list[int]]] = {}
    last_table: str | None = None
    for page in document.pages:
        rows, closer, heading = _page_fragments(page)
        if rows:
            for table_id, indexes in rows.items():
                out.setdefault(table_id, []).append(indexes)
            # 쪽의 마지막 표 = 문서 순서상 가장 뒤 행을 가진 표(쪽 안 표 순서는 FLOW_TABLES·LEDGER 순서를 따른다).
            order = (*FLOW_TABLES, LEDGER)
            last_table = max(rows, key=lambda t: order.index(t) if t in order else -1)
        elif closer and not heading and last_table is not None:
            out.setdefault(last_table, []).append([])
            last_table = None
        else:
            last_table = None
    return out


def plan_fixups(document: Any, current: PageBreaks) -> PageBreaks | None:
    """고아 조각이 있으면 고친 강제 넘김을, 없으면 None을 돌려준다. 강제 넘김은 누적만 된다(결정적·단조)."""
    before_table = set(current.before_table)
    before_row: dict[str, set[int]] = {k: set(v) for k, v in current.before_row.items()}
    changed = False
    for table_id, parts in fragments(document).items():
        if len(parts) < 2:
            continue
        for index, part in enumerate(parts):
            if len(part) >= MIN_ROWS:
                continue
            if index == 0:
                # 첫 조각이 짧으면 모듈째 새 쪽으로. 부록 표는 부록 첫 쪽(마스트헤드·제목 뒤)에서 시작하므로 넘기면
                # 마스트헤드만 남은 빈 쪽이 생긴다(검증기 DOCTOR_PDF_PAGE_EMPTY) — 고치지 않는다.
                if table_id != "appendix-table" and table_id not in before_table:
                    before_table.add(table_id)
                    changed = True
                break
            previous = parts[index - 1]
            if not previous:
                break
            need = MIN_ROWS - len(part)
            if len(previous) - need >= MIN_ROWS:
                start = previous[len(previous) - need]
            elif index - 1 == 0:
                if table_id not in before_table:
                    before_table.add(table_id)
                    changed = True
                break
            else:
                start = previous[0]
            rows = before_row.setdefault(table_id, set())
            if start not in rows:
                rows.add(start)
                changed = True
            elif table_id != "appendix-table" and table_id not in before_table:
                # 이미 그 자리에 강제 넘김을 넣었는데도 고아가 남았다 — 모듈째 넘겨 한 단계 올린다(정체 방지).
                before_table.add(table_id)
                changed = True
            break  # 한 표는 한 번에 한 자리만 고치고 다시 그린다 — 뒤 조각은 새 레이아웃에서 다시 본다
    if not changed:
        return None
    return PageBreaks(
        before_table=frozenset(before_table),
        before_row={k: frozenset(v) for k, v in before_row.items() if v},
    )


def orphan_fragments(document: Any) -> dict[str, list[list[int]]]:
    """검사용: 조각이 둘 이상인데 3행 미만인 조각이 있는 표만."""
    return {
        table_id: parts
        for table_id, parts in fragments(document).items()
        if len(parts) > 1 and any(len(part) < MIN_ROWS for part in parts)
    }
