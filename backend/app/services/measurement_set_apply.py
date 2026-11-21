"""측정 키워드 30개 세트를 병원 질문 행에 적용한다 (설계 §6).

초안 적용(CLI)과 어드민 저장(PUT)이 같은 함수를 쓴다.

- 같은 문구의 질문 행이 있으면 다시 쓴다 — 측정 이력·노출 격차·글 주제 연결이 이어진다.
- 세트 질문(고정·교체)은 ACTIVE, 교체 후보는 새로 만들 때 PAUSED(측정도 글 주제도 아니다).
- 세트에서 빠진 질문은 그룹·역할만 지운다. 시스템이 만든 후보가 빠지면 보관한다. 옛 15개 질문은
  지우지 않는다(글 주제로 계속 쓰인다) — 측정에서만 빠진다.
- `in_tracking_set`은 역할에 맞춘다(30개 방식 판정은 `measurement_set.uses_curated_set`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import arrow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.hospital import Hospital
from app.models.sov import AIQueryTarget, AIQueryVariant
from app.services import measurement_set
from app.services.measurement_question_set import (
    PROBLEM_LABELS,
    DraftQuestion,
    clinic_name_for,
    draft_measurement_set,
    measurement_question_problems,
)
from app.services.query_target_structure import describe_query_text

DRAFT_ACTOR = "system:measure-30-draft"
# 세트 저장·초안 적용이 새로 만든 질문 행의 created_by. 세트에서 빠지면 보관한다(측정만을 위해 만든 질문이다).
SET_CREATOR = "system:measure-30-set"
PLATFORMS = ("CHATGPT", "GEMINI")
MAX_TEXT_LENGTH = 200


class MeasurementSetError(ValueError):
    """저장할 수 없는 세트. `problems`는 질문별·세트 단위 사유다."""

    def __init__(self, problems: list[dict[str, Any]]):
        super().__init__("measurement set rejected")
        self.problems = problems


@dataclass(frozen=True, slots=True)
class SetRow:
    text: str
    group: str
    role: str | None  # FIXED / ROTATING / None(교체 후보)


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


def draft_rows(hospital: Hospital) -> list[DraftQuestion]:
    return draft_measurement_set(
        hospital_name=str(hospital.name or ""),
        region=list(hospital.region or []),
        specialties=list(hospital.specialties or []),
        keywords=list(hospital.keywords or []),
        treatments=list(getattr(hospital, "treatments", None) or []),
    )


def validate_rows(hospital: Hospital, rows: Sequence[SetRow]) -> list[dict[str, Any]]:
    """질문별 문형(지침 3-1)과 세트 모양 검사. 비어 있으면 저장할 수 있다."""

    clinic = clinic_name_for(list(hospital.specialties or []))
    problems: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        text = _clean(row.text)
        codes: list[str] = []
        if row.group not in measurement_set.QUESTION_GROUPS:
            codes.append("unknown_group")
        if row.role is not None and row.role not in measurement_set.SET_ROLES:
            codes.append("unknown_role")
        if not text or len(text) > MAX_TEXT_LENGTH:
            codes.append("length")
        if text in seen:
            codes.append("duplicate")
        seen.add(text)
        codes.extend(measurement_question_problems(text, clinic=clinic))
        if row.group == measurement_set.GROUP_BRAND and row.role == measurement_set.ROLE_ROTATING:
            codes.append("brand_rotating")
        if codes:
            problems.append(
                {
                    "index": index,
                    "text": text,
                    "codes": codes,
                    "messages": [PROBLEM_LABELS.get(code, _ROW_MESSAGES.get(code, code)) for code in codes],
                }
            )
    shape = measurement_set.set_problems(
        [_ShapeRow(row.group, row.role) for row in rows if row.role is not None]
    )
    for message in shape:
        problems.append({"index": None, "text": None, "codes": ["set_shape"], "messages": [message]})
    return problems


_ROW_MESSAGES = {
    "unknown_group": "그룹은 A~D 중 하나여야 합니다",
    "unknown_role": "역할은 고정·교체·후보 중 하나여야 합니다",
    "length": f"질문은 1~{MAX_TEXT_LENGTH}자여야 합니다",
    "duplicate": "같은 질문이 두 번 있습니다",
    "brand_rotating": "병원명 질문(A)은 고정으로만 둡니다",
}


@dataclass(frozen=True, slots=True)
class _ShapeRow:
    question_group: str
    set_role: str | None
    status: str = "ACTIVE"
    name: str = ""
    id: str = ""


async def load_targets(db: AsyncSession, hospital_id: uuid.UUID) -> list[AIQueryTarget]:
    return list(
        (
            await db.execute(
                select(AIQueryTarget)
                .options(selectinload(AIQueryTarget.variants))
                .where(AIQueryTarget.hospital_id == hospital_id)
                .order_by(AIQueryTarget.created_at, AIQueryTarget.id)
            )
        )
        .scalars()
        .all()
    )


def _ensure_variants(target: AIQueryTarget, text: str, db: AsyncSession) -> None:
    """두 플랫폼 질문(variant)을 이 문구로 켠다. 다른 문구의 variant는 끈다."""

    present = {
        (str(variant.platform).upper(), variant.query_text): variant
        for variant in (target.variants or [])
    }
    for variant in target.variants or []:
        if variant.query_text != text and variant.is_active:
            variant.is_active = False
    for platform in PLATFORMS:
        existing = present.get((platform, text))
        if existing is not None:
            existing.is_active = True
            continue
        variant = AIQueryVariant(
            query_text=text,
            platform=platform,
            language="ko",
            is_active=True,
        )
        target.variants.append(variant)
        db.add(variant)


def _new_target(hospital_id: uuid.UUID, text: str, *, actor: str) -> AIQueryTarget:
    structure = describe_query_text(text)
    return AIQueryTarget(
        hospital_id=hospital_id,
        name=text,
        target_intent=structure.target_intent,
        region_terms=list(structure.region_terms),
        specialty=structure.specialty,
        condition_or_symptom=structure.condition_or_symptom,
        treatment=structure.treatment,
        decision_criteria=[],
        platforms=list(PLATFORMS),
        competitor_names=[],
        priority="NORMAL",
        status="ACTIVE",
        target_month=arrow.now("Asia/Seoul").format("YYYY-MM"),
        created_by=SET_CREATOR,
        updated_by=actor,
        variants=[],
    )


async def apply_rows(
    db: AsyncSession,
    hospital: Hospital,
    rows: Sequence[SetRow],
    *,
    actor: str,
) -> dict[str, Any]:
    """세트를 질문 행에 적용한다(커밋은 호출자). 저장할 수 없으면 `MeasurementSetError`."""

    cleaned = [SetRow(_clean(row.text), row.group, row.role) for row in rows]
    problems = validate_rows(hospital, cleaned)
    if problems:
        raise MeasurementSetError(problems)

    targets = await load_targets(db, hospital.id)
    by_name = {target.name: target for target in targets}
    desired = {row.text for row in cleaned}
    created = reused = released = 0
    for row in cleaned:
        target = by_name.get(row.text)
        if target is None:
            target = _new_target(hospital.id, row.text, actor=actor)
            # 교체 후보는 측정도 글 주제도 아니다 — 고를 때 ACTIVE가 된다.
            target.status = "ACTIVE" if row.role is not None else "PAUSED"
            db.add(target)
            by_name[row.text] = target
            created += 1
        else:
            reused += 1
            if row.role is not None:
                target.status = "ACTIVE"
            elif (
                str(target.status).upper() == "ARCHIVED"
                or target.created_by in (SET_CREATOR, DRAFT_ACTOR)
                or row.group == measurement_set.GROUP_BRAND
            ):
                # 후보는 측정도 글 주제도 아니다 — 보관된 후보를 다시 올리거나 측정 질문을 후보로 내리면
                # PAUSED다. 옛 질문(V0 시드 등)을 후보로 두면 글 주제로는 계속 쓰이게 그대로 둔다.
                target.status = "PAUSED"
            target.updated_by = actor
        target.question_group = row.group
        target.set_role = row.role
        target.in_tracking_set = row.role is not None
        _ensure_variants(target, row.text, db)
    for target in targets:
        if target.name in desired:
            continue
        if target.question_group is None and target.set_role is None and not target.in_tracking_set:
            continue
        was_brand = target.question_group == measurement_set.GROUP_BRAND
        target.set_role = None
        target.in_tracking_set = False
        target.updated_by = actor
        if target.created_by in (SET_CREATOR, DRAFT_ACTOR):
            # 측정만을 위해 만든 질문 — 보관한다(그룹은 남겨 둔다).
            target.status = "ARCHIVED"
        elif was_brand:
            # 병원명 질문은 측정에서 빠져도 글 주제가 아니다 — 그룹 A를 남기고 보관한다(후보 목록에 다시
            # 나타나지 않게, 글 주제 후보도 아니게).
            target.status = "ARCHIVED"
        else:
            # 옛 질문(V0 시드 등)은 측정에서만 빠지고 글 주제로 남는다.
            target.question_group = None
        released += 1
    await db.flush()
    members = [row for row in cleaned if row.role is not None]
    fingerprint, fixed_count = measurement_set.set_fingerprint(
        [
            _FingerprintRow(row.group, row.role, row.text)
            for row in members
        ]
    )
    return {
        "created": created,
        "reused": reused,
        "released": released,
        "fixed": fixed_count,
        "rotating": sum(row.role == measurement_set.ROLE_ROTATING for row in members),
        "candidates": sum(row.role is None for row in cleaned),
        "fingerprint": fingerprint,
    }


@dataclass(frozen=True, slots=True)
class _FingerprintRow:
    question_group: str
    set_role: str | None
    name: str
    status: str = "ACTIVE"
    variants: tuple = ()


def rows_from_targets(targets: Iterable[AIQueryTarget]) -> list[SetRow]:
    """지금 저장된 세트(역할 있는 질문 + 그룹만 있는 후보)."""

    rows: list[SetRow] = []
    for target in targets:
        group = measurement_set.target_question_group(target)
        if group is None or str(target.status).upper() == "ARCHIVED":
            continue
        role = measurement_set.target_set_role(target)
        if role is not None and str(target.status).upper() != "ACTIVE":
            role = None
        rows.append(SetRow(text=target.name, group=group, role=role))
    return rows
