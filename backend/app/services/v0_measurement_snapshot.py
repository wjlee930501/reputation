"""Immutable V0 inputs and ownership checks across resumable worker chunks.

Saved provider inputs, not mutable current profile/query values, define a run.
The caller owns transactions; this module does not call external providers.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.hospital import Hospital
from app.models.monthly_control import MeasurementObservationSlot
from app.models.sov import MeasurementRun, QueryMatrix
from app.services import mention_identity, sov_engine


def v0_query_snapshot(queries: Iterable[QueryMatrix]) -> list[dict[str, str]]:
    """Freeze the exact questions a V0 run will send before provider calls start."""
    return [
        {
            "query_id": str(query.id),
            "query_text": query.query_text,
            "query_intent": query.query_intent,
        }
        for query in queries
    ]


def v0_judgment_context(hospital: Hospital) -> dict[str, Any]:
    """Freeze every hospital field that can change a saved answer's verdict."""
    region = str((hospital.region or [""])[0])
    return {
        "hospital_identity": str(hospital.id),
        "hospital_name": hospital.name,
        "region": region,
        "competitors": [str(name) for name in (hospital.competitors or [])],
        # 규칙 대조(`mention_identity`)는 주소·전화·지역어 전체로 판정한다 — 실행 도중 병원 정보가
        # 바뀌어도 같은 실행의 판정이 섞이지 않게 함께 고정한다.
        "identity": judgment_identity(hospital, hospital.name, region).material(),
    }


def judgment_identity(
    hospital: Hospital, hospital_name: str, region: str | None
) -> mention_identity.MentionIdentity:
    """측정 판정에 쓰는 병원 신원 — 이름·주소·전화·지역어."""
    terms = [str(term) for term in (getattr(hospital, "region", None) or []) if str(term or "").strip()]
    if region and region not in terms:
        terms.insert(0, region)
    return mention_identity.MentionIdentity.build(
        hospital_name,
        address=getattr(hospital, "address", None),
        phone=getattr(hospital, "phone", None),
        region_terms=terms,
    )


def parse_v0_judgment_context(value: object, *, hospital_id: uuid.UUID) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    identity = value.get("hospital_identity")
    hospital_name = value.get("hospital_name")
    region = value.get("region")
    competitors = value.get("competitors")
    if (
        identity != str(hospital_id)
        or not isinstance(hospital_name, str)
        or not hospital_name.strip()
        or not isinstance(region, str)
        or not isinstance(competitors, list)
        or any(not isinstance(name, str) for name in competitors)
    ):
        return None
    context: dict[str, Any] = {
        "hospital_identity": identity,
        "hospital_name": hospital_name,
        "region": region,
        "competitors": list(competitors),
    }
    # 2026-11-09 이전에 고정한 문맥에는 신원이 없다 — 그런 실행은 측정 기준 변경으로 막힌다.
    if mention_identity.MentionIdentity.from_material(value.get("identity")) is not None:
        context["identity"] = value["identity"]
    return context


def v0_platforms_from_run(run: MeasurementRun) -> list[str] | None:
    """Read the provider set frozen by `_start_measurement_run`."""
    config = run.config if isinstance(run.config, dict) else {}
    model_names = config.get("model_names")
    if not isinstance(model_names, dict):
        return None
    platforms = [name for name in ("chatgpt", "gemini") if name in model_names]
    if not platforms or set(model_names) != set(platforms):
        return None
    return platforms


def v0_resume_judgment_context(
    run: MeasurementRun,
    hospital: Hospital,
    slots: Sequence[MeasurementObservationSlot],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Load the frozen context, safely upgrading pre-snapshot in-flight runs."""
    config = run.config if isinstance(run.config, dict) else {}
    frozen = parse_v0_judgment_context(config.get("judgment_context"), hospital_id=hospital.id)
    if frozen is not None:
        return frozen

    # Rows created before judgment_context was introduced can be upgraded only
    # when every already-attempted judgment proves the current profile produces
    # its exact saved fingerprint. Otherwise mixing identities would corrupt one
    # MeasurementRun, so fail before another provider call.
    candidate = v0_judgment_context(hospital)
    for slot in slots:
        if slot.judgment_input_fingerprint is None:
            continue
        expected = sov_engine.judgment_input_fingerprint(
            hospital_identity=candidate["hospital_identity"],
            hospital_name=candidate["hospital_name"],
            response_text=slot.raw_response or "",
            region=candidate["region"],
            competitors=candidate["competitors"],
            policy=dict(protocol),
        )
        if slot.judgment_input_fingerprint != expected:
            raise RuntimeError("resumable V0 judgment context changed")
    run.config = {**config, "judgment_context": candidate}
    return candidate


def local_v0_query_texts(snapshot: object) -> dict[uuid.UUID, str] | None:
    """Parse an immutable V0 query snapshot; malformed lineage is unusable."""
    queries = _parse_query_snapshot(snapshot)
    if queries is None:
        return None
    return {
        query.query_id: query.text
        for query in queries
        if query.intent == sov_engine.QUERY_INTENT_LOCAL
    } or None


def v0_queries_from_snapshot(
    db: Session, snapshot: object, *, hospital_id: uuid.UUID
) -> list[QueryMatrix] | None:
    """Reload frozen provider inputs while verifying their live FK ownership."""
    queries = _parse_query_snapshot(snapshot)
    if queries is None:
        return None
    ordered_ids = [query.query_id for query in queries]
    live_rows = {
        row.id: row
        for row in db.execute(select(QueryMatrix).where(QueryMatrix.id.in_(ordered_ids))).scalars()
    }
    if set(live_rows) != set(ordered_ids) or any(
        row.hospital_id != hospital_id for row in live_rows.values()
    ):
        return None
    # Current rows prove ownership, not the question text sent by this lineage.
    return [
        QueryMatrix(
            id=query.query_id,
            hospital_id=hospital_id,
            query_text=query.text,
            query_intent=query.intent,
        )
        for query in queries
    ]


@dataclass(frozen=True, slots=True)
class _FrozenQuery:
    query_id: uuid.UUID
    text: str
    intent: str


def _parse_query_snapshot(snapshot: object) -> tuple[_FrozenQuery, ...] | None:
    """One validation contract for baseline lookup and resumed provider inputs."""
    if not isinstance(snapshot, list) or not snapshot:
        return None
    queries: list[_FrozenQuery] = []
    seen: set[uuid.UUID] = set()
    for raw in snapshot:
        if not isinstance(raw, dict):
            return None
        try:
            query_id = uuid.UUID(str(raw.get("query_id")))
        except (TypeError, ValueError):
            return None
        text, intent = raw.get("query_text"), raw.get("query_intent")
        if (
            query_id in seen
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(intent, str)
        ):
            return None
        seen.add(query_id)
        queries.append(_FrozenQuery(query_id, text, intent))
    return tuple(queries)
