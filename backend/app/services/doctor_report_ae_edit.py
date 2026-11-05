"""원장용 월간 보고서 v4 — AE 수정 판(서술 문구·진료 항목 라벨만, 숫자는 그대로).

대표 지시(2026-11-05): AE가 Admin 보고서 탭에서 보고서의 문장과 라벨을 바로잡고 저장된 숫자는 그대로 둔 채
새 버전 PDF를 만든다. 적용 규칙은 `doctor_report_v4.apply_ae_overrides` 하나이고, 이 모듈은 그 주변 —
화이트리스트 검증(경로·형·길이·금지 표현·내부 표식·새 숫자), 편집 가능 필드 추출(미리보기·화면), 숫자 보존 판정,
이전 버전 수정 값의 승계 규칙 — 을 맡는다. DB·저장소·공급자 호출이 없다.

저장 위치: `MonthlyReport.content_summary.report_snapshot.render_inputs.ae_overrides`
    {schema_version, edited_by, edited_at, fields: {경로: 값}, base_view: 시스템이 만든 pristine v4 뷰, history: [...]}
`doctor_view`는 수정을 적용한(실제로 찍은) 뷰라 검증·대조·전달 경로의 독자는 바뀌지 않는다. `base_view`는 다음
버전이 같은 수정을 pristine 뷰에 다시 적용하고, 화면이 원문을 보여 주는 근거다.
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
    AE_OVERRIDES_SCHEMA_VERSION,
    AE_PATH_CARD_NAMES,
    CaseCandidate,
    DoctorReportV4,
    ae_path,
    is_v4_payload,
    parse_ae_path,
    restore_v4,
    split_card_names,
)
from app.services.report_artifact_validation import INTERNAL_ONLY_MARKERS
from app.utils.medical_filter import check_forbidden

# 수정 값의 길이 상한 — 원장 PDF 한 칸에 들어가는 길이다(템플릿 검증 `_MAX_FIELD_CHARS`보다 훨씬 작다).
MAX_SENTENCE_CHARS = 200
MAX_LABEL_CHARS = 40
MAX_CARD_NAMES = 8
MAX_FIELDS = 200
# 승계·사람 기록 상한 — 한 보고서가 수십 번 고쳐져도 스냅샷이 무한히 자라지 않게.
MAX_HISTORY = 20
AE_EDIT_DROP_REASON_NUMBERS_CHANGED = "NUMBERS_CHANGED"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class AeEditProblem:
    path: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


# ── 편집 가능 필드(원문) ───────────────────────────────────────────────────────


def editable_fields(view: DoctorReportV4) -> dict[str, Any]:
    """뷰에서 AE가 고칠 수 있는 칸만 경로 → 현재 값으로 뽑는다(미리보기·화면·검증의 원문)."""
    fields: dict[str, Any] = {}
    for index, line in enumerate(view.summary.key_lines[:3]):
        fields[ae_path(AE_FIELD_KEY_LINE, index)] = line
    if len(view.summary.cards) >= 4:
        names, _tail = split_card_names(view.summary.cards[3].names)
        fields[AE_PATH_CARD_NAMES] = list(names)
    for slot in ("found", "missing"):
        case = getattr(view.cases, slot)
        if case is not None:
            fields[ae_path(AE_FIELD_CASE_INSIGHT, slot)] = case.insight
    for row in (*view.plan.rows, *view.plan.continued):
        fields.setdefault(ae_path(AE_FIELD_PLAN_ACTION, row.keyword), row.action)
    for row in view.appendix.keyword_rows:
        fields.setdefault(ae_path(AE_FIELD_TREATMENT_LABEL, row.keyword), row.treatment)
    return fields


def editable_summary(view: DoctorReportV4) -> dict[str, Any]:
    """화면이 그리는 구조 — 핵심 3줄·카드 4·사례·계획 행·키워드 라벨·콘텐츠 표. 숫자 칸은 읽기 전용 참고 값이다."""
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


# ── 검증 ──────────────────────────────────────────────────────────────────────


def _text_problems(path: str, value: Any, original: str | None, *, limit: int) -> list[AeEditProblem]:
    if not isinstance(value, str):
        return [AeEditProblem(path, "NOT_TEXT", "문장은 글자로 보내야 합니다.")]
    text = value.strip()
    if not text:
        return [AeEditProblem(path, "EMPTY", "빈 칸으로 둘 수 없습니다. 원문으로 되돌리려면 수정을 지워 주세요.")]
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
    # 새 숫자는 저장 숫자와 어긋날 수 있다 — 원문에 있던 숫자만(같은 횟수 이하로) 남길 수 있다.
    allowed = Counter(_NUMBER.findall(original or ""))
    extra = [token for token, count in Counter(_NUMBER.findall(text)).items() if count > allowed.get(token, 0)]
    if extra:
        problems.append(
            AeEditProblem(path, "NUMBER_NOT_IN_ORIGINAL", f"원문에 없는 숫자는 넣을 수 없습니다: {', '.join(extra)}")
        )
    return problems


def validate_ae_fields(
    fields: Mapping[str, Any],
    view: DoctorReportV4,
    *,
    case_candidates: Sequence[CaseCandidate] | None = None,
) -> list[AeEditProblem]:
    """수정 값을 화이트리스트·형·길이·금지 표현·내부 표식·새 숫자로 거른다. 값 None은 '수정 취소'라 검사하지 않는다.

    `case_candidates`가 None이면 사례 교체 경로는 형만 보고(후보는 호출부가 뒤에 맞춘다), 목록을 주면 그 안에 있어야 한다.
    """
    if not isinstance(fields, Mapping):
        return [AeEditProblem("", "NOT_OBJECT", "수정 값은 경로 → 값의 객체여야 합니다.")]
    if len(fields) > MAX_FIELDS:
        return [AeEditProblem("", "TOO_MANY_FIELDS", f"한 번에 {MAX_FIELDS}개 넘는 칸은 고칠 수 없습니다.")]
    originals = editable_fields(view)
    plan_keywords = {row.keyword for row in (*view.plan.rows, *view.plan.continued)}
    label_keywords = {row.keyword for row in view.appendix.keyword_rows}
    problems: list[AeEditProblem] = []
    for path, value in fields.items():
        field_ = parse_ae_path(path)
        if field_ is None:
            problems.append(AeEditProblem(str(path), "UNKNOWN_FIELD", "고칠 수 없는 칸입니다. 숫자·키워드 문장·콘텐츠 제목·부록 표는 바꿀 수 없습니다."))
            continue
        if value is None:
            continue
        if field_.kind == AE_FIELD_KEY_LINE:
            problems.extend(_text_problems(path, value, originals.get(path), limit=MAX_SENTENCE_CHARS))
        elif field_.kind == AE_FIELD_CARD_NAMES:
            if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
                problems.append(AeEditProblem(path, "NOT_LIST", "항목 이름은 글자 목록으로 보내야 합니다."))
                continue
            if len(value) > MAX_CARD_NAMES:
                problems.append(AeEditProblem(path, "TOO_MANY_NAMES", f"항목 이름은 {MAX_CARD_NAMES}개까지입니다."))
            original_names = " ".join(originals.get(path) or [])
            for index, name in enumerate(value):
                problems.extend(_text_problems(f"{path}[{index}]", name, original_names, limit=MAX_LABEL_CHARS))
        elif field_.kind == AE_FIELD_CASE_INSIGHT:
            if getattr(view.cases, field_.arg) is None:
                problems.append(AeEditProblem(path, "NO_SUCH_CASE", "이 달 보고서에 없는 사례입니다."))
                continue
            problems.extend(_text_problems(path, value, originals.get(path), limit=MAX_SENTENCE_CHARS))
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
            problems.extend(_text_problems(path, value, originals.get(path), limit=MAX_SENTENCE_CHARS))
        elif field_.kind == AE_FIELD_TREATMENT_LABEL:
            if field_.arg not in label_keywords:
                problems.append(AeEditProblem(path, "UNKNOWN_KEYWORD", "이 달 측정 키워드에 없는 문장입니다."))
                continue
            problems.extend(_text_problems(path, value, originals.get(path), limit=MAX_LABEL_CHARS))
    return problems


def normalize_ae_fields(fields: Mapping[str, Any], view: DoctorReportV4) -> dict[str, Any]:
    """저장할 수정 값 — 공백을 다듬고, 원문과 같은 값·None(수정 취소)은 뺀다(경로 정렬)."""
    originals = editable_fields(view)
    normalized: dict[str, Any] = {}
    for path in sorted(fields):
        value = fields[path]
        if value is None or parse_ae_path(path) is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, (list, tuple)):
            value = [str(item).strip() for item in value]
        if path in originals and originals[path] == value:
            continue
        normalized[path] = value
    return normalized


# ── 숫자 보존 판정 ──────────────────────────────────────────────────────────────


def _facts_without_treatments(facts: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in facts.items() if not key.startswith("treatment.")}


def ae_edit_fact_problems(base: DoctorReportV4, edited: DoctorReportV4) -> list[str]:
    """수정을 적용한 뷰의 숫자 사실이 pristine 뷰와 같은가. 진료 항목 묶음(`treatment.*`)만 라벨에 따라 달라질 수 있다."""
    before = _facts_without_treatments(base.facts())
    after = _facts_without_treatments(edited.facts())
    problems = [
        (f"수정 뒤 사라짐: {key}={before[key]}" if key not in after else f"{key}: 원본 {before[key]} ≠ 수정 {after[key]}")
        for key in sorted(before)
        if after.get(key) != before[key]
    ]
    problems.extend(f"수정으로 생긴 숫자: {key}={after[key]}" for key in sorted(set(after) - set(before)))
    return problems


def numbers_changed_between(previous_base_facts: Mapping[str, str], new_facts: Mapping[str, str]) -> bool:
    """일반 재생성이 숫자·키워드 집합을 바꿨는가 — 바뀌었으면 사람이 쓴 문장을 잇지 않는다."""
    return _facts_without_treatments(previous_base_facts) != _facts_without_treatments(new_facts)


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
) -> dict[str, Any]:
    """스냅샷에 넣을 `ae_overrides`. 사람 기록(`history`)은 이전 버전 것을 이어 붙이고 상한을 둔다."""
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
    return {
        "schema_version": AE_OVERRIDES_SCHEMA_VERSION,
        "edited_by": edited_by if changed_paths else str((previous or {}).get("edited_by") or edited_by),
        "edited_at": edited_at if changed_paths else str((previous or {}).get("edited_at") or edited_at),
        "fields": dict(fields),
        "base_view": dict(base_view_payload),
        "history": history[-MAX_HISTORY:],
    }


def carry_ae_fields(previous_fields: Mapping[str, Any], *, numbers_changed: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """이전 버전의 수정 값을 다음 버전으로 잇는다 → (잇는 값, 버리는 값).

    숫자·키워드 집합이 그대로면 전부 잇는다. 바뀐 일반 재생성이면 진료 항목 라벨만 잇고 문장(핵심 3줄·시사점·
    실행 내용·카드 이름·사례 교체)은 버린다 — 사람이 쓴 문장이 바뀐 숫자와 어긋나지 않게.
    """
    if not numbers_changed:
        return dict(previous_fields), {}
    kept: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for path, value in previous_fields.items():
        field_ = parse_ae_path(path)
        if field_ is not None and field_.kind == AE_FIELD_TREATMENT_LABEL:
            kept[path] = value
        else:
            dropped[path] = value
    return kept, dropped


def changed_paths(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> list[str]:
    before = dict(before or {})
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def field_changes(base: DoctorReportV4, before_fields: Mapping[str, Any] | None, after_fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """감사 기록용 필드별 전/후 — '전'은 직전 버전이 찍던 값(이전 수정 또는 원문), '후'는 이번 요청이 찍을 값."""
    originals = editable_fields(base)
    before = dict(before_fields or {})
    changes = []
    for path in changed_paths(before, after_fields):
        changes.append(
            {
                "path": path,
                "before": before.get(path, originals.get(path)),
                "after": after_fields.get(path, originals.get(path)),
            }
        )
    return changes


__all__ = [
    "AE_EDIT_DROP_REASON_NUMBERS_CHANGED", "AeEditProblem", "MAX_HISTORY", "ae_edit_fact_problems",
    "base_view_of", "build_ae_overrides", "carry_ae_fields", "changed_paths", "editable_fields",
    "editable_summary", "field_changes", "merge_ae_fields", "normalize_ae_fields", "numbers_changed_between",
    "stored_ae_overrides", "validate_ae_fields",
]
