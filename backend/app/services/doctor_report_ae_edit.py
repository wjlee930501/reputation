"""원장용 월간 보고서 v4 — AE 수정 판(보고서의 모든 글자를 고치거나 지우고, 숫자도 바꾼다).

대표 결정(2026-11-05 오후): "보고서 파일은 AE들이 직접 수정할 수 있어야 한다" — 원장 PDF v4에 찍히는 모든 글자
(뷰 모델의 문장·라벨·표의 칸, 템플릿에 박혀 있던 소제목·캡션·머리글)를 AE가 고치거나 지울 수 있고, 숫자도 자유롭게
바꾼다. 적용 규칙은 `doctor_report_v4.apply_ae_overrides` 하나이고, 이 모듈은 그 주변 — 경로·형·길이·금지 표현·
내부 표식 검증, 빈 절(빈 쪽) 거절, 편집 항목 추출(보고서 순서), 측정값과 다른 숫자의 기록, 이전 버전 수정 값의
승계 규칙 — 을 맡는다. DB·저장소·공급자 호출이 없다.

저장 위치: `MonthlyReport.content_summary.report_snapshot.render_inputs.ae_overrides`
    {schema_version, edited_by, edited_at, fields: {경로: 값}, base_view: pristine v4 뷰, history: [...],
     number_changes: [{path, before, after, edited_by, edited_at}], fact_changes: {사실 키: {before, after}}}
`doctor_view`·`doctor_metric_facts`는 수정을 적용한(실제로 찍은) 뷰 기준이라 검증·대조·전달 경로의 독자는 바뀌지
않는다. `base_view`는 다음 버전이 같은 수정을 pristine 뷰에 다시 적용하고, 화면이 원문을 보여 주는 근거다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.doctor_report_v4 import (
    AE_FIELD_CARD_NAMES,
    AE_FIELD_CASE_INSIGHT,
    AE_FIELD_CASE_RECORD,
    AE_FIELD_KEY_LINE,
    AE_FIELD_PLAN_ACTION,
    AE_FIELD_TREATMENT_LABEL,
    AE_FIELD_VIEW,
    AE_OVERRIDES_SCHEMA_VERSION,
    AE_PATH_CARD_NAMES,
    CLASS_LABEL,
    CLASS_STATIC,
    HEAT_STATES,
    T_COUNT,
    T_INT,
    T_LABEL,
    T_LIST,
    T_PCT,
    T_PCT_OPT,
    T_STATE,
    T_TEXT,
    CaseCandidate,
    DoctorReportV4,
    ae_path,
    apply_ae_overrides,
    apply_structural_overrides,
    coerce_ae_value,
    is_delete_marker,
    is_structural_path,
    is_v4_payload,
    parse_ae_number,
    parse_ae_path,
    path_class,
    resolve_view_path,
    restore_v4,
    section_texts,
    split_card_names,
    treatment_card_count,
    walk_ae_paths,
)
from app.services.report_artifact_validation import INTERNAL_ONLY_MARKERS
from app.utils.medical_filter import check_forbidden

# 값의 길이 상한 — 원장 PDF 한 칸·한 문단에 들어가는 길이(템플릿 검증 `_MAX_FIELD_CHARS`보다 훨씬 작다).
MAX_SENTENCE_CHARS = 400
MAX_LABEL_CHARS = 60
MAX_CARD_NAMES = 8
MAX_FIELDS = 1200
# 승계·사람 기록 상한 — 한 보고서가 수십 번 고쳐져도 스냅샷이 무한히 자라지 않게.
MAX_HISTORY = 20
MAX_NUMBER_CHANGES = 400
AE_EDIT_DROP_REASON_NUMBERS_CHANGED = "NUMBERS_CHANGED"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SECTION_NAMES = ("p1", "p2", "p3", "p4", "p5", "p6", "appendix")


@dataclass(frozen=True, slots=True)
class AeEditProblem:
    path: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


# ── 편집 가능 필드(원문) ───────────────────────────────────────────────────────


def editable_fields(view: DoctorReportV4) -> dict[str, Any]:
    """뷰의 모든 편집 경로 → 현재 값(미리보기·화면·검증의 원문). 종전 별칭(`plan.action[…]`)도 함께 낸다."""
    fields: dict[str, Any] = {}
    for item in walk_ae_paths(view):
        fields[item.path] = list(item.value) if isinstance(item.value, tuple) else item.value
    for row in (*view.plan.rows, *view.plan.continued):
        fields.setdefault(ae_path(AE_FIELD_PLAN_ACTION, row.keyword), row.action)
    return fields


def editable_summary(view: DoctorReportV4) -> dict[str, Any]:
    """화면·테스트가 읽는 요약 구조 — 핵심 3줄·카드 4·사례·계획 행·키워드 라벨·콘텐츠 표·막대."""
    names, tail = split_card_names(view.summary.cards[3].names) if len(view.summary.cards) >= 4 else ([], None)

    def case_dict(slot: str) -> dict[str, Any] | None:
        case = getattr(view.cases, slot)
        if case is None:
            return None
        return {
            "label": case.label,
            "keyword": case.keyword,
            "excerpt": case.excerpt,
            "platform": case.platform,
            "measured_on": case.measured_on,
            "insight": case.insight,
        }

    return {
        "key_lines": list(view.summary.key_lines),
        "card_value": view.summary.cards[3].value if len(view.summary.cards) >= 4 else "",
        "card_names": list(names),
        "card_names_tail": tail,
        "cases": {"found": case_dict("found"), "missing": case_dict("missing")},
        "plan_rows": [
            {
                "priority": row.priority,
                "keyword": row.keyword,
                "treatment": row.treatment,
                "rate_label": row.rate_label,
                "action": row.action,
                "continued": index >= len(view.plan.rows),
            }
            for index, row in enumerate((*view.plan.rows, *view.plan.continued))
        ],
        "keyword_labels": [
            {"keyword": row.keyword, "group": row.group, "treatment": row.treatment, "total": row.total}
            for row in view.appendix.keyword_rows
        ],
        "content_rows": [
            {"title": row.title, "keyword": row.keyword, "treatment": row.treatment}
            for row in view.content.rows
        ],
        "treatment_bars": [{"label": bar.label, "detail": bar.detail} for bar in view.exposure.treatment_bars],
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def editor_items(
    base: DoctorReportV4, stored_fields: Mapping[str, Any], *, case_candidates: Sequence[CaseCandidate] = ()
) -> list[dict[str, Any]]:
    """Admin 편집 화면의 항목 — 보고서 순서(01~06·부록)대로, 항목마다 원문·지금 저장된 값·지움 여부·측정값과 다른 숫자.

    원문은 라벨·사례 교체를 적용한 뷰(파생 뷰)의 값이다 — 라벨을 바꾸면 막대 묶음이 달라지므로 행의 키도 그 뷰 기준이다.
    지워진 행의 칸도 항목으로 남아 화면이 '원래대로'를 보여 줄 수 있다.
    """
    structural = {path: value for path, value in stored_fields.items() if is_structural_path(path) and value is not None}
    derived = apply_structural_overrides(base, structural, case_candidates=case_candidates) if structural else base
    items: list[dict[str, Any]] = []
    for item in walk_ae_paths(derived):
        original = _json_value(item.value)
        stored = stored_fields.get(item.path)
        row_deleted = item.row is not None and is_delete_marker(stored_fields.get(item.row))
        deleted = is_delete_marker(stored)
        value = original if deleted or stored is None else _json_value(stored)
        items.append(
            {
                "path": item.path,
                "section": item.section,
                "kind": item.kind,
                "type": item.value_type,
                "value": value,
                "original": original,
                "deleted": deleted,
                "row": item.row,
                "row_deleted": row_deleted,
                "context": item.context,
                "deletable": item.deletable,
                "overridden": stored is not None,
                "numbers_changed": stored is not None and not deleted and numbers_differ(original, stored),
            }
        )
    return items


def numbers_differ(original: Any, value: Any) -> bool:
    """값이 측정값과 다른 숫자를 말하는가 — 수는 값으로, 글자는 원문에 없던 숫자 토큰이 있는지로 본다(숫자를 뺀 문장은 아니다)."""
    if isinstance(value, Mapping):
        return False
    if isinstance(original, (int, float)) and not isinstance(original, bool):
        number = parse_ae_number(value)
        return number is None or abs(number - float(original)) > 1e-9
    if original is None:
        return parse_ae_number(value) is not None
    before = Counter(_NUMBER.findall(" ".join(original) if isinstance(original, (list, tuple)) else str(original)))
    after = Counter(_NUMBER.findall(" ".join(value) if isinstance(value, (list, tuple)) else str(value)))
    return bool(after - before)


# ── 검증 ──────────────────────────────────────────────────────────────────────


def _text_problems(path: str, value: Any, *, limit: int) -> list[AeEditProblem]:
    if not isinstance(value, str):
        return [AeEditProblem(path, "NOT_TEXT", "문장은 글자로 보내야 합니다.")]
    text = value.strip()
    problems: list[AeEditProblem] = []
    if len(text) > limit:
        problems.append(AeEditProblem(path, "TOO_LONG", f"{limit}자를 넘을 수 없습니다."))
    if _CONTROL.search(text) or any(ch in text for ch in "\n\r\t"):
        problems.append(AeEditProblem(path, "CONTROL_CHARS", "줄바꿈이나 제어 문자는 넣을 수 없습니다."))
    labels = check_forbidden(text)
    if labels:
        problems.append(
            AeEditProblem(path, "FORBIDDEN_EXPRESSION", f"의료광고 금지 표현이 있습니다: {', '.join(labels)}")
        )
    markers = [marker for marker in INTERNAL_ONLY_MARKERS if marker in text]
    if markers:
        problems.append(AeEditProblem(path, "INTERNAL_MARKER", f"원장 전달본에 넣을 수 없는 표식입니다: {', '.join(markers)}"))
    return problems


def _value_problems(path: str, value_type: str, value: Any) -> list[AeEditProblem]:
    if value_type in (T_TEXT, T_LABEL):
        return _text_problems(path, value, limit=MAX_SENTENCE_CHARS if value_type == T_TEXT else MAX_LABEL_CHARS)
    if value_type == T_STATE:
        if not isinstance(value, str) or value.strip() not in HEAT_STATES:
            return [AeEditProblem(path, "NOT_A_STATE", "칸 상태는 노출·미노출·측정 실패·측정 없음 중 하나여야 합니다.")]
        return []
    if value_type == T_LIST:
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            return [AeEditProblem(path, "NOT_LIST", "항목 이름은 글자 목록으로 보내야 합니다.")]
        problems: list[AeEditProblem] = []
        for index, item in enumerate(value):
            problems.extend(_text_problems(f"{path}[{index}]", item, limit=MAX_LABEL_CHARS))
        return problems
    number = parse_ae_number(value)
    if number is None:
        if value_type in (T_COUNT, T_PCT_OPT) and (value is None or (isinstance(value, str) and not value.strip())):
            return []
        return [AeEditProblem(path, "NOT_A_NUMBER", "숫자로 보내야 합니다.")]
    if number < 0:
        return [AeEditProblem(path, "OUT_OF_RANGE", "0보다 작을 수 없습니다.")]
    if value_type in (T_PCT, T_PCT_OPT) and number > 100:
        return [AeEditProblem(path, "OUT_OF_RANGE", "비율은 0~100 사이여야 합니다.")]
    if value_type == T_INT and number != int(number):
        return [AeEditProblem(path, "NOT_AN_INTEGER", "정수로 보내야 합니다.")]
    return []


def validate_ae_fields(
    fields: Mapping[str, Any],
    view: DoctorReportV4,
    *,
    case_candidates: Sequence[CaseCandidate] | None = None,
) -> list[AeEditProblem]:
    """수정 값을 경로 문법·키·형·길이·금지 표현·내부 표식으로 거른다. 값 None은 '수정 취소'라 검사하지 않는다.

    행·칸의 키는 라벨·사례 교체를 적용한 뷰 기준이다. `case_candidates`가 None이면 사례 교체 경로는 형만 보고(후보는
    호출부가 뒤에 맞춘다), 목록을 주면 그 안에 있어야 한다. 빈 절(빈 쪽)은 적용 뒤 `empty_section_problems`가 본다.
    """
    if not isinstance(fields, Mapping):
        return [AeEditProblem("", "NOT_OBJECT", "수정 값은 경로 → 값의 객체여야 합니다.")]
    if len(fields) > MAX_FIELDS:
        return [AeEditProblem("", "TOO_MANY_FIELDS", f"한 번에 {MAX_FIELDS}개 넘는 칸은 고칠 수 없습니다.")]
    plan_keywords = {row.keyword for row in (*view.plan.rows, *view.plan.continued)}
    label_keywords = {row.keyword for row in view.appendix.keyword_rows}
    structural = {path: value for path, value in fields.items() if is_structural_path(path) and value is not None}
    derived = apply_structural_overrides(view, structural, case_candidates=case_candidates or ()) if structural else view
    # 카드 4 이름 목록은 **라벨을 적용한 뒤 다시 센 n**을 넘을 수 없다 — 라벨을 합쳐 n이 줄면 옛 목록이 카드 값보다 길어진다.
    derived_count = derived_card_count(view, fields)
    problems: list[AeEditProblem] = []
    for path, value in fields.items():
        field_ = parse_ae_path(path)
        if field_ is None:
            problems.append(AeEditProblem(str(path), "UNKNOWN_FIELD", "고칠 수 없는 칸입니다. 병원명·기간·공개 페이지 링크·마스트헤드는 바꿀 수 없습니다."))
            continue
        if value is None:
            continue
        if field_.kind == AE_FIELD_KEY_LINE:
            if not is_delete_marker(value):
                problems.extend(_text_problems(path, value, limit=MAX_SENTENCE_CHARS))
        elif field_.kind == AE_FIELD_CARD_NAMES:
            if is_delete_marker(value):
                continue
            if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
                problems.append(AeEditProblem(path, "NOT_LIST", "항목 이름은 글자 목록으로 보내야 합니다."))
                continue
            if len(value) > MAX_CARD_NAMES:
                problems.append(AeEditProblem(path, "TOO_MANY_NAMES", f"항목 이름은 {MAX_CARD_NAMES}개까지입니다."))
            elif derived_count is not None and len(_distinct_names(value)) > derived_count:
                problems.append(
                    AeEditProblem(
                        path, "TOO_MANY_NAMES",
                        f"라벨을 적용한 노출 진료 항목은 {derived_count}개라 이름을 {derived_count}개까지만 적을 수 있습니다.",
                    )
                )
            for index, name in enumerate(value):
                problems.extend(_text_problems(f"{path}[{index}]", name, limit=MAX_LABEL_CHARS))
        elif field_.kind == AE_FIELD_CASE_INSIGHT:
            if getattr(view.cases, field_.arg) is None and getattr(derived.cases, field_.arg) is None:
                problems.append(AeEditProblem(path, "NO_SUCH_CASE", "이 달 보고서에 없는 사례입니다."))
                continue
            if not is_delete_marker(value):
                problems.extend(_text_problems(path, value, limit=MAX_SENTENCE_CHARS))
        elif field_.kind == AE_FIELD_CASE_RECORD:
            if not isinstance(value, str) or not value.strip():
                problems.append(AeEditProblem(path, "NOT_TEXT", "사례는 관측 기록 번호로 골라야 합니다."))
                continue
            if case_candidates is not None and not any(
                c.record_id == value.strip() and c.found == (field_.arg == "found") for c in case_candidates
            ):
                problems.append(AeEditProblem(path, "UNKNOWN_CANDIDATE", "그 달 관측 기록에 없는 사례입니다. 목록에서 다시 골라 주세요."))
        elif field_.kind == AE_FIELD_PLAN_ACTION:
            if field_.arg not in plan_keywords:
                problems.append(AeEditProblem(path, "UNKNOWN_KEYWORD", "다음 달 실행 계획에 없는 키워드입니다."))
                continue
            if not is_delete_marker(value):
                problems.extend(_text_problems(path, value, limit=MAX_SENTENCE_CHARS))
        elif field_.kind == AE_FIELD_TREATMENT_LABEL:
            if field_.arg not in label_keywords:
                problems.append(AeEditProblem(path, "UNKNOWN_KEYWORD", "이 달 측정 키워드에 없는 문장입니다."))
                continue
            if is_delete_marker(value) or not isinstance(value, str) or not value.strip():
                problems.append(AeEditProblem(path, "EMPTY", "진료 항목 라벨은 비울 수 없습니다. 원문으로 되돌리려면 수정을 지워 주세요."))
                continue
            problems.extend(_text_problems(path, value, limit=MAX_LABEL_CHARS))
        elif field_.kind == AE_FIELD_VIEW:
            vp = field_.arg
            if not resolve_view_path(derived, vp):
                problems.append(AeEditProblem(path, "UNKNOWN_KEY", "이 보고서에 없는 행이나 칸입니다."))
                continue
            if is_delete_marker(value):
                if vp.value_type in (T_PCT, T_INT):
                    problems.append(AeEditProblem(path, "NOT_DELETABLE", "이 숫자는 비울 수 없습니다. 값을 바꾸거나 행을 지워 주세요."))
                continue
            if vp.attr is None and vp.family in ("row", "case", "figure"):
                problems.append(AeEditProblem(path, "NOT_TEXT", "행·사례·검정 면 자체는 지우기만 할 수 있습니다."))
                continue
            problems.extend(_value_problems(path, vp.value_type or T_TEXT, value))
    return problems


def empty_section_problems(edited: DoctorReportV4) -> list[AeEditProblem]:
    """본문 6절과 부록은 저마다 한 글자는 남아야 한다 — 다 지우면 빈 쪽이 생겨 원장 PDF 검증(`DOCTOR_PDF_PAGE_EMPTY`)이 막는다."""
    return [
        AeEditProblem(name, "SECTION_EMPTY", "이 절의 글자를 모두 지울 수는 없습니다. 한 항목은 남겨 주세요.")
        for name, texts in section_texts(edited).items()
        if not texts
    ]


def invalid_carry_paths(
    fields: Mapping[str, Any],
    view: DoctorReportV4,
    *,
    case_candidates: Sequence[CaseCandidate] | None = None,
) -> set[str]:
    """이어진(저장된) 수정 값 중 새 pristine 뷰에서 더는 맞지 않는 경로 — **전체 fields 문맥**으로 검증한다.

    카드 4 이름 목록은 라벨을 적용해 다시 센 n을 기준으로 보므로 경로 하나씩 따로 검사하면 라벨 문맥을 잃는다. 경로를
    뺄 때마다 n이 달라질 수 있어 문제가 없을 때까지 되풀이하되, 무효한 라벨(다른 경로)을 먼저 빼고 이름 목록은 다른
    문제가 없을 때만 뺀다. 전체 모양 문제(경로 '')는 어느 경로도 지목하지 않으므로 무시한다.
    """
    remaining = dict(fields)
    invalid: set[str] = set()
    for _ in range(len(remaining) + 1):
        problems = validate_ae_fields(remaining, view, case_candidates=case_candidates)
        paths = {
            AE_PATH_CARD_NAMES if problem.path.startswith(f"{AE_PATH_CARD_NAMES}[") else _owner_path(problem.path, remaining)
            for problem in problems
            if problem.path
        } & set(remaining)
        if not paths:
            break
        others = paths - {AE_PATH_CARD_NAMES}
        paths = others or paths
        invalid |= paths
        for path in paths:
            remaining.pop(path, None)
    return invalid


def _owner_path(problem_path: str, fields: Mapping[str, Any]) -> str:
    """목록 값의 항목 문제(`…competitors[1]`)는 그 목록 경로의 문제다."""
    if problem_path in fields:
        return problem_path
    match = re.match(r"^(.+)\[\d+\]$", problem_path)
    return match.group(1) if match is not None and match.group(1) in fields else problem_path


def _distinct_names(value: Sequence[Any]) -> list[str]:
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def derived_card_count(view: DoctorReportV4, fields: Mapping[str, Any]) -> int | None:
    """`fields`의 진료 항목 라벨만 적용했을 때 카드 4의 n — 라벨이 없으면 원문 카드의 n. 셀 수 없는 카드면 None."""
    if len(view.summary.cards) < 4:
        return None
    labels = {
        path: value
        for path, value in fields.items()
        if value is not None and (f := parse_ae_path(path)) is not None and f.kind == AE_FIELD_TREATMENT_LABEL
    }
    derived = apply_structural_overrides(view, labels) if labels else view
    return treatment_card_count(derived.summary.cards[3])


def normalize_ae_fields(
    fields: Mapping[str, Any],
    view: DoctorReportV4,
    *,
    case_candidates: Sequence[CaseCandidate] = (),
) -> dict[str, Any]:
    """저장할 수정 값 — 공백을 다듬고 형을 맞추고, None(수정 취소)과 **없어도 같은 값이 찍히는** 값은 뺀다(경로 정렬).

    군더더기인지는 원문이 아니라 라벨·사례 교체만 적용한 뷰(파생 뷰)와 비교한다 — 라벨 때문에 자동으로 바뀐 실행
    내용을 사람이 원문으로 되돌린 값은 원문과 같아도 명시 수정으로 남아야 한다. 지우기 표식은 그대로 남긴다.
    """
    cleaned: dict[str, Any] = {}
    for path in sorted(fields):
        value = fields[path]
        field_ = parse_ae_path(path)
        if value is None or field_ is None:
            continue
        if is_delete_marker(value):
            cleaned[path] = {"deleted": True}
            continue
        if field_.kind == AE_FIELD_VIEW and field_.arg.value_type is not None:
            coerced = coerce_ae_value(field_.arg.value_type, value)
            cleaned[path] = list(coerced) if isinstance(coerced, tuple) else coerced
        elif isinstance(value, str):
            cleaned[path] = value.strip()
        elif isinstance(value, (list, tuple)):
            cleaned[path] = [str(item).strip() for item in value]
        else:
            cleaned[path] = value
    structural = {path: value for path, value in cleaned.items() if is_structural_path(path)}
    derived = apply_structural_overrides(view, structural, case_candidates=case_candidates) if structural else view
    baseline = editable_fields(derived)
    return {
        path: value
        for path, value in cleaned.items()
        if path in structural or is_delete_marker(value) or baseline.get(path) != value
    }


# ── 숫자: 전후 기록 ───────────────────────────────────────────────────────────────


# 진료 항목 **묶음** 사실 — 라벨을 고치면 묶음이 갈라지거나 합쳐져 값이 바뀌는 키(막대 `treatment.*`·카드 4의 n).
TREATMENT_GROUPING_FACT_KEYS = frozenset({"card.treatments"})
TREATMENT_GROUPING_FACT_PREFIX = "treatment."


def is_treatment_grouping_fact(key: str) -> bool:
    return key in TREATMENT_GROUPING_FACT_KEYS or key.startswith(TREATMENT_GROUPING_FACT_PREFIX)


def _facts_without_treatments(facts: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in facts.items() if not is_treatment_grouping_fact(key)}


def fact_changes(base: DoctorReportV4, edited: DoctorReportV4) -> dict[str, dict[str, str | None]]:
    """수정을 적용한 뷰의 숫자 사실이 pristine 뷰와 다른 키 → {before, after}. 기록용(막지 않는다)."""
    before = base.facts()
    after = edited.facts()
    changes: dict[str, dict[str, str | None]] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            changes[key] = {"before": before.get(key), "after": after.get(key)}
    return changes


def ae_edit_fact_problems(base: DoctorReportV4, edited: DoctorReportV4) -> list[str]:
    """pristine 뷰와 달라진 숫자 사실의 설명(진료 항목 묶음 제외) — 종전엔 거절 사유였고 지금은 WARN·기록이다."""
    before = _facts_without_treatments(base.facts())
    after = _facts_without_treatments(edited.facts())
    problems = [
        (f"수정 뒤 사라짐: {key}={before[key]}" if key not in after else f"{key}: 원본 {before[key]} ≠ 수정 {after[key]}")
        for key in sorted(before)
        if after.get(key) != before[key]
    ]
    problems.extend(f"수정으로 생긴 숫자: {key}={after[key]}" for key in sorted(set(after) - set(before)))
    return problems


def number_changes(
    base: DoctorReportV4, before_fields: Mapping[str, Any] | None, after_fields: Mapping[str, Any],
    *, edited_by: str, edited_at: str, case_candidates: Sequence[CaseCandidate] = (),
) -> list[dict[str, Any]]:
    """이번 요청이 바꾼 경로 중 측정값(파생 뷰의 원문)과 숫자가 다른 것 — 감사 로그·`ae_overrides.number_changes`에 남긴다."""
    structural = {path: value for path, value in after_fields.items() if is_structural_path(path) and value is not None}
    derived = apply_structural_overrides(base, structural, case_candidates=case_candidates) if structural else base
    originals = editable_fields(derived)
    out: list[dict[str, Any]] = []
    for path in changed_paths(before_fields, after_fields):
        value = after_fields.get(path)
        original = originals.get(path)
        if value is None or is_delete_marker(value) or original is None or not numbers_differ(original, value):
            continue
        out.append({"path": path, "before": _json_value(original), "after": _json_value(value), "edited_by": edited_by, "edited_at": edited_at})
    return out


def _facts_without_treatment_bars(facts: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in facts.items() if not key.startswith(TREATMENT_GROUPING_FACT_PREFIX)}


def numbers_changed_between(previous_base_facts: Mapping[str, str], new_facts: Mapping[str, str]) -> bool:
    """일반 재생성이 숫자·키워드 집합을 바꿨는가 — pristine 뷰끼리의 비교. 바뀌었으면 라벨·고정 문구만 잇는다.

    카드 4의 n(`card.treatments`)도 본다(원본의 변화). 막대(`treatment.*`)는 라벨 이름이 키라 비교하지 않는다.
    """
    return _facts_without_treatment_bars(previous_base_facts) != _facts_without_treatment_bars(new_facts)


def legacy_card_corrected(
    render_inputs: Any, view: DoctorReportV4 | None, facts: Mapping[str, str],
) -> tuple[DoctorReportV4 | None, dict[str, str], str | None]:
    """옛 코드(#286)로 저장된 AE 판의 카드 4를 지금 규칙으로 다시 센 값으로 바꾼 (뷰, 사실, 바뀐 설명).

    옛 코드는 라벨을 합치거나 갈라도 카드 4의 n·'외 N개'를 그대로 뒀다. 그런 판을 경쟁 병원 반영의 얼린 뷰·사실로 쓰면
    지금 규칙으로 다시 얹은 뷰와 카드만 달라 DIFF로 거절된다. 저장된 수정 값을 pristine 뷰에 지금 코드로 다시 얹은
    카드가 얼린 카드와 다를 때만 카드 하나를 바꾸고, 그 밖의 면·사실은 건드리지 않는다.
    """
    from dataclasses import replace

    stored = stored_ae_overrides(render_inputs)
    base = base_view_of(render_inputs) if stored is not None else None
    if stored is None or base is None or len(base.summary.cards) < 4 or (view is not None and len(view.summary.cards) < 4):
        return view, dict(facts), None
    fields = dict(stored["fields"])
    for path in invalid_carry_paths(fields, base):
        fields.pop(path, None)
    applied = apply_ae_overrides(base, fields)
    expected = applied.summary.cards[3]
    frozen_value = view.summary.cards[3].value if view is not None else facts.get("card.treatments")
    frozen_names = list(view.summary.cards[3].names) if view is not None else None
    expected_value = applied.facts().get("card.treatments")
    if (view.summary.cards[3] == expected) if view is not None else (facts.get("card.treatments") == expected_value):
        return view, dict(facts), None
    corrected = view
    if view is not None:
        cards = (*view.summary.cards[:3], expected, *view.summary.cards[4:])
        corrected = replace(view, summary=replace(view.summary, cards=cards))
    corrected_facts = {key: value for key, value in facts.items() if key != "card.treatments"}
    if expected_value is not None:
        corrected_facts["card.treatments"] = expected_value
    note = f"card 4: 얼린 {frozen_value} {frozen_names if frozen_names is not None else ''} → 다시 센 {expected.value} {list(expected.names)}"
    return corrected, corrected_facts, note.strip()


# ── 저장 모양·승계 ─────────────────────────────────────────────────────────────


def stored_ae_overrides(render_inputs: Any) -> dict[str, Any] | None:
    """스냅샷 `render_inputs`(pydantic 모델 또는 dict)의 `ae_overrides` — 수정 값이 하나라도 있을 때만."""
    raw = getattr(render_inputs, "ae_overrides", None) if not isinstance(render_inputs, Mapping) else render_inputs.get("ae_overrides")
    if not isinstance(raw, Mapping):
        return None
    fields = raw.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        return None
    return dict(raw)


def base_view_of(render_inputs: Any) -> DoctorReportV4 | None:
    """저장된 pristine 뷰 — 수정 값이 있으면 `ae_overrides.base_view`, 없으면 `doctor_view` 자신(v4일 때)."""
    overrides = stored_ae_overrides(render_inputs)
    if overrides is not None and is_v4_payload(overrides.get("base_view")):
        return restore_v4(overrides["base_view"])
    doctor_view = getattr(render_inputs, "doctor_view", None) if not isinstance(render_inputs, Mapping) else render_inputs.get("doctor_view")
    if is_v4_payload(doctor_view):
        return restore_v4(doctor_view)
    return None


def merge_ae_fields(stored: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict[str, Any]:
    """이전 수정 값 위에 새 요청을 덮는다. 새 요청의 None은 그 경로의 수정을 지운다."""
    merged = dict(stored or {})
    for path, value in incoming.items():
        if value is None:
            merged.pop(path, None)
        else:
            merged[path] = value
    return merged


def build_ae_overrides(
    *,
    fields: Mapping[str, Any],
    base_view_payload: Mapping[str, Any],
    edited_by: str,
    edited_at: str,
    previous: Mapping[str, Any] | None,
    changed_paths: Sequence[str],
    report_version: int | None,
    number_changes: Sequence[Mapping[str, Any]] = (),
    fact_changes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """스냅샷에 넣을 `ae_overrides`. 사람 기록(`history`)·숫자 변경 기록은 이전 버전 것을 이어 붙이고 상한을 둔다."""
    history = list((previous or {}).get("history") or [])
    if changed_paths:
        history.append(
            {
                "edited_by": edited_by,
                "edited_at": edited_at,
                "paths": sorted(changed_paths),
                "report_version": report_version,
            }
        )
    numbers = [dict(item) for item in ((previous or {}).get("number_changes") or []) if isinstance(item, Mapping)]
    numbers.extend(dict(item) for item in number_changes)
    return {
        "schema_version": AE_OVERRIDES_SCHEMA_VERSION,
        "edited_by": edited_by if changed_paths else str((previous or {}).get("edited_by") or edited_by),
        "edited_at": edited_at if changed_paths else str((previous or {}).get("edited_at") or edited_at),
        "fields": dict(fields),
        "base_view": dict(base_view_payload),
        "history": history[-MAX_HISTORY:],
        "number_changes": numbers[-MAX_NUMBER_CHANGES:],
        "fact_changes": dict(fact_changes or {}),
    }


def carry_ae_fields(previous_fields: Mapping[str, Any], *, numbers_changed: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """이전 버전의 수정 값을 다음 버전으로 잇는다 → (잇는 값, 버리는 값).

    숫자·키워드 집합이 그대로면 전부 잇는다. 바뀐 일반 재생성이면 진료 항목 라벨(`CLASS_LABEL`)과 측정과 무관한 고정
    문구(`CLASS_STATIC` — 소제목·표 머리글·캡션·용어·유의사항·그 지우기)만 잇고, 측정 결과를 말하는 문장·표의 칸·숫자·
    행 지우기·사례 교체는 버린다 — 사람이 적은 값이 바뀐 숫자와 어긋나지 않게.
    """
    if not numbers_changed:
        return dict(previous_fields), {}
    kept: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for path, value in previous_fields.items():
        if path_class(path) in (CLASS_LABEL, CLASS_STATIC):
            kept[path] = value
        else:
            dropped[path] = value
    return kept, dropped


def changed_paths(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> list[str]:
    before = dict(before or {})
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def field_changes(base: DoctorReportV4, before_fields: Mapping[str, Any] | None, after_fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """감사 기록용 필드별 전/후 — '전'은 직전 버전이 찍던 값(이전 수정 또는 원문), '후'는 이번 요청이 찍을 값.

    측정값과 숫자가 다른 변경은 `numbers_changed: true`로 표시한다.
    """
    originals = editable_fields(base)
    before = dict(before_fields or {})
    changes = []
    for path in changed_paths(before, after_fields):
        after = after_fields.get(path, originals.get(path))
        original = originals.get(path)
        changes.append(
            {
                "path": path,
                "before": _json_value(before.get(path, original)),
                "after": _json_value(after),
                "numbers_changed": bool(original is not None and after is not None and not is_delete_marker(after) and numbers_differ(original, after)),
            }
        )
    return changes


__all__ = [
    "AE_EDIT_DROP_REASON_NUMBERS_CHANGED", "AeEditProblem", "MAX_HISTORY", "ae_edit_fact_problems",
    "base_view_of", "build_ae_overrides", "carry_ae_fields", "changed_paths", "editable_fields",
    "editable_summary", "editor_items", "empty_section_problems", "fact_changes", "field_changes",
    "merge_ae_fields", "normalize_ae_fields", "number_changes", "numbers_changed_between", "numbers_differ",
    "stored_ae_overrides", "validate_ae_fields",
]
