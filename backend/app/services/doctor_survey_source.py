"""원장 사전 설문 답 → 설문 근거 자료 한 행(D32 INTERVIEW).

원장이 링크에서 **제출한** 답 중 서술(진료 철학·소개·말투·진료 설명·지역·주의)만 줄 단위로 렌더해 병원마다
시스템 소유 자료 한 행(`SourceType.INTERVIEW` + ``source_metadata.system_kind="DOCTOR_SURVEY"`` +
``created_by="system:doctor-survey"``)에 둔다. 새 enum을 만들지 않는 이유는 PROFILE_FACTS와 같다(E2).

- **설문 최우선 근거:** 자료 종류가 INTERVIEW라 Essence 합성에서 원장 목소리·철학·진료 서술의 최우선
  근거이고, 필수 문구는 이 자료의 원장 문장을 글자 그대로 쓴다(`essence_engine._select_must_use`).
- **원장이 쓴 글만 문장으로:** 고르기 답(보기)은 ``라벨: 값`` 조각 그대로 싣는다. 보기로 1인칭 문장을
  지어 넣지 않는다 — 시스템이 쓴 문장이 '설문 원문의 원장 문장'으로 뽑혀 모든 글에 들어가면 안 된다.
  노트의 원문 발췌(source_excerpt)는 라벨을 뺀 **답 부분만**이다.
- **가림(E6):** 답마다 휴대전화·이메일·주민번호를 가린 뒤 싣는다.
- **오염 줄 제외:** 프롬프트 주입 의심·오류 페이지 잔재 줄은 싣지 않고 메타데이터에만 남긴다. 그래서
  자료 전체가 Essence 입력에서 빠지는 일이 없다.
- **결정적 처리:** 줄 하나 → 근거 노트 하나, LLM을 부르지 않는다(`essence_engine.process_source_asset`
  분기). 다시 제출하면 **같은 행을 제자리에서** 맞추고, 그대로인 줄의 노트 id는 유지해 그 노트를 인용한
  판·글의 근거가 끊기지 않는다. 사람이 자료를 고치는 경로(PATCH)처럼 근거 철회로 다루지 않는다 —
  원장이 답을 고치는 것은 정정이 아니라 같은 원장의 최신 답이다.
- 사실(병원명·의료진·주소·전화·진료시간·채널·인증·가격)과 측정 기준(검색 키워드·AI 질문·비교 병원)은
  싣지 않는다(`doctor_survey_schema` 머리말).
- Admin 자료 API에서 읽기 전용이다(수정·제외·재처리 409).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.services import doctor_survey_schema as schema
from app.services.survey_sources import mask_personal_info
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import check_forbidden
from app.utils.prompt_injection import has_prompt_injection

DOCTOR_SURVEY_SYSTEM_KIND = "DOCTOR_SURVEY"
DOCTOR_SURVEY_ACTOR = "system:doctor-survey"
DOCTOR_SURVEY_TITLE = "원장 사전 설문(원장 작성)"
DOCTOR_SURVEY_READ_ONLY_CODE = "DOCTOR_SURVEY_READ_ONLY"
DOCTOR_SURVEY_READ_ONLY_MESSAGE = (
    "원장 사전 설문 자료는 원장님이 설문 링크에서 제출한 답으로 자동 갱신됩니다. "
    "답을 바꾸려면 원장님께 같은 링크에서 고쳐 다시 제출해 달라고 요청해 주세요."
)

#: 줄 머리표 — 노트 종류를 한국어로 싣는다(LLM도 사람도 읽는다).
_TAGS: dict[str, str] = {
    schema.NOTE_PHILOSOPHY: "원장 설문·진료 철학",
    schema.NOTE_KEY_MESSAGE: "원장 설문·병원 소개",
    schema.NOTE_TONE: "원장 설문·말투와 특징",
    schema.NOTE_TREATMENT: "원장 설문·진료",
    schema.NOTE_LOCAL: "원장 설문·환자와 지역",
    schema.NOTE_RISK: "원장 설문·쓰지 않을 표현",
}
_TAG_NOTE_TYPES = {tag: note_type for note_type, tag in _TAGS.items()}
_LINE_PATTERN = re.compile(r"^\[(?P<tag>[^\]]+)\] (?P<label>[^:\n]+): (?P<value>.+)$")
_MAX_VALUE_CHARS = 600
_TREATMENT_SEPARATOR = " — "


@dataclass(frozen=True, slots=True)
class SurveyLine:
    note_type: str
    label: str
    value: str
    treatment: str | None = None

    def render(self) -> str:
        return f"[{_TAGS[self.note_type]}] {self.label}: {self.value}"


@dataclass(frozen=True, slots=True)
class SurveyNote:
    note_type: EvidenceNoteType
    claim: str
    source_excerpt: str
    excerpt_start: int
    excerpt_end: int
    note_metadata: dict[str, Any] = field(default_factory=dict)


def is_doctor_survey_source(source: Any) -> bool:
    value = getattr(source, "source_type", None)
    if str(getattr(value, "value", value)) != SourceType.INTERVIEW.value:
        return False
    metadata = getattr(source, "source_metadata", None)
    return isinstance(metadata, dict) and metadata.get("system_kind") == DOCTOR_SURVEY_SYSTEM_KIND


# ── 렌더 ────────────────────────────────────────────────────────────────


def _one_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\x00", " ")).strip()
    return text[:_MAX_VALUE_CHARS]


def _label(text: str) -> str:
    # 줄 형식 ``[태그] 라벨: 값`` — 라벨에 콜론·대괄호가 들어가면 다시 읽을 때 갈라진다.
    return _one_line(text).replace(":", "·").replace("[", "(").replace("]", ")")


def _display(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, bool):
        return ""
    return str(value or "")


def _field_lines(
    fields: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any],
    *,
    prefix: str = "",
    treatment: str | None = None,
) -> Iterator[SurveyLine]:
    for spec in schema.active_fields(fields, scope):
        key = str(spec["key"])
        note_type = spec.get("essence")
        if not note_type or spec.get("t") == "list":
            continue
        value = scope.get(key)
        if key == "tone" and value == "직접 입력":
            continue  # 직접 입력한 말투는 tone_custom 줄이 싣는다.
        text = _display(value)
        if not text.strip():
            continue
        label = str(spec.get("render_label") or spec.get("label") or key)
        yield SurveyLine(note_type, _label(f"{prefix}{label}"), _one_line(text), treatment)


def survey_lines(answers: Mapping[str, Any] | None, *, new_clinic: bool) -> list[SurveyLine]:
    """제출한 답 → 근거 자료 줄(가리기 전). 설문 화면의 순서를 따른다."""

    answers = answers or {}
    lines: list[SurveyLine] = []
    for _part, question in schema.all_questions():
        if question.get("new_clinic_only") and not new_clinic:
            continue
        active = list(schema.active_fields(question.get("b") or [], answers))
        for spec in active:
            key = str(spec["key"])
            if spec.get("t") != "list" or not spec.get("essence"):
                continue
            for row in answers.get(key) or []:
                if not isinstance(row, Mapping):
                    continue
                if key == "treatments":
                    name = _one_line(str(row.get("name") or ""))
                    summary = _one_line(str(row.get("summary") or ""))
                    if not name:
                        continue
                    value = f"{name}{_TREATMENT_SEPARATOR}{summary}" if summary else name
                    lines.append(SurveyLine(spec["essence"], "진료 항목", value, name))
                elif key == "faq_custom":
                    question_text = _one_line(str(row.get("question") or ""))
                    answer = _one_line(str(row.get("answer") or ""))
                    if question_text and answer:
                        lines.append(
                            SurveyLine(
                                spec["essence"],
                                _label(f"자주 받는 질문 '{question_text}'에 대한 답"),
                                answer,
                            )
                        )
        lines.extend(_field_lines(question.get("b") or [], answers))
    focus_answers = answers.get(schema.FOCUS_ANSWERS_KEY) or {}
    for name in (answers.get("focus_treatments") or [])[: schema.FOCUS_MAX]:
        block = focus_answers.get(name) if isinstance(focus_answers, Mapping) else None
        if not isinstance(block, Mapping):
            continue
        treatment = _one_line(str(name))
        prefix = f"{_label(treatment)}{_TREATMENT_SEPARATOR}"
        for question in schema.FOCUS_QUESTIONS:
            lines.extend(
                _field_lines(question.get("b") or [], block, prefix=prefix, treatment=treatment)
            )
    return lines


def render_interview_text(
    answers: Mapping[str, Any] | None,
    *,
    new_clinic: bool,
    excluded: list[dict[str, str]] | None = None,
) -> str | None:
    """근거 자료 본문. 실을 답이 없으면 None. 가림(E6)·오염 줄 제외를 여기서 한다."""

    rendered: list[str] = []
    for line in survey_lines(answers, new_clinic=new_clinic):
        masked = SurveyLine(
            line.note_type,
            mask_personal_info(line.label) or "",
            mask_personal_info(line.value) or "",
            line.treatment,
        )
        text = masked.render()
        if has_prompt_injection(text):
            reason = "PROMPT_INJECTION"
        elif looks_like_error_page_text(text):
            reason = "ERROR_PAGE"
        else:
            reason = None
        if reason:
            if excluded is not None:
                excluded.append({"line": text[:80], "reason": reason})
            continue
        if text not in rendered:
            rendered.append(text)
    return "\n".join(rendered) or None


# ── 노트 ────────────────────────────────────────────────────────────────


def survey_note_payloads(raw_text: str | None) -> list[SurveyNote]:
    """원문 줄 → 근거 노트(결정적). 발췌는 라벨을 뺀 답 부분이다."""

    notes: list[SurveyNote] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    for line in (raw_text or "").split("\n"):
        start = offset
        offset += len(line) + 1
        match = _LINE_PATTERN.match(line)
        if not match:
            continue
        type_value = _TAG_NOTE_TYPES.get(match.group("tag"))
        if type_value is None:
            continue
        note_type = EvidenceNoteType(type_value)
        label = match.group("label")
        value = match.group("value")
        value_start = start + match.start("value")
        metadata: dict[str, Any] = {"system_kind": DOCTOR_SURVEY_SYSTEM_KIND, "label": label}
        if _TREATMENT_SEPARATOR in label:
            metadata["treatment"] = label.split(_TREATMENT_SEPARATOR, 1)[0].strip()
        elif label == "진료 항목":
            metadata["treatment"] = value.split(_TREATMENT_SEPARATOR, 1)[0].strip()
        violations = check_forbidden(line)
        if violations and note_type != EvidenceNoteType.RISK_SIGNAL:
            note_type = EvidenceNoteType.RISK_SIGNAL
            metadata["violations"] = violations
        claim = f"{label}: {value}"[:500]
        key = (note_type.value, claim)
        if key in seen:
            continue
        seen.add(key)
        notes.append(
            SurveyNote(
                note_type=note_type,
                claim=claim,
                source_excerpt=value,
                excerpt_start=value_start,
                excerpt_end=value_start + len(value),
                note_metadata=metadata,
            )
        )
    return notes


# ── 자료 행 맞추기 ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurveySyncResult:
    status: str
    source_id: uuid.UUID | None = None
    notes_added: int = 0
    notes_removed: int = 0


async def existing_survey_source(
    db: AsyncSession, hospital_id: uuid.UUID, source_id: uuid.UUID | None = None
) -> HospitalSourceAsset | None:
    if source_id is not None:
        source = await db.get(HospitalSourceAsset, source_id)
        if source is not None and source.hospital_id == hospital_id and is_doctor_survey_source(source):
            return source
    rows = (
        await db.execute(
            select(HospitalSourceAsset)
            .where(
                HospitalSourceAsset.hospital_id == hospital_id,
                HospitalSourceAsset.source_type == SourceType.INTERVIEW,
                HospitalSourceAsset.created_by == DOCTOR_SURVEY_ACTOR,
            )
            .order_by(HospitalSourceAsset.created_at, HospitalSourceAsset.id)
        )
    ).scalars()
    return next((row for row in rows if is_doctor_survey_source(row)), None)


def _metadata(raw_text: str | None, excluded: list[dict[str, str]]) -> dict[str, Any]:
    from app.services.essence_engine import source_processing_coverage

    metadata: dict[str, Any] = {"system_kind": DOCTOR_SURVEY_SYSTEM_KIND, "read_only": True}
    if raw_text:
        metadata["extraction_coverage"] = source_processing_coverage(raw_text)
    if excluded:
        metadata["excluded_lines"] = excluded
    return metadata


async def sync_doctor_survey_source(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    answers: Mapping[str, Any] | None,
    new_clinic: bool,
    source_id: uuid.UUID | None = None,
) -> SurveySyncResult:
    """제출한 답으로 설문 자료와 노트를 맞춘다. 호출자가 병원 advisory 잠금을 들고 커밋한다."""

    from app.services.essence_engine import compute_source_content_hash
    from app.services.source_processing_runs import processing_input_hash

    excluded: list[dict[str, str]] = []
    raw_text = render_interview_text(answers, new_clinic=new_clinic, excluded=excluded)
    source = await existing_survey_source(db, hospital_id, source_id)
    if source is None and raw_text is None:
        return SurveySyncResult(status="EMPTY")

    now = datetime.now(UTC)
    created = source is None
    if source is None:
        source = HospitalSourceAsset(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            source_type=SourceType.INTERVIEW,
            title=DOCTOR_SURVEY_TITLE,
            created_by=DOCTOR_SURVEY_ACTOR,
            updated_by=DOCTOR_SURVEY_ACTOR,
            source_metadata={"system_kind": DOCTOR_SURVEY_SYSTEM_KIND, "read_only": True},
            status=SourceStatus.PENDING,
        )
        db.add(source)
        await db.flush()

    content_hash = (
        compute_source_content_hash(source.title, None, raw_text, None) if raw_text else None
    )
    if (
        not created
        and source.raw_text == raw_text
        and source.url is None
        and source.operator_note is None
        and (raw_text is None or (source.content_hash == content_hash and source.status == SourceStatus.PROCESSED))
    ):
        return SurveySyncResult(status="UNCHANGED", source_id=source.id)

    existing_notes = list(
        (
            await db.execute(
                select(HospitalSourceEvidenceNote).where(
                    HospitalSourceEvidenceNote.source_asset_id == source.id
                )
            )
        )
        .scalars()
        .all()
    )
    if raw_text is None:
        # 실을 답이 모두 비었다. 본문 없는 행은 필수 자료가 아니므로 Essence 입력에서 빠진다.
        for note in existing_notes:
            await db.delete(note)
        source.raw_text = None
        source.content_hash = None
        source.status = SourceStatus.PENDING
        source.processed_at = None
        source.url = None
        source.operator_note = None
        source.updated_by = DOCTOR_SURVEY_ACTOR
        source.source_metadata = _metadata(None, excluded)
        await db.flush()
        return SurveySyncResult(status="CLEARED", source_id=source.id, notes_removed=len(existing_notes))

    payloads = survey_note_payloads(raw_text)
    # 같은 답(예: '10분 이내')이 진료마다 나올 수 있어 라벨을 담은 claim으로 노트를 가른다.
    wanted = {(payload.note_type.value, payload.claim): payload for payload in payloads}
    kept: set[tuple[str, str]] = set()
    removed = 0
    for note in existing_notes:
        note_type = getattr(note.note_type, "value", note.note_type)
        key = (note_type, note.claim)
        payload = wanted.get(key)
        if payload is None or key in kept or payload.source_excerpt != note.source_excerpt:
            await db.delete(note)
            removed += 1
            continue
        kept.add(key)
        note.excerpt_start = payload.excerpt_start
        note.excerpt_end = payload.excerpt_end
        note.note_metadata = payload.note_metadata
        note.confidence = 1.0
    added = 0
    for key, payload in wanted.items():
        if key in kept:
            continue
        db.add(
            HospitalSourceEvidenceNote(
                hospital_id=hospital_id,
                source_asset_id=source.id,
                note_type=payload.note_type,
                claim=payload.claim,
                source_excerpt=payload.source_excerpt,
                excerpt_start=payload.excerpt_start,
                excerpt_end=payload.excerpt_end,
                confidence=1.0,
                note_metadata=payload.note_metadata,
            )
        )
        added += 1

    source.raw_text = raw_text
    source.url = None
    source.operator_note = None
    source.content_hash = content_hash
    source.status = SourceStatus.PROCESSED
    source.process_error = None
    source.processed_at = now
    source.updated_by = DOCTOR_SURVEY_ACTOR
    source.source_metadata = _metadata(raw_text, excluded)
    # 워커가 이 행을 다시 집어도(일괄 재처리) 같은 입력이면 건너뛰게 한다.
    source.source_metadata = {
        **source.source_metadata,
        "extraction_input_hash": processing_input_hash(source, content_hash),
    }
    await db.flush()
    return SurveySyncResult(
        status="CREATED" if created else "UPDATED",
        source_id=source.id,
        notes_added=added,
        notes_removed=removed,
    )


__all__ = (
    "DOCTOR_SURVEY_ACTOR",
    "DOCTOR_SURVEY_READ_ONLY_CODE",
    "DOCTOR_SURVEY_READ_ONLY_MESSAGE",
    "DOCTOR_SURVEY_SYSTEM_KIND",
    "DOCTOR_SURVEY_TITLE",
    "SurveyLine",
    "SurveyNote",
    "SurveySyncResult",
    "existing_survey_source",
    "is_doctor_survey_source",
    "render_interview_text",
    "survey_lines",
    "survey_note_payloads",
    "sync_doctor_survey_source",
)
