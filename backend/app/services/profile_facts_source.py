"""PROFILE_FACTS 자료 — 병원 정보의 서술 값을 Essence 근거 자료 한 행으로 싣는다(Phase 4 D33·E2).

마케터가 입력한 값과 자동 입력이 채운 값(원장명·약력·진료 철학·전문 과목·진료 항목·지역·핵심
키워드)을 줄 단위로 렌더해 병원마다 시스템 소유 자료 1행(`SourceType.INTERNAL_NOTE` +
``source_metadata.system_kind="PROFILE_FACTS"`` + ``created_by="system:profile-facts"``)에 둔다.
새 enum 값을 만들지 않는 이유는 롤백한 옛 코드가 그 행을 읽다 깨지지 않게 하기 위해서다(E2).

- 줄마다 출처 태그(`[마케터 입력]`/`[자동 수집·네이버]`/…)를 출처 원장(`profile_provenance`)에서 붙인다.
- 주소·전화·진료시간 같은 운영 사실은 싣지 않는다(E5) — 생성은 그 값을 병원 행에서 읽는다.
- 처리는 **결정적**이다. 줄 하나 → 근거 노트 하나, LLM 호출 없음. 원문(raw_text)만으로 노트가
  정해지므로 워커가 다시 처리해도 같은 노트가 나온다(`essence_engine.process_source_asset` 분기).
- 프로필 저장·자동 적용은 커밋 뒤 자동 요약 태스크를 부르고, 태스크가 병원 잠금 아래서 이 자료를
  맞춘 뒤 다시 합성할지 판단한다. 15분 정기 조정도 모든 병원을 맞춘다(저장 훅이 놓친 병원 회수).
- 동기화는 바뀐 줄의 노트만 바꾼다. 그대로인 줄의 노트 id는 유지되어 그 노트를 인용한 판·글의
  근거가 끊기지 않는다.
- Admin 자료 API에서 읽기 전용이다(수정·제외·재처리 409).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.essence import (
    EvidenceNoteType,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital
from app.services import profile_provenance
from app.utils.error_page import looks_like_error_page_text
from app.utils.medical_filter import check_forbidden
from app.utils.prompt_injection import has_prompt_injection

logger = logging.getLogger(__name__)

PROFILE_FACTS_SYSTEM_KIND = "PROFILE_FACTS"
PROFILE_FACTS_ACTOR = "system:profile-facts"
PROFILE_FACTS_TITLE = "병원 정보(자동)"
PROFILE_FACTS_READ_ONLY_CODE = "PROFILE_FACTS_READ_ONLY"
PROFILE_FACTS_READ_ONLY_MESSAGE = (
    "병원 정보(자동) 자료는 병원 정보 화면의 값으로 자동 갱신됩니다. 병원 정보 탭에서 값을 고쳐 주세요."
)

#: 출처 원장의 자동 입력 출처 → 태그.
_AUTOFILL_TAGS = {
    "naver": "자동 수집·네이버",
    "homepage": "자동 수집·홈페이지",
    "blog": "자동 수집·블로그",
    "survey": "자동 수집·설문",
}
TAG_OPERATOR = "마케터 입력"
TAG_UNKNOWN = "병원 정보"

#: 줄 라벨 → (병원 필드, 노트 종류). 순서가 곧 렌더 순서다.
LABEL_NAME = "병원명"
LABEL_DIRECTOR = "원장"
LABEL_CAREER = "원장 약력"
LABEL_PHILOSOPHY = "진료 철학"
LABEL_SPECIALTIES = "전문 과목"
LABEL_TREATMENT = "진료 항목"
LABEL_REGION = "지역"
LABEL_KEYWORDS = "핵심 키워드"

_LABEL_NOTE_TYPES: dict[str, EvidenceNoteType] = {
    LABEL_NAME: EvidenceNoteType.KEY_MESSAGE,
    LABEL_DIRECTOR: EvidenceNoteType.KEY_MESSAGE,
    LABEL_CAREER: EvidenceNoteType.PROOF_POINT,
    LABEL_PHILOSOPHY: EvidenceNoteType.DOCTOR_PHILOSOPHY,
    LABEL_SPECIALTIES: EvidenceNoteType.KEY_MESSAGE,
    LABEL_TREATMENT: EvidenceNoteType.TREATMENT_SIGNAL,
    LABEL_REGION: EvidenceNoteType.LOCAL_CONTEXT,
    LABEL_KEYWORDS: EvidenceNoteType.KEY_MESSAGE,
}
#: 이 자료에 실리는 병원 필드(PATCH의 `physicians`는 원장 필드로 파생된다). 이 중 하나라도 바뀐
#: 저장은 자동 요약 태스크를 부른다 — 태스크가 잠금 아래서 자료를 맞춘다.
PROFILE_FACT_FIELDS = frozenset(
    {
        "name",
        "director_name",
        "director_career",
        "director_philosophy",
        "specialties",
        "treatments",
        "region",
        "keywords",
        "physicians",
    }
)
_LINE_PATTERN = re.compile(r"^\[(?P<tag>[^\]]+)\] (?P<label>[^:]+): (?P<value>.+)$")
_TREATMENT_SEPARATOR = " — "
_MAX_VALUE_CHARS = 600


@dataclass(frozen=True, slots=True)
class FactLine:
    label: str
    tag: str
    value: str

    def render(self) -> str:
        return f"[{self.tag}] {self.label}: {self.value}"


@dataclass(frozen=True, slots=True)
class ProfileFactNote:
    note_type: EvidenceNoteType
    claim: str
    source_excerpt: str
    excerpt_start: int
    excerpt_end: int
    note_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SyncResult:
    status: str  # CREATED | UPDATED | UNCHANGED | EMPTY | CLEARED | NOT_FOUND
    source_id: uuid.UUID | None = None
    notes_added: int = 0
    notes_removed: int = 0

    @property
    def changed(self) -> bool:
        return self.status in {"CREATED", "UPDATED", "CLEARED"}


def touches_profile_facts(fields: Iterable[str]) -> bool:
    return any(field in PROFILE_FACT_FIELDS for field in fields)


def is_profile_facts_source(source: Any) -> bool:
    if source is None:
        return False
    source_type = getattr(source, "source_type", None)
    source_type = getattr(source_type, "value", source_type)
    if source_type != SourceType.INTERNAL_NOTE.value:
        return False
    metadata = getattr(source, "source_metadata", None)
    return isinstance(metadata, dict) and metadata.get("system_kind") == PROFILE_FACTS_SYSTEM_KIND


def _one_line(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:_MAX_VALUE_CHARS].rstrip()


def _tag(hospital: Any, field: str) -> str:
    entry = profile_provenance.ledger(hospital).get(field) or {}
    origin = entry.get("origin")
    if origin == profile_provenance.ORIGIN_OPERATOR:
        return TAG_OPERATOR
    if origin == profile_provenance.ORIGIN_AUTOFILL:
        return _AUTOFILL_TAGS.get(str(entry.get("source") or ""), TAG_UNKNOWN)
    return TAG_UNKNOWN


def _joined(values: Iterable[Any]) -> str:
    return ", ".join(item for item in (_one_line(value) for value in values or []) if item)


def _poisoned_reason(value: str) -> str | None:
    if has_prompt_injection(value):
        return "PROMPT_INJECTION"
    if looks_like_error_page_text(value):
        return "ERROR_PAGE"
    return None


def render_profile_fact_lines(
    hospital: Any, *, excluded: list[dict[str, str]] | None = None
) -> list[FactLine]:
    """병원 행의 서술 값을 줄로 만든다. 운영 사실(주소·전화·진료시간)은 넣지 않는다(E5).

    프롬프트 주입 의심·차단 페이지 잔재가 든 값은 그 줄만 뺀다(`excluded`에 라벨과 사유를
    남긴다). 한 칸의 오염이 나머지 안전한 병원 정보까지 입력에서 빼지 않게 한다.
    """

    lines: list[FactLine] = []

    def add(label: str, field: str, value: str) -> None:
        if not value:
            return
        reason = _poisoned_reason(value)
        if reason:
            if excluded is not None:
                excluded.append({"label": label, "field": field, "reason": reason})
            return
        lines.append(FactLine(label=label, tag=_tag(hospital, field), value=value))

    # 병원명은 언제나 있다 — 다른 자료가 모두 빠져도 최소 자동 판의 근거가 된다.
    add(LABEL_NAME, "name", _one_line(getattr(hospital, "name", None)))
    add(LABEL_DIRECTOR, "director_name", _one_line(getattr(hospital, "director_name", None)))
    add(LABEL_CAREER, "director_career", _one_line(getattr(hospital, "director_career", None)))
    add(
        LABEL_PHILOSOPHY,
        "director_philosophy",
        _one_line(getattr(hospital, "director_philosophy", None)),
    )
    add(LABEL_SPECIALTIES, "specialties", _joined(getattr(hospital, "specialties", None) or []))
    for treatment in getattr(hospital, "treatments", None) or []:
        if isinstance(treatment, dict):
            name = _one_line(treatment.get("name"))
            description = _one_line(treatment.get("description"))
        else:
            name, description = _one_line(treatment), ""
        if not name:
            continue
        add(
            LABEL_TREATMENT,
            "treatments",
            f"{name}{_TREATMENT_SEPARATOR}{description}" if description else name,
        )
    add(LABEL_REGION, "region", _joined(getattr(hospital, "region", None) or []))
    add(LABEL_KEYWORDS, "keywords", _joined(getattr(hospital, "keywords", None) or []))
    # 같은 줄이 두 번 생기면(같은 진료 항목 두 번) 한 번만 둔다 — 노트 키가 겹치지 않게.
    return list(dict.fromkeys(lines))


def render_profile_facts_text(
    hospital: Any, *, excluded: list[dict[str, str]] | None = None
) -> str | None:
    lines = render_profile_fact_lines(hospital, excluded=excluded)
    if not lines:
        return None
    return "\n".join(line.render() for line in lines)


def profile_fact_note_payloads(raw_text: str | None) -> list[ProfileFactNote]:
    """원문 줄 → 근거 노트. 같은 원문이면 언제나 같은 노트다(결정적)."""

    notes: list[ProfileFactNote] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    for line in (raw_text or "").split("\n"):
        start = offset
        offset += len(line) + 1
        match = _LINE_PATTERN.match(line)
        if not match:
            continue
        label = match.group("label")
        value = match.group("value")
        note_type = _LABEL_NOTE_TYPES.get(label, EvidenceNoteType.KEY_MESSAGE)
        metadata: dict[str, Any] = {
            "system_kind": PROFILE_FACTS_SYSTEM_KIND,
            "label": label,
            "tag": match.group("tag"),
        }
        violations = check_forbidden(line)
        if violations:
            note_type = EvidenceNoteType.RISK_SIGNAL
            metadata["violations"] = violations
        elif note_type == EvidenceNoteType.TREATMENT_SIGNAL:
            metadata["treatment"] = value.split(_TREATMENT_SEPARATOR, 1)[0].strip()
        key = (note_type.value, line)
        if key in seen:
            continue
        seen.add(key)
        notes.append(
            ProfileFactNote(
                note_type=note_type,
                claim=f"{label}: {value}"[:500],
                source_excerpt=line,
                excerpt_start=start,
                excerpt_end=start + len(line),
                note_metadata=metadata,
            )
        )
    return notes


def existing_profile_facts_source(
    db: Session, hospital_id: uuid.UUID
) -> HospitalSourceAsset | None:
    rows = db.execute(
        select(HospitalSourceAsset)
        .where(
            HospitalSourceAsset.hospital_id == hospital_id,
            HospitalSourceAsset.source_type == SourceType.INTERNAL_NOTE,
            HospitalSourceAsset.created_by == PROFILE_FACTS_ACTOR,
        )
        .order_by(HospitalSourceAsset.created_at, HospitalSourceAsset.id)
    ).scalars()
    for row in rows:
        if is_profile_facts_source(row):
            return row
    return None


def _source_metadata(raw_text: str | None, _content_hash: str | None) -> dict[str, Any]:
    from app.services.essence_engine import source_processing_coverage

    metadata: dict[str, Any] = {
        "system_kind": PROFILE_FACTS_SYSTEM_KIND,
        "read_only": True,
    }
    if raw_text:
        metadata["extraction_coverage"] = source_processing_coverage(raw_text)
    return metadata


def sync_profile_facts_source(db: Session, hospital: Hospital | None) -> SyncResult:
    """병원 행의 서술 값으로 PROFILE_FACTS 자료와 노트를 맞춘다. 커밋은 호출자가 한다.

    내용이 같으면 아무것도 쓰지 않는다(UNCHANGED). 호출자는 병원 advisory 잠금을 들고 있어야
    한다(프로필 PATCH·자동 적용·15분 틱 모두 그 잠금 안에서 부른다).
    """

    from app.services.essence_engine import compute_source_content_hash
    from app.services.source_processing_runs import processing_input_hash

    if hospital is None:
        return SyncResult(status="NOT_FOUND")
    excluded_lines: list[dict[str, str]] = []
    raw_text = render_profile_facts_text(hospital, excluded=excluded_lines)
    source = existing_profile_facts_source(db, hospital.id)
    if source is None and raw_text is None:
        return SyncResult(status="EMPTY")

    now = datetime.now(UTC)
    if source is None:
        source = HospitalSourceAsset(
            id=uuid.uuid4(),
            hospital_id=hospital.id,
            source_type=SourceType.INTERNAL_NOTE,
            title=PROFILE_FACTS_TITLE,
            created_by=PROFILE_FACTS_ACTOR,
            updated_by=PROFILE_FACTS_ACTOR,
            source_metadata={"system_kind": PROFILE_FACTS_SYSTEM_KIND, "read_only": True},
            status=SourceStatus.PENDING,
        )
        db.add(source)
        db.flush()
        created = True
    else:
        created = False

    content_hash = (
        compute_source_content_hash(source.title, None, raw_text, None) if raw_text else None
    )
    if (
        not created
        and source.raw_text == raw_text
        and source.url is None
        and source.operator_note is None
        and (
            raw_text is None
            or (
                source.content_hash == content_hash
                and source.status == SourceStatus.PROCESSED
            )
        )
    ):
        return SyncResult(status="UNCHANGED", source_id=source.id)

    existing_notes = list(
        db.execute(
            select(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        .scalars()
        .all()
    )
    if raw_text is None:
        # 서술 값이 모두 비었다. 본문 없는 행은 필수 자료가 아니므로 Essence 입력에서 빠진다.
        for note in existing_notes:
            db.delete(note)
        source.raw_text = None
        source.content_hash = None
        source.status = SourceStatus.PENDING
        source.processed_at = None
        source.url = None
        source.operator_note = None
        source.updated_by = PROFILE_FACTS_ACTOR
        source.source_metadata = _source_metadata(None, None)
        db.flush()
        return SyncResult(
            status="CLEARED", source_id=source.id, notes_removed=len(existing_notes)
        )

    payloads = profile_fact_note_payloads(raw_text)
    wanted = {(payload.note_type.value, payload.source_excerpt): payload for payload in payloads}
    kept: set[tuple[str, str]] = set()
    removed = 0
    for note in existing_notes:
        note_type = getattr(note.note_type, "value", note.note_type)
        key = (note_type, note.source_excerpt)
        payload = wanted.get(key)
        if payload is None or key in kept:
            db.delete(note)
            removed += 1
            continue
        kept.add(key)
        note.claim = payload.claim
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
                hospital_id=hospital.id,
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
    source.updated_by = PROFILE_FACTS_ACTOR
    source.source_metadata = _source_metadata(raw_text, content_hash)
    if excluded_lines:
        source.source_metadata = {**source.source_metadata, "excluded_lines": excluded_lines}
    # 워커가 이 행을 다시 집어도(일괄 재처리) 같은 입력이면 건너뛰게 한다.
    source.source_metadata = {
        **source.source_metadata,
        "extraction_input_hash": processing_input_hash(source, content_hash),
    }
    db.flush()
    return SyncResult(
        status="CREATED" if created else "UPDATED",
        source_id=source.id,
        notes_added=added,
        notes_removed=removed,
    )


__all__ = (
    "PROFILE_FACTS_ACTOR",
    "PROFILE_FACT_FIELDS",
    "PROFILE_FACTS_READ_ONLY_CODE",
    "PROFILE_FACTS_READ_ONLY_MESSAGE",
    "PROFILE_FACTS_SYSTEM_KIND",
    "PROFILE_FACTS_TITLE",
    "FactLine",
    "ProfileFactNote",
    "SyncResult",
    "existing_profile_facts_source",
    "is_profile_facts_source",
    "profile_fact_note_payloads",
    "render_profile_fact_lines",
    "render_profile_facts_text",
    "sync_profile_facts_source",
    "touches_profile_facts",
)
