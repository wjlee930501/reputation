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
    treatment_card_count,
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
    # 카드 4 이름 목록은 **라벨을 적용한 뒤 다시 센 n**을 넘을 수 없다 — 라벨을 합쳐 n이 줄면 옛 목록(이어진 수정 포함)이
    # 카드 값보다 길어진다(Codex 3-P1-1). 라벨 수정이 없으면 원문 카드의 n이다.
    derived_count = derived_card_count(view, fields)
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
            elif derived_count is not None and len(_distinct_names(value)) > derived_count:
                problems.append(
                    AeEditProblem(
                        path, "TOO_MANY_NAMES",
                        f"라벨을 적용한 노출 진료 항목은 {derived_count}개라 이름을 {derived_count}개까지만 적을 수 있습니다.",
                    )
                )
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


def _distinct_names(value: Sequence[Any]) -> list[str]:
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def derived_card_count(view: DoctorReportV4, fields: Mapping[str, Any]) -> int | None:
    """`fields`의 진료 항목 라벨만 적용했을 때 카드 4의 n — 라벨이 없으면 원문 카드의 n. 셀 수 없는 카드면 None."""
    from app.services.doctor_report_v4 import apply_ae_overrides

    if len(view.summary.cards) < 4:
        return None
    labels = {
        path: value
        for path, value in fields.items()
        if value is not None and (f := parse_ae_path(path)) is not None and f.kind == AE_FIELD_TREATMENT_LABEL
    }
    derived = apply_ae_overrides(view, labels) if labels else view
    return treatment_card_count(derived.summary.cards[3])


def normalize_ae_fields(
    fields: Mapping[str, Any],
    view: DoctorReportV4,
    *,
    case_candidates: Sequence[CaseCandidate] = (),
) -> dict[str, Any]:
    """저장할 수정 값 — 공백을 다듬고, None(수정 취소)과 **없어도 같은 값이 찍히는** 문장은 뺀다(경로 정렬).

    문장이 군더더기인지는 원문이 아니라 라벨·사례 교체만 적용한 뷰(파생 뷰)와 비교한다 — 라벨 때문에 자동으로 바뀐
    실행 내용을 사람이 원문으로 되돌린 값은 원문과 같아도 명시 수정으로 남아야 한다(검토 2-P2-2).
    """
    from app.services.doctor_report_v4 import apply_ae_overrides

    cleaned: dict[str, Any] = {}
    for path in sorted(fields):
        value = fields[path]
        if value is None or parse_ae_path(path) is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, (list, tuple)):
            value = [str(item).strip() for item in value]
        cleaned[path] = value
    structural = {
        path: value
        for path, value in cleaned.items()
        if parse_ae_path(path).kind in (AE_FIELD_TREATMENT_LABEL, AE_FIELD_CASE_RECORD)
    }
    derived = apply_ae_overrides(view, structural, case_candidates=case_candidates) if structural else view
    baseline = editable_fields(derived)
    return {
        path: value
        for path, value in cleaned.items()
        if path in structural or baseline.get(path) != value
    }


# ── 숫자 보존 판정 ──────────────────────────────────────────────────────────────


# 진료 항목 **묶음** 사실 — 라벨을 고치면 묶음이 갈라지거나 합쳐져 값이 바뀌는 키. 막대(`treatment.*`)와 카드 4의 n
# (`card.treatments`, '{n}개 항목')이 같은 노출 행에서 나오므로 함께 예외다. 그 밖의 숫자는 라벨로 바뀌지 않는다.
TREATMENT_GROUPING_FACT_KEYS = frozenset({"card.treatments"})
TREATMENT_GROUPING_FACT_PREFIX = "treatment."


def is_treatment_grouping_fact(key: str) -> bool:
    return key in TREATMENT_GROUPING_FACT_KEYS or key.startswith(TREATMENT_GROUPING_FACT_PREFIX)


def _facts_without_treatments(facts: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in facts.items() if not is_treatment_grouping_fact(key)}


def ae_edit_fact_problems(base: DoctorReportV4, edited: DoctorReportV4) -> list[str]:
    """수정을 적용한 뷰의 숫자 사실이 pristine 뷰와 같은가. 진료 항목 묶음(`treatment.*`·카드 4의 n)만 라벨에 따라 달라질 수 있다."""
    before = _facts_without_treatments(base.facts())
    after = _facts_without_treatments(edited.facts())
    problems = [
        (f"수정 뒤 사라짐: {key}={before[key]}" if key not in after else f"{key}: 원본 {before[key]} ≠ 수정 {after[key]}")
        for key in sorted(before)
        if after.get(key) != before[key]
    ]
    problems.extend(f"수정으로 생긴 숫자: {key}={after[key]}" for key in sorted(set(after) - set(before)))
    return problems


def _facts_without_treatment_bars(facts: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in facts.items() if not key.startswith(TREATMENT_GROUPING_FACT_PREFIX)}


def numbers_changed_between(previous_base_facts: Mapping[str, str], new_facts: Mapping[str, str]) -> bool:
    """일반 재생성이 숫자·키워드 집합을 바꿨는가 — 바뀌었으면 사람이 쓴 문장을 잇지 않는다.

    pristine 뷰끼리의 비교라 카드 4의 n(`card.treatments`)도 **본다** — 여기서 n의 변화는 라벨 수정이 아니라 원본(측정·분류)의
    변화이고, 사람이 쓴 문장이 그 수를 담고 있을 수 있다(Codex 3-P1-2). 막대(`treatment.*`)는 라벨 이름이 키라 비교하지 않는다.
    """
    return _facts_without_treatment_bars(previous_base_facts) != _facts_without_treatment_bars(new_facts)


def legacy_card_corrected(
    render_inputs: Any, view: DoctorReportV4 | None, facts: Mapping[str, str],
) -> tuple[DoctorReportV4 | None, dict[str, str], str | None]:
    """옛 코드(#286)로 저장된 AE 판의 카드 4를 지금 규칙으로 다시 센 값으로 바꾼 (뷰, 사실, 바뀐 설명).

    옛 코드는 라벨을 합치거나 갈라도 카드 4의 n·'외 N개'를 그대로 뒀다. 그런 판을 경쟁 병원 반영의 얼린 뷰·사실로 쓰면 지금
    규칙으로 다시 얹은 뷰와 카드만 달라 DIFF로 거절된다(Codex 3-P1-3). 저장된 수정 값을 pristine 뷰에 지금 코드로 다시 얹은
    카드가 얼린 카드와 다를 때만 카드 하나를 바꾸고, 그 밖의 면·사실은 건드리지 않는다. 수정 값이 없거나 같으면 그대로다.
    """
    from dataclasses import replace

    from app.services.doctor_report_v4 import apply_ae_overrides

    stored = stored_ae_overrides(render_inputs)
    base = base_view_of(render_inputs) if stored is not None else None
    if stored is None or base is None or len(base.summary.cards) < 4 or (view is not None and len(view.summary.cards) < 4):
        return view, dict(facts), None
    applied = apply_ae_overrides(base, dict(stored["fields"]))
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
