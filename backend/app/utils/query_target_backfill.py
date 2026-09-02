"""이미 만들어진 AIQueryTarget의 빈 구조 필드를 채운다.

V0 자동 시드는 오랫동안 `target_intent="증상 탐색"`, `region_terms=[]`,
`condition_or_symptom=None`, `treatment=None`으로만 target을 만들었다. 그 결과
콘텐츠 슬롯이 "어느 질문을 답할지" 고를 때 타깃 간 변별이 없었다.

새 시드는 문장에서 구조를 되찾아 채우지만(`query_target_structure`), 이미 저장된
행은 그대로다. 이 유틸은 그 행들을 같은 규칙으로 채운다. 재실행해도 안전하다 —
**빈 필드만** 채우고, AE가 Admin에서 손으로 넣은 값은 건드리지 않는다.

콘텐츠 계획 경로(`content_target_planner`)도 같은 함수를 지연 호출하므로, 이 스크립트를
돌리지 않아도 운영은 스스로 회복한다. 스크립트는 "지금 당장 전부 채우고 Admin에서
확인하고 싶을 때" 쓴다.
"""
from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SyncSessionLocal
from app.models.sov import AIQueryTarget
from app.services.query_target_structure import apply_structure_to_target

logger = logging.getLogger(__name__)


@dataclass
class QueryTargetBackfillResult:
    targets_total: int = 0
    targets_updated: int = 0


def backfill_targets(targets, *, force: bool = False) -> QueryTargetBackfillResult:
    """순수 루프 — 각 target을 제자리에서 수정한다. DB 세션 없이 단위 테스트 가능."""
    result = QueryTargetBackfillResult(targets_total=len(targets))
    for target in targets:
        if apply_structure_to_target(target, force=force):
            result.targets_updated += 1
    return result


def backfill_query_target_structure(
    db: Session,
    hospital_id: uuid.UUID | str | None = None,
    force: bool = False,
) -> QueryTargetBackfillResult:
    """AIQueryTarget 구조 필드 백필.

    - hospital_id=None → 전체 병원
    - force=False      → 비어 있는 필드만 채움(기본)
    - force=True       → 질의 문장에서 되찾은 값으로 덮어씀(수기 편집도 덮음 — 주의)
    """
    stmt = select(AIQueryTarget)
    if hospital_id is not None:
        stmt = stmt.where(AIQueryTarget.hospital_id == _coerce_uuid(hospital_id))
    targets = db.execute(stmt).scalars().all()

    result = backfill_targets(targets, force=force)
    if result.targets_updated:
        db.commit()
    return result


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill AIQueryTarget structured fields from the measured query text."
    )
    parser.add_argument("--hospital-id", help="Restrict to a single hospital UUID.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing values instead of filling only empty fields.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with SyncSessionLocal() as db:
        result = backfill_query_target_structure(
            db, hospital_id=args.hospital_id, force=args.force
        )

    logger.info(
        "query target structure backfill done: targets=%d updated=%d",
        result.targets_total,
        result.targets_updated,
    )


if __name__ == "__main__":
    _main()
